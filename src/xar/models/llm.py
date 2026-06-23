"""LLM access via LiteLLM. Two-tier routing (fast=Haiku / strong=Opus),
per-run cost tracking + hard budget cap, and resilient structured output.

Pluggable: any LiteLLM-supported provider works by changing the model ids in
config. Default provider is Anthropic (only ANTHROPIC_API_KEY required)."""
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

log = get_logger("xar.llm")
litellm.drop_params = True  # silently drop params a provider doesn't accept

# Local price table ($/1M tokens) so cost tracking works even for models
# newer than LiteLLM's bundled map.
_PRICES = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-fable-5": (10.0, 50.0),
    # DeepSeek (approx public pricing, $/1M)
    "deepseek/deepseek-chat": (0.27, 1.10),
    "deepseek/deepseek-reasoner": (0.55, 2.19),
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    # DeepSeek V4 (flash = general/extraction, pro = reasoning/debate)
    "deepseek/deepseek-v4-flash": (0.28, 1.14),
    "deepseek/deepseek-v4-pro": (0.60, 2.40),
    "deepseek-v4-flash": (0.28, 1.14),
    "deepseek-v4-pro": (0.60, 2.40),
}

T = TypeVar("T", bound=BaseModel)


class BudgetExceeded(RuntimeError):
    pass


# Batch jobs (build_kg / expert.process / synthesize_all) attribute their spend to a
# run_id with one of these prefixes so the (larger) batch budget cap actually bounds
# them — otherwise run_id=None meant `_spent` was always 0 and the cap never fired.
_BATCH_PREFIXES = ("kg", "expert", "synth", "batch")


def new_batch_run_id(prefix: str = "batch") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _budget_cap(run_id: str | None, s) -> float:
    if run_id and run_id.split("-", 1)[0] in _BATCH_PREFIXES:
        return s.llm_max_usd_per_batch
    return s.llm_max_usd_per_run


_KEYS_SYNCED = False


def _ensure_keys() -> None:
    """LiteLLM reads provider keys from os.environ; pydantic loads them from
    .env into Settings only. Mirror them into os.environ so both the Docker
    (env_file) and local (.env) paths work identically."""
    global _KEYS_SYNCED
    if _KEYS_SYNCED:
        return
    s = get_settings()
    for var, val in {
        "ANTHROPIC_API_KEY": s.anthropic_api_key,
        "OPENAI_API_KEY": s.openai_api_key,
        "DEEPSEEK_API_KEY": s.deepseek_api_key,
    }.items():
        if val and not os.environ.get(var):
            os.environ[var] = val
    _KEYS_SYNCED = True


def _provider_model(model: str) -> str:
    """Prefix models with their LiteLLM provider so routing works for new ids.
    Already-prefixed ids (e.g. 'deepseek/deepseek-chat') pass through."""
    if "/" in model:
        return model
    if model.startswith("claude-"):
        return f"anthropic/{model}"
    if model.startswith("deepseek"):
        return f"deepseek/{model}"
    return model


def _price(model: str, in_tok: int, out_tok: int) -> float:
    pin, pout = _PRICES.get(model, (3.0, 15.0))
    return (in_tok * pin + out_tok * pout) / 1_000_000


def _spent(run_id: str | None) -> float:
    if not run_id:
        return 0.0
    rows = db.query("SELECT COALESCE(SUM(usd),0) AS s FROM llm_usage WHERE run_id=%s", (run_id,))
    return float(rows[0]["s"]) if rows else 0.0


def _record(run_id, node, model, usage) -> None:
    in_tok = getattr(usage, "prompt_tokens", 0) or 0
    out_tok = getattr(usage, "completion_tokens", 0) or 0
    usd = _price(model, in_tok, out_tok)
    try:
        db.execute(
            "INSERT INTO llm_usage(run_id,node,model,input_tokens,output_tokens,usd) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            (run_id, node, model, in_tok, out_tok, usd),
        )
    except Exception:  # usage logging must never break a run
        pass


def complete(
    prompt: str,
    *,
    system: str | None = None,
    tier: str = "fast",
    node: str = "?",
    run_id: str | None = None,
    max_tokens: int = 4000,
    json_mode: bool = False,
) -> str:
    """Plain text completion."""
    _ensure_keys()
    s = get_settings()
    cap = _budget_cap(run_id, s)
    if run_id and _spent(run_id) >= cap:
        raise BudgetExceeded(f"run {run_id} exceeded ${cap}")

    model = s.model_strong if tier == "strong" else s.model_fast
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    kwargs: dict = dict(model=_provider_model(model), messages=messages, max_tokens=max_tokens)
    # Reasoning-model tiers (e.g. DeepSeek V4): cap the general tier's thinking
    # ("low") so structured extraction reliably emits its answer within budget;
    # let the reasoning tier think hard. Dropped/retried for non-reasoning models.
    kwargs["reasoning_effort"] = s.model_effort if tier == "strong" else "low"
    if json_mode:  # constrain to valid JSON (DeepSeek/OpenAI json mode); dropped if unsupported
        kwargs["response_format"] = {"type": "json_object"}

    try:
        resp = litellm.completion(**kwargs)
    except Exception as e:
        # retry once without reasoning_effort (some providers reject it). KEEP
        # response_format: dropping JSON mode on the retry would silently degrade
        # complete_json() to free text exactly when structure matters most.
        # litellm.drop_params already strips response_format for providers that
        # genuinely don't support it, so retaining it here is safe.
        kwargs.pop("reasoning_effort", None)
        log.warning("llm retry (%s): %s", node, e)
        resp = litellm.completion(**kwargs)

    _record(run_id, node, model, resp.usage)
    return resp.choices[0].message.content or ""


def complete_json(
    prompt: str,
    schema: Type[T],
    *,
    system: str | None = None,
    tier: str = "fast",
    node: str = "?",
    run_id: str | None = None,
    max_tokens: int = 6000,
) -> T:
    """Structured output: prompt for JSON matching `schema`, parse + validate,
    retry once on failure. Provider-agnostic (no reliance on a specific
    structured-output API)."""
    js = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    instruction = (
        f"{prompt}\n\nReturn ONLY a JSON object matching this JSON Schema "
        f"(no markdown, no prose):\n{js}"
    )
    last_err = None
    for attempt in range(2):
        raw = complete(
            instruction if attempt == 0 else instruction + "\n\nYour previous reply was not valid JSON. Return only the JSON object.",
            system=system, tier=tier, node=node, run_id=run_id, max_tokens=max_tokens,
            json_mode=True,
        )
        obj = _extract_json(raw)
        if obj is not None:
            try:
                return schema.model_validate(obj)
            except Exception as e:
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
    except Exception:
        pass
    # find the outermost {...}
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            return None
    return None
