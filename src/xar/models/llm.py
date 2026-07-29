"""LLM access via LiteLLM — task-routed, with cross-provider fallback, a
billing-aware budget, and resilient structured output.

`complete()` / `complete_json()` resolve a TaskClass (or the legacy `tier=`) to an
ordered candidate chain via `router.resolve` over the code-as-truth `registry`, then
execute with fallback: each candidate is tried (with one in-candidate retry on a
transient error), and on failure / empty / over-budget the next candidate is used.

Pricing is billing-aware: per-token models record their real USD and honor the hard
budget cap; subscription (flat-plan) models record `usd=0`, so bulk/search routed to a
subscription plan never trips `BudgetExceeded`. Every call is logged to `llm_usage` with
its provider / task_class / billing for audit.

Default routing = DeepSeek V4 (token); GLM/Kimi (subscription) carry bulk/search. Any
LiteLLM-supported model works — edit `registry.MODELS`."""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Type, TypeVar

import litellm
from pydantic import BaseModel

from ..config import get_settings
from ..logging import get_logger
from ..storage import db
from . import registry, router
from .registry import Billing, Capability

log = get_logger("xar.llm")
litellm.drop_params = True  # silently drop params a provider doesn't accept

# Price table ($/1M tokens) — derived from the registry (so a new model added there is
# priced automatically) plus a couple of legacy ids the registry no longer lists.
_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (5.0, 25.0),
    "claude-fable-5": (10.0, 50.0),
    **registry.PRICES,
}

T = TypeVar("T", bound=BaseModel)


class BudgetExceeded(RuntimeError):
    pass


class StructuredOutputError(RuntimeError):
    """模型**没能产出可解析的结构化输出**(与「供应商调用失败」是两回事)。

    默认路径下 `complete_json` 用空 `schema()` 兜底,这对可选字段全带默认值的模型是灾难性的:
    一个全默认的 CriticVote 构造得干干净净,于是「解析失败」在辩论痕迹里伪装成一张真实的
    abstain 票 —— 全体 critic 崩掉会被判成「一致同意」。`on_fail="raise"` 让调用方拿到这个
    异常,把「模型没答」与「模型弃权」分开。"""


# Batch jobs (build_kg / expert.process / synthesize_all / thesis / phanny …) attribute spend to
# a run_id with one of these prefixes so the (larger) batch budget cap actually bounds them.
# 漏一个前缀 = 那条批量道悄悄掉回 per-run 小帽(subpool 的 "thesis-"、flow_extract 的 "flow-" 曾如此)。
_BATCH_PREFIXES = ("kg", "expert", "synth", "batch", "thesis", "flow", "phanny", "earn")


def new_batch_run_id(prefix: str = "batch") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _budget_cap(run_id: str | None, s) -> float:
    if run_id and run_id.split("-", 1)[0] in _BATCH_PREFIXES:
        return s.llm_max_usd_per_batch
    return s.llm_max_usd_per_run


_KEYS_SYNCED = False


def _ensure_keys() -> None:
    """LiteLLM reads provider keys from os.environ; mirror them from Settings (.env)."""
    global _KEYS_SYNCED
    if _KEYS_SYNCED:
        return
    s = get_settings()
    # 镜像 Settings(.env)→os.environ,变量名必须与 registry Provider 的 key_env/sub_key_env 一致
    # (否则 host 侧 _endpoint/model_usable 读不到 → 静默「未配置」跳过候选)。moonshot provider 的
    # key_env 已在 P6 改为 KIMI_API_KEY;minimax 的主/副 key 此前完全缺镜像。
    for var, val in {
        "ANTHROPIC_API_KEY": s.anthropic_api_key,
        "OPENAI_API_KEY": s.openai_api_key,
        "DEEPSEEK_API_KEY": s.deepseek_api_key,
        "GLM_API_KEY": s.glm_api_key,
        "KIMI_API_KEY": s.moonshot_api_key,          # moonshot provider key_env(P6);_endpoint 读此名
        "MINIMAX_API_KEY": s.minimax_api_key,         # minimax provider key_env
        "GLM_SUB_API_KEY": s.glm_sub_api_key,
        "MOONSHOT_SUB_API_KEY": s.moonshot_sub_api_key,
        "MINIMAX_SUB_API_KEY": s.minimax_sub_api_key,  # minimax provider sub_key_env
        "OLLAMA_API_KEY": s.ollama_api_key,
    }.items():
        if val and not os.environ.get(var):
            os.environ[var] = val
    _KEYS_SYNCED = True


def _provider_model(model: str) -> str:
    """Prefix a bare model id with its LiteLLM provider. Already-prefixed ids pass through."""
    if "/" in model:
        return model
    if model.startswith("claude-"):
        return f"anthropic/{model}"
    if model.startswith("deepseek"):
        return f"deepseek/{model}"
    return model


def _price(model: str, in_tok: int, out_tok: int) -> float:
    pin, pout = _PRICES.get(model, _PRICES.get(model.split("/")[-1], (3.0, 15.0)))
    return (in_tok * pin + out_tok * pout) / 1_000_000


def _spent(run_id: str | None) -> float:
    if not run_id:
        return 0.0
    rows = db.query("SELECT COALESCE(SUM(usd),0) AS s FROM llm_usage WHERE run_id=%s", (run_id,))
    return float(rows[0]["s"]) if rows else 0.0


# ── 模型钉扎(subscription-only 工作负载的机制保证)────────────────────────────
# pinned() 把上下文内的所有 LLM 调用限制为指定 registry 模型(按序)。钉扎链之外
# 没有回退:额度耗尽 = 调用失败(由调用方决定等待),而不是悄悄落到按 token 计费的
# 模型。GLM 常驻工人(orchestration/glm_worker.py)的成本承诺依赖这一点。
_PIN: contextvars.ContextVar[tuple[str, ...] | None] = contextvars.ContextVar(
    "xar_llm_pin", default=None)


# 便捷钉扎链:强制某高价值流程走 Claude Max(Opus),订阅/宿主不可用则优雅降级到 GLM。
# 用法:with llm.pinned(llm.CLAUDE_MAX_PIN): report/thesis 综合...  —— 只有该上下文内
# 才用 Claude Max;默认路由仍是 peer/fallback(不改全局默认)。
CLAUDE_MAX_PIN: tuple[str, ...] = ("claude-opus-max", "glm-5.2-sub", "glm-4.6-sub")
# 同理钉扎 ChatGPT/Codex 订阅(深度研究候选);订阅/宿主不可用则降级 Claude-Max → GLM。
CODEX_PIN: tuple[str, ...] = ("codex-sub", "claude-opus-max", "glm-5.2-sub")
# Fenny 客户叙述(市场解读 / 票据面向非专业客户的措辞):Claude Opus 领衔 → Codex(gpt-5.5)
# → GLM-5.2 → DeepSeek 依次回退。宿主/订阅不可用的头部候选(opus-max/codex-sub)在 complete()
# 里被跳过、链条优雅轮转到 GLM/DeepSeek(docker 默认即如此)。措辞质量优先,故 opus 领衔。
FENNY_NARRATIVE_PIN: tuple[str, ...] = (
    "claude-opus-max", "codex-sub", "glm-5.2-sub", "deepseek-v4-pro")


@contextmanager
def pinned(model_ids: Sequence[str]):
    token = _PIN.set(tuple(model_ids))
    try:
        yield
    finally:
        _PIN.reset(token)


def _apply_pin(chain: list) -> list:
    pin = _PIN.get()
    if not pin:
        return chain
    return [spec for spec in (registry.get(mid) for mid in pin) if spec is not None]


def _executor_module(name: str):
    """Non-litellm executor name → module (subprocess-on-subscription paths). None for litellm."""
    if name == "agent_sdk":
        from . import agentsdk

        return agentsdk
    if name == "codex_cli":
        from . import codex_cli

        return codex_cli
    return None


def _record(run_id, node, spec, usage, task_class: str, used_sub: bool, *,
            status: str = "ok", error: str | None = None, latency_ms: int | None = None,
            attempt: int | None = None, requested: dict | None = None,
            context: dict | None = None, prompt_sha: str | None = None,
            tokens_estimated: bool = False) -> None:
    """记一次 LLM 调用。**成功与失败都记** —— 只记成功时,轮转/重试/返空全部不可见,
    「这次为什么换了模型」「哪家在抖」「实际用了什么参数」都无从查起。
    失败行 usd=0/tokens=0,故任何既有花费聚合(ops 面板)口径不变。"""
    in_tok = getattr(usage, "prompt_tokens", 0) or 0
    out_tok = getattr(usage, "completion_tokens", 0) or 0
    # EFFECTIVE billing, not the spec's nominal billing: a SUBSCRIPTION model that fell back
    # to the provider's metered key (no sub key configured) is really billing per token, so
    # record its real cost — otherwise that spend is invisible to the budget cap. usd=0 only
    # when the flat subscription endpoint was actually used.
    billing = "subscription" if used_sub else "token"
    usd = 0.0 if (used_sub or status != "ok") else _price(spec.litellm_model, in_tok, out_tok)
    try:
        db.execute(
            "INSERT INTO llm_usage(run_id,node,model,input_tokens,output_tokens,usd,"
            "provider,task_class,billing,status,error,latency_ms,attempt,requested,context,"
            "prompt_sha,tokens_estimated) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)",
            (run_id, node, spec.litellm_model, in_tok, out_tok, usd,
             spec.provider, task_class, billing, status, (error or None) and error[:500],
             latency_ms, attempt,
             json.dumps(requested, ensure_ascii=False, default=str) if requested else None,
             json.dumps(context, ensure_ascii=False, default=str) if context else None,
             prompt_sha, tokens_estimated),
        )
    except Exception as e:  # noqa: BLE001 — usage logging must never break a run(但要留声)
        log.warning("llm_usage record failed (%s/%s): %s", node, spec.id, str(e)[:120])


def _sha(system: str | None, prompt: str) -> str:
    """实际发出去的提示词指纹(system + user)。回放时重渲染再比对此值即可发现漂移。"""
    return hashlib.sha256(((system or "") + "\x00" + (prompt or "")).encode()).hexdigest()


class _NoUsage:
    """失败候选没有 usage 对象;给 0 值占位,失败行的 tokens/usd 恒为 0(花费聚合口径不变)。"""
    prompt_tokens = 0
    completion_tokens = 0


_NO_USAGE = _NoUsage()


def _retryable(e: Exception) -> bool:
    """Transient errors worth an in-candidate retry (vs. rotating immediately). Auth /
    bad-request / not-found, and the DETERMINISTIC errors below (an over-length prompt or a
    policy refusal won't change on an identical re-issue), are NOT transient — they rotate to
    the next candidate at once."""
    import litellm.exceptions as le
    names = ("RateLimitError", "Timeout", "APIConnectionError", "ServiceUnavailableError",
             "InternalServerError", "BadGatewayError")
    classes = tuple(getattr(le, n) for n in names if hasattr(le, n))
    return bool(classes) and isinstance(e, classes)


_SUB_BASE_ATTR = {"zhipu": "glm_sub_api_base", "moonshot": "moonshot_sub_api_base",
                  "minimax": "minimax_sub_api_base", "ollama": "ollama_api_base"}


def _endpoint(spec, s) -> tuple[str | None, str | None, bool]:
    """(api_base, api_key_env, used_sub) for a candidate. A subscription model uses its
    dedicated sub key/base when configured (used_sub=True → flat billing); else it falls back
    to the provider's standard metered key (used_sub=False → billed per token). None env means
    'not configured'."""
    prov = registry.PROVIDERS.get(spec.provider)
    if not prov:
        return None, None, False
    if spec.billing == Billing.SUBSCRIPTION:
        has_sub = bool(prov.sub_key_env and os.environ.get(prov.sub_key_env))
        key_env = prov.sub_key_env if has_sub else prov.key_env
        base = getattr(s, _SUB_BASE_ATTR.get(prov.id, ""), "") or prov.sub_api_base or prov.api_base
        return (base or None), key_env, has_sub
    return prov.api_base or None, prov.key_env, False


def _build_kwargs(spec, messages, max_tokens, want_strong, json_mode, s, base, key_env,
                  reasoning_effort=None) -> dict:
    out = max_tokens if not spec.max_output else min(max_tokens, spec.max_output)
    kwargs: dict = dict(model=spec.litellm_model, messages=messages, max_tokens=out)
    if spec.supports_reasoning:  # let strong tasks think; cap the general/bulk tier's thinking
        # 力度三级优先:①调用方显式(thesis 量产 low / phanny high)> ②强、推理层默认拉满
        # (s.model_effort="high")> ③非强层(bulk/triage)的低档上限(s.model_effort_bulk)——
        # 小 token 预算下思考会烧光 content,bulk 不能一刀切拉满。这就是「默认最大化、
        # 视任务需求而实际调用」的落点。
        kwargs["reasoning_effort"] = (reasoning_effort or
                                      (s.model_effort if want_strong else s.model_effort_bulk))
    if json_mode and spec.supports_json:
        kwargs["response_format"] = {"type": "json_object"}
    if base:                      # OpenAI-compatible / subscription endpoint, per candidate
        kwargs["api_base"] = base
    if key_env and os.environ.get(key_env):
        kwargs["api_key"] = os.environ[key_env]
    if spec.provider == "ollama":  # 本地端点:短超时防挂死(连接拒绝本就秒败→轮转云端)
        kwargs["timeout"] = s.llm_local_timeout_s
        if not spec.supports_reasoning:
            # ollama 对 thinking-capable 模型(如 Qwen3.5 renderer)默认开思考,/v1 端点
            # 把 reasoning 与 content 分离 → 4000 token 预算被思考耗尽即 content 空
            # ("empty completion" 轮转云端,赛马实测 30/40 空)。Modelfile 无法关思考
            # (ollama#10961/#14809),/v1 唯一开关是 reasoning_effort="none";非思考模型
            # (glm4 系/模板已预填空 think 的 qwen3 系)收到后忽略,实测无害。
            # 必须走 extra_body:顶层 reasoning_effort 的 "none" 不在 OpenAI SDK 枚举,
            # 会被 litellm.drop_params=True 静默丢弃(赛马重跑实测:直连 curl 生效、
            # litellm 顶层传参无效);extra_body 原样并入请求体绕过校验。
            kwargs["extra_body"] = {"reasoning_effort": "none"}
    return kwargs


def complete(
    prompt: str,
    *,
    system: str | None = None,
    tier: str = "fast",
    task: "router.TaskClass | str | None" = None,
    node: str = "?",
    run_id: str | None = None,
    max_tokens: int = 4000,
    json_mode: bool = False,
    complexity: str | None = None,
    relevance: str | None = None,
    reasoning_effort: str | None = None,
    context: dict | None = None,
) -> str:
    """Plain-text completion, task-routed with cross-provider fallback. `complexity`
    ("low"/"medium"/"high") and `relevance` ("high") drive dynamic layer selection; when
    unset, complexity is auto-derived from prompt size (router.route).

    `context` 是调用方的业务坐标({company_id, event_date, round, role}),原样写进
    `llm_usage.context` —— 没有它,一行花费无法归属到任何一家公司/一次裁决,只能按时间猜。"""
    _ensure_keys()
    s = get_settings()
    tc = router.as_task(task, tier)
    plan = router.route_plan(tc, complexity=complexity, relevance=relevance,
                             input_chars=len(prompt or "") + len(system or ""))
    chain = _apply_pin(plan.chain)
    if not chain:
        raise RuntimeError(f"no model candidates for task {tc.value}")
    # want_strong 取**调整后**能力(route_plan),与实际所选层一致:升 bulk→STRONG 时给足推理力度、
    # 降 STRONG→FAST 时不白烧(修 J.1.3:此前取静态 POLICIES[tc].capability,动态升/降层后错配)。
    want_strong = plan.capability in (Capability.STRONG, Capability.REASONING)
    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
    cap = _budget_cap(run_id, s)
    spent = _spent(run_id) if run_id else 0.0
    last_err: Exception | None = None
    psha = _sha(system, prompt)
    pin_now = _PIN.get()
    attempt = 0

    def _req(spec) -> dict:
        """本次实际请求参数(含 max_tokens 被 spec.max_output 钳制的事实)。
        `clamped=true` 就是「我们要 16000、模型只给 8192」—— 结构化输出被截断的直接证据。"""
        granted = max_tokens if not spec.max_output else min(max_tokens, spec.max_output)
        return {"task": tc.value, "max_tokens": max_tokens, "granted": granted,
                "clamped": granted < max_tokens, "want_strong": want_strong,
                "reasoning_effort": reasoning_effort, "pin": list(pin_now) if pin_now else None}

    # Fallback is resilience, not free insurance: during a partial provider outage a quality
    # task can rotate down its chain to a pricier model (e.g. deepseek-pro → sonnet → opus),
    # so a report costs more than usual. It is NOT a runaway — token spend still counts toward
    # the budget cap and over-cap token candidates are skipped below — but spend accelerates
    # while the preferred provider is down. (Subscription/flat-plan candidates record usd=0.)
    for spec in chain:
        # Subscription executors (Claude Max via Agent SDK, ChatGPT via Codex CLI): distinct
        # subprocess-on-subscription paths, never litellm/metered. Host-only → skip when
        # unavailable so the chain rotates to GLM/DeepSeek (e.g. in docker). Billing: usd=0.
        attempt += 1
        executor = getattr(spec, "executor", "litellm")
        mod = _executor_module(executor)
        if mod is not None:
            if not mod.available():
                last_err = RuntimeError(f"{spec.id}: {executor} unavailable (host-only)")
                continue
            t0 = time.monotonic()
            try:
                text, usage = mod.complete(spec, system=system, prompt=prompt,
                                           max_tokens=max_tokens, want_strong=want_strong)
            except Exception as e:  # noqa: BLE001 — quota/timeout/failure → rotate to next candidate
                last_err = e
                log.warning("llm %s %s %s failed: %s", node, executor, spec.id, str(e)[:160])
                _record(run_id, node, spec, _NO_USAGE, tc.value, used_sub=True, status="error",
                        error=f"{type(e).__name__}: {e}", attempt=attempt, context=context,
                        prompt_sha=psha, requested=_req(spec),
                        latency_ms=int((time.monotonic() - t0) * 1000))
                continue
            # 订阅执行器(codex/agent-sdk)的 token 数是 len//4 估算,不是计量值 —— 标出来,
            # 免得下游把它当真实用量做额度推算。
            _record(run_id, node, spec, usage, tc.value, used_sub=True, attempt=attempt,
                    context=context, prompt_sha=psha, requested=_req(spec), tokens_estimated=True,
                    latency_ms=int((time.monotonic() - t0) * 1000))
            log.info("route %s -> %s [subscription/%s]", tc.value, spec.id, executor)
            return text
        base, key_env, used_sub = _endpoint(spec, s)
        if key_env and not os.environ.get(key_env):   # skip unconfigured provider — no wasted call
            last_err = RuntimeError(f"{spec.id}: {key_env} not configured")
            continue
        # budget-aware skip by EFFECTIVE billing: a candidate that bills tokens (a token spec,
        # OR a subscription spec falling back to the metered key) yields to the next when over
        # cap; only a real flat-plan call (used_sub) never skips. Only token spend counts.
        if run_id and not used_sub and spent >= cap:
            last_err = BudgetExceeded(f"run {run_id} exceeded ${cap}")
            continue
        kwargs = _build_kwargs(spec, messages, max_tokens, want_strong, json_mode, s, base, key_env,
                               reasoning_effort)

        def _fail(e: Exception, t_start: float) -> None:
            _record(run_id, node, spec, _NO_USAGE, tc.value, used_sub, status="error",
                    error=f"{type(e).__name__}: {e}", attempt=attempt, context=context,
                    prompt_sha=psha, requested=_req(spec),
                    latency_ms=int((time.monotonic() - t_start) * 1000))

        t0 = time.monotonic()
        try:
            resp = litellm.completion(**kwargs)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if not _retryable(e):                     # auth / bad-request / deterministic → rotate now
                log.warning("llm %s candidate %s failed: %s", node, spec.id, e)
                _fail(e, t0)
                continue
            kwargs.pop("reasoning_effort", None)       # one in-candidate retry (some providers reject it)
            try:
                resp = litellm.completion(**kwargs)
            except Exception as e2:  # noqa: BLE001
                last_err = e2
                log.warning("llm %s candidate %s failed: %s", node, spec.id, e2)
                _fail(e2, t0)
                continue
        latency = int((time.monotonic() - t0) * 1000)
        content = resp.choices[0].message.content or ""
        if not content.strip():
            last_err = ValueError("empty completion")
            log.warning("llm %s candidate %s returned empty; rotating", node, spec.id)
            # 返空是 GLM-5.2 高推理力度下的招牌故障(推理烧光输出预算)。记下来 + requested.clamped,
            # 才能事后区分「模型不行」与「我们给的预算不够」。
            _record(run_id, node, spec, getattr(resp, "usage", _NO_USAGE), tc.value, used_sub,
                    status="empty", error="empty completion", attempt=attempt, context=context,
                    prompt_sha=psha, requested=_req(spec), latency_ms=latency)
            continue
        _record(run_id, node, spec, resp.usage, tc.value, used_sub, attempt=attempt,
                context=context, prompt_sha=psha, requested=_req(spec), latency_ms=latency)
        log.info("route %s -> %s [%s]", tc.value, spec.id, spec.billing.value)
        return content

    raise last_err or RuntimeError(f"all LLM candidates failed for {node}")


def _msg_to_dict(m) -> dict:
    """A streamed assistant message → a JSON-serializable dict (content + tool_calls)."""
    out: dict = {"role": "assistant", "content": m.content or ""}
    tcs = getattr(m, "tool_calls", None)
    if tcs:
        out["tool_calls"] = [t.model_dump() if hasattr(t, "model_dump") else dict(t) for t in tcs]
    return out


def complete_stream(
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    task: "router.TaskClass | str" = "chat",
    node: str = "chathy",
    run_id: str | None = None,
    max_tokens: int = 4000,
    reasoning_effort: str | None = None,
) -> Iterator[dict]:
    """Streaming, tool-calling completion for the Chathy chat agent.

    `messages` is a ready OpenAI-style message list (the agent owns history). Yields
    event dicts: `{"type":"delta","text":...}` as content streams, then exactly one
    terminal event — `{"type":"final","message":<assistant dict incl. tool_calls>,
    "usage":{...}}` on success, or `{"type":"error","message":...}`.

    Candidate rotation happens ONLY before the first content delta (a mid-stream failure
    surfaces as an error event, not a silent model switch). Usage + tool_calls are
    reconstructed once via `litellm.stream_chunk_builder` and billed through `_record`.
    """
    _ensure_keys()
    s = get_settings()
    tc = router.as_task(task, "strong")
    # want_strong 取**调整后**能力(route_plan),与 complete() 一致(J.1.3):此前读静态
    # POLICIES[tc].capability,动态升/降层后会与实际所选层错配、给错推理力度。
    plan = router.route_plan(tc)
    chain = _apply_pin(plan.chain)
    if not chain:
        yield {"type": "error", "message": f"no model candidates for task {tc.value}"}
        return
    want_strong = plan.capability in (Capability.STRONG, Capability.REASONING)
    cap = _budget_cap(run_id, s)
    spent = _spent(run_id) if run_id else 0.0
    last_err: Exception | None = None

    for spec in chain:
        executor = getattr(spec, "executor", "litellm")
        if executor != "litellm":
            # Subscription executors (agent_sdk/codex_cli) are single-shot (no streaming/tool
            # loop); skip here so the streaming chat chain rotates to a litellm candidate.
            last_err = RuntimeError(f"{spec.id}: {executor} not supported for streaming")
            continue
        base, key_env, used_sub = _endpoint(spec, s)
        if key_env and not os.environ.get(key_env):
            last_err = RuntimeError(f"{spec.id}: {key_env} not configured")
            continue
        if run_id and not used_sub and spent >= cap:
            last_err = BudgetExceeded(f"run {run_id} exceeded ${cap}")
            continue
        kwargs = _build_kwargs(spec, messages, max_tokens, want_strong, False, s, base, key_env,
                               reasoning_effort)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        chunks: list = []
        started = False
        try:
            for chunk in litellm.completion(**kwargs):
                chunks.append(chunk)
                delta = chunk.choices[0].delta if getattr(chunk, "choices", None) else None
                text = getattr(delta, "content", None) if delta else None
                if text:
                    started = True
                    yield {"type": "delta", "text": text}
        except Exception as e:  # noqa: BLE001
            if not started and _retryable(e):        # rotate only before the first delta
                last_err = e
                log.warning("stream %s candidate %s failed pre-delta: %s", node, spec.id, e)
                continue
            log.warning("stream %s candidate %s failed mid-stream: %s", node, spec.id, e)
            yield {"type": "error", "message": str(e)}
            return

        try:
            full = litellm.stream_chunk_builder(chunks, messages=messages)
        except Exception:  # noqa: BLE001
            full = None
        msg = _msg_to_dict(full.choices[0].message) if full and full.choices else {"role": "assistant", "content": ""}
        if not msg.get("content", "").strip() and not msg.get("tool_calls"):
            last_err = ValueError("empty completion")            # rotate: nothing yielded yet
            if not started:
                continue
        usage = getattr(full, "usage", None) if full else None
        _record(run_id, node, spec, usage, tc.value, used_sub)
        log.info("route %s -> %s [%s] (stream)", tc.value, spec.id, spec.billing.value)
        yield {"type": "final", "message": msg,
               "usage": _usage_dict(usage)}
        return

    yield {"type": "error", "message": str(last_err or f"all LLM candidates failed for {node}")}


def _usage_dict(usage) -> dict:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        try:
            return usage.model_dump()
        except Exception:  # noqa: BLE001
            pass
    return {"prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0)}


def complete_json(
    prompt: str,
    schema: Type[T],
    *,
    system: str | None = None,
    tier: str = "fast",
    task: "router.TaskClass | str | None" = None,
    node: str = "?",
    run_id: str | None = None,
    max_tokens: int = 6000,
    complexity: str | None = None,
    relevance: str | None = None,
    reasoning_effort: str | None = None,
    context: dict | None = None,
    capture: dict | None = None,
    on_fail: str = "empty",
) -> T:
    """Structured output: prompt for JSON matching `schema`, parse + validate, retry once.
    Provider-agnostic; a hard provider failure rotates providers (see complete), and the
    empty schema is only the final safety net. `complexity`/`relevance` → dynamic routing.

    `capture`(可选,给传入的 dict 填字段):留下这次调用的**可回放痕迹** ——
    `raw`(模型原文,解析前)/`instruction`(含 schema 子句的完整提示词)/`prompt_sha`/
    `schema_sha`/`attempts`。默认不传 = 零开销,正文不进热表 `llm_usage`。"""
    instruction = json_instruction(prompt, schema)
    if capture is not None:
        capture["instruction"] = instruction
        capture["prompt_sha"] = _sha(system, instruction)
        capture["schema_sha"] = hashlib.sha256(
            json.dumps(schema.model_json_schema(), sort_keys=True,
                       ensure_ascii=False).encode()).hexdigest()
    last_err = None
    for attempt in range(2):
        raw = complete(
            instruction if attempt == 0 else instruction + "\n\nYour previous reply was not valid JSON. Return only the JSON object.",
            system=system, tier=tier, task=task, node=node, run_id=run_id, max_tokens=max_tokens,
            json_mode=True, complexity=complexity, relevance=relevance,
            reasoning_effort=reasoning_effort, context=context,
        )
        if capture is not None:
            capture["raw"] = raw
            capture["attempts"] = attempt + 1
        obj = _extract_json(raw)
        if obj is not None:
            try:
                return schema.model_validate(obj)
            except Exception as e:  # noqa: BLE001
                last_err = e
        else:
            last_err = ValueError("no JSON object found")
    log.warning("structured output failed for %s: %s", node, last_err)
    if capture is not None:
        capture["fallback"] = True          # 兜底 schema() —— 调用方据此区分「真产出」与「空壳」
    if on_fail == "raise":
        raise StructuredOutputError(f"{node}: no valid JSON after 2 attempts ({last_err})")
    return schema()  # safe empty default


def json_instruction(prompt: str, schema: Type[T]) -> str:
    """The exact instruction complete_json sends (prompt + schema clause). Module-level so
    the bench harness can issue byte-identical calls via complete() and score raw validity
    itself — complete_json's empty-default fallback would hide invalid JSON from a benchmark."""
    js = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    return (
        f"{prompt}\n\nReturn ONLY a JSON object matching this JSON Schema "
        f"(no markdown, no prose):\n{js}"
    )


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start: end + 1])
        except Exception:  # noqa: BLE001
            return None
    return None
