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

import json
import os
import re
import uuid
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


# Batch jobs (build_kg / expert.process / synthesize_all) attribute spend to a run_id with
# one of these prefixes so the (larger) batch budget cap actually bounds them.
_BATCH_PREFIXES = ("kg", "expert", "synth", "batch")


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
    for var, val in {
        "ANTHROPIC_API_KEY": s.anthropic_api_key,
        "OPENAI_API_KEY": s.openai_api_key,
        "DEEPSEEK_API_KEY": s.deepseek_api_key,
        "GLM_API_KEY": s.glm_api_key,
        "MOONSHOT_API_KEY": s.moonshot_api_key,
        "GLM_SUB_API_KEY": s.glm_sub_api_key,
        "MOONSHOT_SUB_API_KEY": s.moonshot_sub_api_key,
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


def _record(run_id, node, spec, usage, task_class: str, used_sub: bool) -> None:
    in_tok = getattr(usage, "prompt_tokens", 0) or 0
    out_tok = getattr(usage, "completion_tokens", 0) or 0
    # EFFECTIVE billing, not the spec's nominal billing: a SUBSCRIPTION model that fell back
    # to the provider's metered key (no sub key configured) is really billing per token, so
    # record its real cost — otherwise that spend is invisible to the budget cap. usd=0 only
    # when the flat subscription endpoint was actually used.
    billing = "subscription" if used_sub else "token"
    usd = 0.0 if used_sub else _price(spec.litellm_model, in_tok, out_tok)
    try:
        db.execute(
            "INSERT INTO llm_usage(run_id,node,model,input_tokens,output_tokens,usd,"
            "provider,task_class,billing) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (run_id, node, spec.litellm_model, in_tok, out_tok, usd,
             spec.provider, task_class, billing),
        )
    except Exception:  # noqa: BLE001 — usage logging must never break a run
        pass


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


_SUB_BASE_ATTR = {"zhipu": "glm_sub_api_base", "moonshot": "moonshot_sub_api_base"}


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


def _build_kwargs(spec, messages, max_tokens, want_strong, json_mode, s, base, key_env) -> dict:
    out = max_tokens if not spec.max_output else min(max_tokens, spec.max_output)
    kwargs: dict = dict(model=spec.litellm_model, messages=messages, max_tokens=out)
    if spec.supports_reasoning:  # let strong tasks think; cap the general/bulk tier's thinking
        kwargs["reasoning_effort"] = s.model_effort if want_strong else "low"
    if json_mode and spec.supports_json:
        kwargs["response_format"] = {"type": "json_object"}
    if base:                      # OpenAI-compatible / subscription endpoint, per candidate
        kwargs["api_base"] = base
    if key_env and os.environ.get(key_env):
        kwargs["api_key"] = os.environ[key_env]
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
) -> str:
    """Plain-text completion, task-routed with cross-provider fallback."""
    _ensure_keys()
    s = get_settings()
    tc = router.as_task(task, tier)
    chain = router.resolve(tc)
    if not chain:
        raise RuntimeError(f"no model candidates for task {tc.value}")
    want_strong = router.POLICIES[tc].capability in (Capability.STRONG, Capability.REASONING)
    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
    cap = _budget_cap(run_id, s)
    spent = _spent(run_id) if run_id else 0.0
    last_err: Exception | None = None

    # Fallback is resilience, not free insurance: during a partial provider outage a quality
    # task can rotate down its chain to a pricier model (e.g. deepseek-pro → sonnet → opus),
    # so a report costs more than usual. It is NOT a runaway — token spend still counts toward
    # the budget cap and over-cap token candidates are skipped below — but spend accelerates
    # while the preferred provider is down. (Subscription/flat-plan candidates record usd=0.)
    for spec in chain:
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
        kwargs = _build_kwargs(spec, messages, max_tokens, want_strong, json_mode, s, base, key_env)
        try:
            resp = litellm.completion(**kwargs)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if not _retryable(e):                     # auth / bad-request / deterministic → rotate now
                log.warning("llm %s candidate %s failed: %s", node, spec.id, e)
                continue
            kwargs.pop("reasoning_effort", None)       # one in-candidate retry (some providers reject it)
            try:
                resp = litellm.completion(**kwargs)
            except Exception as e2:  # noqa: BLE001
                last_err = e2
                log.warning("llm %s candidate %s failed: %s", node, spec.id, e2)
                continue
        content = resp.choices[0].message.content or ""
        if not content.strip():
            last_err = ValueError("empty completion")
            log.warning("llm %s candidate %s returned empty; rotating", node, spec.id)
            continue
        _record(run_id, node, spec, resp.usage, tc.value, used_sub)
        log.info("route %s -> %s [%s]", tc.value, spec.id, spec.billing.value)
        return content

    raise last_err or RuntimeError(f"all LLM candidates failed for {node}")


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
) -> T:
    """Structured output: prompt for JSON matching `schema`, parse + validate, retry once.
    Provider-agnostic; a hard provider failure rotates providers (see complete), and the
    empty schema is only the final safety net."""
    js = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    instruction = (
        f"{prompt}\n\nReturn ONLY a JSON object matching this JSON Schema "
        f"(no markdown, no prose):\n{js}"
    )
    last_err = None
    for attempt in range(2):
        raw = complete(
            instruction if attempt == 0 else instruction + "\n\nYour previous reply was not valid JSON. Return only the JSON object.",
            system=system, tier=tier, task=task, node=node, run_id=run_id, max_tokens=max_tokens,
            json_mode=True,
        )
        obj = _extract_json(raw)
        if obj is not None:
            try:
                return schema.model_validate(obj)
            except Exception as e:  # noqa: BLE001
                last_err = e
        else:
            last_err = ValueError("no JSON object found")
    log.warning("structured output failed for %s: %s", node, last_err)
    return schema()  # safe empty default


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
