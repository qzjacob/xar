"""Claude Max 订阅执行器 —— 经 Claude Agent SDK 用 Max 订阅计划跑单次补全。

与 litellm(HTTP、按 token 计费)并列的第二条执行路径。ModelSpec.executor=="agent_sdk"
的模型(claude-opus-max / claude-sonnet-max)走这里:Agent SDK 复用 Claude Code 的
OAuth 凭证(~/.claude/.credentials.json = Max 订阅登录),**零 token 计费**——与 GLM
订阅池同一"订阅内白嫖、订阅外分文不花"的纪律。

订阅强制:子进程剥掉 ANTHROPIC_API_KEY(否则会落到按 token 计费的 key)。用独立子进程
而非改 os.environ,保证并发的 app 侧按 token 的 Anthropic 调用不受影响。

单次补全(allowed_tools=[], max_turns=1)—— 不是 agent loop。~6.5s/次,仅低量高价值
任务。仅在有 `claude` CLI + 凭证的宿主机可用;缺任一 → available()=False,llm 自动跳过、
回退 GLM/DeepSeek(docker 容器即此情形)。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from ..logging import get_logger

log = get_logger("xar.agentsdk")

# 与 glm_worker.is_quota_error 同源的额度/限流标记(Max 订阅有 5 小时窗/周额度)。
_QUOTA_MARKERS = ("rate limit", "ratelimit", "usage limit", "too many requests",
                  "quota", "overloaded", "exceeded your", "429")


class AgentSDKError(RuntimeError):
    pass


def _creds_present() -> bool:
    # Claude Code 的登录凭证(Max 订阅)。任一常见位置存在即可。
    for p in ("~/.claude/.credentials.json", "~/.config/anthropic/credentials"):
        if Path(os.path.expanduser(p)).exists():
            return True
    return False


@lru_cache(maxsize=1)
def _sdk_importable() -> bool:
    try:
        import claude_agent_sdk  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def available() -> bool:
    """启用旗标 + Agent SDK 可导入 + `claude` CLI 在 PATH + Max 凭证在位。
    docker 容器里(无 CLI/凭证)返回 False,llm 据此跳过、回退 GLM。"""
    from ..config import get_settings

    if not get_settings().anthropic_max_enabled:
        return False
    return _sdk_importable() and bool(shutil.which("claude")) and _creds_present()


def is_quota_error(e: Exception) -> bool:
    """Max 订阅额度耗尽/限流(与 glm_worker 同形:钉扎链下这类错误=等待,不落 token)。"""
    msg = str(e).lower()
    return any(m in msg for m in _QUOTA_MARKERS)


def _real_model(spec) -> str:
    """agent_sdk 规格的 litellm_model 形如 'anthropic-max/claude-opus-4-8' →
    取 '/' 之后的真实 Anthropic 模型 id;并允许 config 覆盖 opus 默认。"""
    mid = spec.litellm_model.split("/")[-1]
    if spec.id == "claude-opus-max":
        from ..config import get_settings

        return get_settings().anthropic_max_model or mid
    return mid


def complete(spec, *, system: str | None, prompt: str, max_tokens: int,
             want_strong: bool) -> tuple[str, SimpleNamespace]:
    """单次补全,返回 (text, usage)。usage 暴露 prompt_tokens/completion_tokens 供
    llm._record 记账(记为 subscription,usd=0)。额度/限流/失败抛异常,由 llm 处理。"""
    from ..config import get_settings

    s = get_settings()
    req = {"model": _real_model(spec), "system": system, "prompt": prompt,
           "effort": s.anthropic_max_effort if want_strong else "low", "max_turns": 1}
    # 子进程环境:剥掉 ANTHROPIC_API_KEY(强制订阅);保留其余(HOME/PATH → 凭证解析)。
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    # 关键:按文件路径调用 worker(而非 `-m xar.models…`)—— 后者会导入 xar 包,其配置层
    # 会把 .env 里的 ANTHROPIC_API_KEY 重新灌回 env,盖过订阅登录。worker 本身零 xar 依赖。
    worker = str(Path(__file__).with_name("_agentsdk_worker.py"))
    try:
        p = subprocess.run(
            [sys.executable, worker],
            input=json.dumps(req), capture_output=True, text=True,
            env=env, timeout=s.anthropic_max_timeout_s)
    except subprocess.TimeoutExpired as e:
        raise AgentSDKError(f"agent sdk timeout after {s.anthropic_max_timeout_s}s") from e
    if p.returncode != 0:
        raise AgentSDKError(f"agent sdk worker rc={p.returncode}: {p.stderr[-200:]}")
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception as e:  # noqa: BLE001
        raise AgentSDKError(f"agent sdk bad output: {p.stdout[-200:]}") from e
    if not out.get("ok"):
        raise AgentSDKError(out.get("error") or "agent sdk failed")
    text = out.get("text") or ""
    if not text.strip():
        raise AgentSDKError(f"agent sdk empty completion (subtype={out.get('subtype')})")
    u = out.get("usage") or {}
    usage = SimpleNamespace(
        prompt_tokens=int(u.get("input_tokens", 0) or 0),
        completion_tokens=int(u.get("output_tokens", 0) or 0))
    return text, usage
