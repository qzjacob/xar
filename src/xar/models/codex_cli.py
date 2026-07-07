"""OpenAI Codex CLI 订阅执行器 —— 经 `codex exec` 用 ChatGPT Plus/Pro 订阅跑单次补全。

与 litellm(HTTP、按 token)/ agent_sdk(Claude Max)并列的第三条执行路径。
ModelSpec.executor=="codex_cli" 的模型(codex-sub)走这里:Codex CLI 复用其 OAuth 登录
(~/.codex/auth.json = ChatGPT 订阅),**零 token 计费**——与 GLM/Claude-Max 同一"订阅内
用、订阅外分文不花"的纪律。

订阅强制:子进程剥掉 OPENAI_API_KEY / OPENAI_BASE_URL(否则 Codex 会落到按 token 的
API key),并显式 `-c preferred_auth_method="chatgpt"`。用独立子进程(非改 os.environ),
保证并发的 app 侧按 token 的 OpenAI 调用不受影响。

非 agent 运行:`--sandbox read-only` + `--cd <临时空目录>` + `--skip-git-repo-check`
+ `--ephemeral` —— 即便模型试图跑命令也只读、且在临时目录里,碰不到本仓库;`--output-last-
message` 稳态取最终答复(不解析 agent 事件流)。单次高价值任务,~数十秒/次,仅低量。

ToS 提示:ChatGPT 订阅面向交互式 Codex 使用,把它当 headless 研究后端属 off-label,故本
执行器 **默认关闭**(codex_enabled=False),需显式 arm;缺 CLI/登录 → available()=False,
llm 自动跳过、回退 GLM/DeepSeek(docker 容器即此情形)。
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from ..logging import get_logger

log = get_logger("xar.codex")

# 与 glm_worker/agentsdk 同源的额度/限流标记(ChatGPT 订阅有 5 小时窗/周额度)。
_QUOTA_MARKERS = ("rate limit", "ratelimit", "usage limit", "too many requests",
                  "quota", "exceeded your", "limit reached", "429")
# 剥掉会盖过订阅 OAuth 的 OpenAI auth 变量(任一残留都会让 Codex 落到按 token 的 API)。
_STRIP = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_ORGANIZATION")


class CodexCLIError(RuntimeError):
    pass


def _auth_present() -> bool:
    home = os.environ.get("CODEX_HOME") or "~/.codex"
    return Path(os.path.expanduser(f"{home}/auth.json")).exists()


@lru_cache(maxsize=1)
def _host_ready() -> bool:
    """`codex` CLI 在 PATH + 登录凭证在位 —— 进程内不变,缓存(避免每次 ops 页 / 每轮候选
    循环都做 PATH 扫描 + 文件 stat)。"""
    return bool(shutil.which("codex")) and _auth_present()


def available() -> bool:
    """启用旗标(可运行时切换,不缓存)+ 宿主就绪(缓存)。docker 容器里(无 CLI/登录)
    返回 False,llm 据此跳过、回退 GLM。"""
    from ..config import get_settings

    return bool(get_settings().codex_enabled) and _host_ready()


def is_quota_error(e: Exception) -> bool:
    """ChatGPT 订阅额度耗尽/限流(钉扎链下这类错误=等待,不落 token)。"""
    msg = str(e).lower()
    return any(m in msg for m in _QUOTA_MARKERS)


def _real_model(spec) -> str:
    """codex_cli 规格 id → 真实 Codex 模型 id。litellm_model 的 bare 名故意 = 规格 id
    (避开 PRICES 索引碰撞),故用 config(默认 gpt-5.5)。"""
    from ..config import get_settings

    return get_settings().codex_model or "gpt-5.5"


def complete(spec, *, system: str | None, prompt: str, max_tokens: int,
             want_strong: bool) -> tuple[str, SimpleNamespace]:
    """单次补全,返回 (text, usage)。usage 暴露 prompt/completion_tokens(估算,订阅 usd=0
    只用于遥测)。额度/限流/失败抛异常,由 llm 处理、回退下一候选。"""
    from ..config import get_settings

    s = get_settings()
    model = _real_model(spec)
    effort = s.codex_effort if want_strong else "low"
    # exec 无独立 --system:把 system 前置进 prompt。
    full = f"{system}\n\n{prompt}" if system else prompt
    codex_bin = shutil.which("codex") or "codex"
    env = {k: v for k, v in os.environ.items() if k not in _STRIP}
    # 临时空目录当工作根 + 只读沙箱 → 模型碰不到本仓库;--output-last-message 稳态取答复。
    with tempfile.TemporaryDirectory(prefix="xar-codex-") as td:
        out_file = os.path.join(td, "last.txt")
        cmd = [codex_bin, "exec", "--sandbox", "read-only", "--skip-git-repo-check",
               "--ephemeral", "--color", "never", "--cd", td,
               "-c", f"model_reasoning_effort={effort}",
               "-c", 'preferred_auth_method="chatgpt"',
               "--output-last-message", out_file, "-m", model, "-"]
        # Popen + 独立进程组:超时时 killpg 连同 Codex 派生的孙进程一起收掉,不留孤儿。
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=env, start_new_session=True)
        try:
            _out, stderr = proc.communicate(input=full, timeout=s.codex_timeout_s)
        except subprocess.TimeoutExpired as e:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.communicate()
            raise CodexCLIError(f"codex exec timeout after {s.codex_timeout_s}s") from e
        if proc.returncode != 0:
            raise CodexCLIError(f"codex exec rc={proc.returncode}: {(stderr or '')[-200:]}")
        try:
            text = Path(out_file).read_text(encoding="utf-8", errors="replace").strip()
        except OSError as e:
            raise CodexCLIError(f"codex exec no output file: {e}") from e
    if not text:
        raise CodexCLIError(f"codex exec empty completion: {(stderr or '')[-200:]}")
    usage = SimpleNamespace(prompt_tokens=len(full) // 4, completion_tokens=len(text) // 4)
    return text, usage
