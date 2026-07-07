"""Task router for the LLM task manager.

Maps a TaskClass to an ordered fallback chain of ModelSpecs, resolved from the
code-as-truth registry under a billing-aware policy:

  - BULK / SEARCH tasks prefer SUBSCRIPTION models first (GLM/Kimi flat-rate), so a
    nightly extraction over the whole corpus never runs up an unbounded token bill;
    the per-token DeepSeek models trail as a budget-capped fallback.
  - QUALITY / single-shot tasks prefer the strongest token model, with cross-provider
    strong fallbacks.

Resolution precedence (strongest first), so an operator can re-route live:
    route_overrides table (ops API)  >  env (XAR_MODEL_*)  >  registry `preferred`.

`tier="fast"|"strong"` maps to ADHOC_FAST/ADHOC_STRONG, which resolve to
settings.model_fast/model_strong exactly as before — unmigrated call sites are unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..logging import get_logger
from . import registry
from .registry import Billing, Capability, ModelSpec

log = get_logger("xar.router")


class TaskClass(str, Enum):
    KG_EXTRACT = "kg_extract"      # bulk: build_kg
    EXPERT = "expert"             # bulk: expert.process
    SEARCH_BULK = "search_bulk"   # large-scale enumeration / search
    ANALYST = "analyst"           # single, fast/strong
    DEBATE = "debate"             # single, strong
    EDITOR = "editor"             # single, strong (report synthesis)
    JUDGE = "judge"               # single, fast (evidence gate)
    SYNTH = "synth"               # low-vol, strong (frontier synthesis)
    EVAL = "eval"                 # one-off, fast
    ADHOC_FAST = "adhoc_fast"     # default for tier="fast"
    ADHOC_STRONG = "adhoc_strong"  # default for tier="strong"
    CHAT = "chat"                 # Chathy: interactive tool-calling chat (strong, token)
    THESIS = "thesis"             # bulk: company-thesis generation (research/thesis.py)
    WECHAT_TRIAGE = "wechat_triage"  # bulk: cheap pre-extraction SNR triage (mining/triage.py)
    THESIS_LINK = "thesis_link"   # bulk: claim-relative evidence→debate/pillar classification


@dataclass(frozen=True)
class RoutePolicy:
    capability: Capability
    prefer_billing: str            # Billing.value | "any"
    volume: str                    # "bulk" | "normal"
    fallback: tuple[str, ...] = field(default_factory=tuple)  # explicit model ids appended last


POLICIES: dict[TaskClass, RoutePolicy] = {
    # bulk / search → subscription FIRST (bounded bill), then cheap token under budget
    TaskClass.KG_EXTRACT:   RoutePolicy(Capability.CHEAP_BULK, Billing.SUBSCRIPTION.value, "bulk"),
    TaskClass.EXPERT:       RoutePolicy(Capability.CHEAP_BULK, Billing.SUBSCRIPTION.value, "bulk"),
    TaskClass.SEARCH_BULK:  RoutePolicy(Capability.CHEAP_BULK, Billing.SUBSCRIPTION.value, "bulk"),
    # quality / single → strongest token, cross-provider strong fallback
    TaskClass.DEBATE:       RoutePolicy(Capability.STRONG, Billing.TOKEN.value, "normal"),
    TaskClass.EDITOR:       RoutePolicy(Capability.STRONG, Billing.TOKEN.value, "normal"),
    TaskClass.SYNTH:        RoutePolicy(Capability.STRONG, Billing.TOKEN.value, "normal"),
    TaskClass.ANALYST:      RoutePolicy(Capability.FAST, "any", "normal"),
    TaskClass.JUDGE:        RoutePolicy(Capability.FAST, "any", "normal"),
    TaskClass.EVAL:         RoutePolicy(Capability.FAST, "any", "normal"),
    TaskClass.ADHOC_FAST:   RoutePolicy(Capability.FAST, "any", "normal"),
    TaskClass.ADHOC_STRONG: RoutePolicy(Capability.STRONG, "any", "normal"),
    # interactive chat → strongest TOKEN model (latency + reliable function-calling);
    # deliberately NOT the subscription bulk pool, which is tuned for nightly volume.
    TaskClass.CHAT:         RoutePolicy(Capability.STRONG, Billing.TOKEN.value, "normal"),
    # 947-name thesis batch → subscription-first bounded cost, same shape as KG_EXTRACT;
    # flagship names get a separate EDITOR-tier quality pass in research/thesis.py.
    TaskClass.THESIS:       RoutePolicy(Capability.CHEAP_BULK, Billing.SUBSCRIPTION.value, "bulk"),
    # 微信 triage 是抽取前的廉价预筛(短 prompt),与 EXPERT 同策略:订阅池优先、成本有界。
    TaskClass.WECHAT_TRIAGE: RoutePolicy(Capability.CHEAP_BULK, Billing.SUBSCRIPTION.value, "bulk"),
    # 证据→争论/支柱的相对主张分类(短 prompt、每公司一批),与 EXPERT/THESIS 同策略:订阅池优先。
    TaskClass.THESIS_LINK:  RoutePolicy(Capability.CHEAP_BULK, Billing.SUBSCRIPTION.value, "bulk"),
}


def as_task(task: TaskClass | str | None, tier: str) -> TaskClass:
    """Coerce a task/tier into a TaskClass. tier= is the back-compat alias."""
    if isinstance(task, TaskClass):
        return task
    if isinstance(task, str):
        try:
            return TaskClass(task)
        except ValueError:
            # A typo'd / stale task string would silently fall to a token adhoc class,
            # bypassing the subscription-first bulk billing protection — make it loud so a
            # future regression is visible rather than a silent cost leak.
            log.warning("unknown task %r — falling back to tier=%r adhoc routing", task, tier)
    return TaskClass.ADHOC_STRONG if tier == "strong" else TaskClass.ADHOC_FAST


def _env_spec(capability: Capability) -> ModelSpec | None:
    """The env-configured model for a capability (XAR_MODEL_FAST/STRONG/BULK) as a
    ModelSpec — registered if known, else synthesized as a token model so a brand-new
    override id still routes. None when unset."""
    from ..config import get_settings
    s = get_settings()
    litellm_id = ""
    if capability == Capability.FAST:
        litellm_id = s.model_fast
    elif capability in (Capability.STRONG, Capability.REASONING):
        litellm_id = s.model_strong
    elif capability == Capability.CHEAP_BULK:
        litellm_id = getattr(s, "model_bulk", "") or ""
    if not litellm_id:
        return None
    spec = registry.by_litellm(litellm_id)
    if spec:
        return spec
    return _synth(litellm_id, capability)


def _synth(litellm_id: str, capability: Capability) -> ModelSpec:
    """Build a token ModelSpec for an unregistered model id (env override of a model
    not yet in the registry) so it can still be routed and priced."""
    from .llm import _provider_model
    full = _provider_model(litellm_id)
    prov = full.split("/", 1)[0] if "/" in full else "openai"
    price = registry.PRICES.get(full) or registry.PRICES.get(litellm_id) or (3.0, 15.0)
    return ModelSpec(id=f"env:{litellm_id}", provider=prov if prov in registry.PROVIDERS else "openai",
                     litellm_model=full, capabilities=(capability,), billing=Billing.TOKEN,
                     price_in=price[0], price_out=price[1], supports_reasoning=("deepseek" in full))


def resolve(task: TaskClass) -> list[ModelSpec]:
    """The ordered fallback chain for a task. Override > env > registry preferred,
    then billing-aware registry candidates, then explicit policy fallbacks."""
    p = POLICIES[task]
    chain: list[ModelSpec | None] = []
    chain.append(registry.override_for(task.value, p.capability.value))   # ops runtime switch
    chain.append(_env_spec(p.capability))                                 # env override
    chain += registry.candidates_for(p.capability, billing_pref=p.prefer_billing)  # registry
    chain += [registry.get(m) for m in p.fallback]                        # explicit tail
    return _dedup(chain)


def _dedup(specs: list[ModelSpec | None]) -> list[ModelSpec]:
    seen: set[str] = set()
    out: list[ModelSpec] = []
    for s in specs:
        if s and s.id not in seen:
            seen.add(s.id)
            out.append(s)
    return out
