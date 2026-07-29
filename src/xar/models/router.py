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
from typing import NamedTuple

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
    AUDIT = "audit"               # single, strong TOKEN — INDEPENDENT crawl/archival verifier
    EARNINGS_JUDGE = "earnings_judge"  # single, strong TOKEN — pre-earnings long/short verdict
    PHANNY_VERDICT = "phanny_verdict"  # single, strong TOKEN — Phanny 多空提议/裁决(host 提级订阅)
    PHANNY_CHALLENGE = "phanny_challenge"  # single, strong TOKEN — Phanny 反方 critic(异厂商钉扎)


@dataclass(frozen=True)
class RoutePolicy:
    capability: Capability
    prefer_billing: str            # Billing.value | "any"
    volume: str                    # "bulk" | "normal"
    fallback: tuple[str, ...] = field(default_factory=tuple)  # explicit model ids appended last
    # Settings 属性名。非空且该 setting 有值 → 它的 csv 就是这个 task 的**精确有序链**,
    # registry 候选一律不掺(见 `_resolve_policy`)。留空 = 常规能力/计费策略解析。
    chain_setting: str = ""


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
    # interactive chat → **精确有序链**(settings.chat_models,2026-07-29 用户裁定):
    # Kimi-K3 → GLM-5.2 → MiniMax-M3 → DeepSeek-V4-Pro。前三席订阅(usd=0)故 billing_pref
    # 改 SUBSCRIPTION —— 仅在 chat_models 被清空时才生效(那时退回常规策略解析),保证清空后
    # 也不会漂回无界 token 池。链上全是思考模型,输出预算见 settings.chat_max_tokens。
    TaskClass.CHAT:         RoutePolicy(Capability.STRONG, Billing.SUBSCRIPTION.value, "normal",
                                        chain_setting="chat_models"),
    # 947-name thesis batch → subscription-first bounded cost, same shape as KG_EXTRACT;
    # flagship names get a separate EDITOR-tier quality pass in research/thesis.py.
    TaskClass.THESIS:       RoutePolicy(Capability.CHEAP_BULK, Billing.SUBSCRIPTION.value, "bulk"),
    # 微信 triage 是抽取前的廉价预筛(短 prompt),与 EXPERT 同策略:订阅池优先、成本有界。
    TaskClass.WECHAT_TRIAGE: RoutePolicy(Capability.CHEAP_BULK, Billing.SUBSCRIPTION.value, "bulk"),
    # 证据→争论/支柱的相对主张分类(短 prompt、每公司一批),与 EXPERT/THESIS 同策略:订阅池优先。
    TaskClass.THESIS_LINK:  RoutePolicy(Capability.CHEAP_BULK, Billing.SUBSCRIPTION.value, "bulk"),
    # 独立抓取审计:强 TOKEN 模型 —— 刻意**不落 GLM 订阅池**,验收模型≠生产模型,量小成本有界。
    TaskClass.AUDIT:        RoutePolicy(Capability.STRONG, Billing.TOKEN.value, "normal"),
    # 季报事件裁决:强 TOKEN 深度研究任务 —— host 上由 build_verdict 内 llm.pinned 提级到
    # 订阅执行器(codex-sub/claude-opus-max,$0);docker/无执行器落 deepseek 强 token,量 1-3 次/天。
    TaskClass.EARNINGS_JUDGE: RoutePolicy(Capability.STRONG, Billing.TOKEN.value, "normal"),
    # Phanny 提议/裁决 + 反方 critic:强深度研究任务,**订阅优先**(2026-07-25 裁定:Phanny 只用
    # 订阅项下 minimax/kimi/glm,移除按 token 计费的 deepseek)。engine/debate 内 llm.pinned 会把链
    # 钉到 host 订阅执行器或 phanny_primary_models/phanny_challenger_models(均 $0)并显式
    # reasoning_effort="high";此处 billing_pref=SUBSCRIPTION 保证任何未钉扎路径也不漂回计量模型。
    TaskClass.PHANNY_VERDICT:   RoutePolicy(Capability.STRONG, Billing.SUBSCRIPTION.value, "normal"),
    TaskClass.PHANNY_CHALLENGE: RoutePolicy(Capability.STRONG, Billing.SUBSCRIPTION.value, "normal"),
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


def _chain_from_setting(name: str) -> list[ModelSpec]:
    """A settings csv of registry ids → an **exactly ordered** chain. Unknown ids are logged and
    skipped (a typo must not silently re-route); all-unknown/blank → empty, caller falls back to
    the normal capability/billing resolution."""
    if not name:
        return []
    from ..config import get_settings
    out: list[ModelSpec] = []
    for mid in (x.strip() for x in (getattr(get_settings(), name, "") or "").split(",")):
        if not mid:
            continue
        spec = registry.get(mid)
        if spec is None:
            log.warning("route chain %s: unknown model id %r — skipped", name, mid)
            continue
        out.append(spec)
    return out


def _resolve_policy(task: TaskClass, p: RoutePolicy) -> list[ModelSpec]:
    """Build the ordered chain for a (task, policy). Override > env > registry candidates
    > explicit fallbacks. `p` may be the static POLICIES[task] or a dynamically-adjusted one.

    **billing 偏好压过 env 默认**(2026-07-29 修):`_env_spec` 曾被无条件插在链首,于是
    `model_strong` 的默认值 `deepseek-v4-pro`(TOKEN,max_output=8192)会抢在所有订阅模型前面 ——
    对偏好 SUBSCRIPTION 的任务这有两重破坏:
      ① 违背 route_plan 写明的安全线「bulk 升级仍走 SUBSCRIPTION 优先,绝不越到无界 token 池」,
         也违背用户对 Phanny「只用订阅项下模型」的裁定(未钉扎路径会漂回计量模型);
      ② 实测把 thesis 打废:大 dossier 触发 cheap_bulk→strong 升层后落到 deepseek-v4-pro,
         16000 的 max_tokens 被 8192 钳断 → JSON 截断 → 「no JSON object found」→ 整批 llm_failed。
    现在:env 默认若与偏好计费方式不符,降到偏好候选**之后**(仍是可用回退,不被丢弃);
    ops 的 route_overrides 是人为显式指令,继续排最前。billing_pref='any' 的任务行为不变。"""
    explicit = _chain_from_setting(p.chain_setting)
    if explicit:
        # 精确有序链:ops 的 route_overrides 是人为显式指令,仍排最前;其后严格按配置顺序,
        # **不掺 registry 候选、不掺 env 默认** —— 否则「kimi 领衔」会被 model_strong 之类抢头,
        # 也会出现没人要求过的候选静默接住 chat 流量(计费漂移面)。
        return _dedup([registry.override_for(task.value, p.capability.value), *explicit])
    env = _env_spec(p.capability)
    env_matches = (p.prefer_billing == "any" or
                   (env is not None and env.billing.value == p.prefer_billing))
    chain: list[ModelSpec | None] = []
    chain.append(registry.override_for(task.value, p.capability.value))   # ops runtime switch
    if env_matches:
        chain.append(env)                                                 # env override
    chain += registry.candidates_for(p.capability, billing_pref=p.prefer_billing)  # registry
    if not env_matches:
        chain.append(env)                        # 计费方式不符:留作回退,但不许抢在偏好候选前
    chain += [registry.get(m) for m in p.fallback]                        # explicit tail
    return _dedup(chain)


def _complexity_from_chars(n: int | None) -> str | None:
    """Derive complexity from prompt size when the caller gives no explicit hint."""
    if not n:
        return None
    from ..config import get_settings
    s = get_settings()
    if n >= s.dynamic_routing_chars_high:
        return "high"
    if n <= s.dynamic_routing_chars_low:
        return "low"
    return "medium"


class RoutePlan(NamedTuple):
    """动态路由决策:被选中的(可能已升/降的)能力层 + 解析出的回退链。返回 `capability`
    使调用方能据实际所选层设定 reasoning_effort/want_strong,而非重查静态策略(修 J.1.3)。"""
    capability: Capability
    chain: list[ModelSpec]


def route_plan(task: TaskClass, *, complexity: str | None = None, relevance: str | None = None,
               input_chars: int | None = None) -> RoutePlan:
    """动态路由:按 **complexity**(显式 or 从 `input_chars` 推)× **relevance**(内容价值)在能力层间
    升降,返回**调整后能力** + 回退链。守既有安全线:bulk 升级仍走 SUBSCRIPTION 优先(GLM-5.2/kimi/
    minimax-thinking),绝不越到无界 token 池。complexity/relevance 均无信号 → 退化为静态 per-task 路由。
    注:能力升层后 `_resolve_policy` 的 override 查表用**调整后**能力键(如 cheap_bulk→strong),故某
    task 的 cheap_bulk 级 route override 在升层场景不参与——task 级 override 不受影响。"""
    from ..config import get_settings

    p = POLICIES[task]
    cap = p.capability
    if get_settings().dynamic_routing_enabled:
        comp = complexity or _complexity_from_chars(input_chars)
        if p.capability == Capability.CHEAP_BULK and (comp == "high" or relevance == "high"):
            cap = Capability.STRONG          # 复杂/高价值 bulk → 升强层(订阅优先,成本仍有界)
        elif p.capability == Capability.STRONG and comp == "low" and relevance != "high":
            cap = Capability.FAST            # 简单强任务 → 降快层(省成本)
        if cap != p.capability:
            log.info("dynamic route %s: %s → %s (complexity=%s relevance=%s)",
                     task.value, p.capability.value, cap.value, comp, relevance)
    adjusted = RoutePolicy(cap, p.prefer_billing, p.volume, p.fallback, p.chain_setting)
    return RoutePlan(cap, _resolve_policy(task, adjusted))


def route(task: TaskClass, *, complexity: str | None = None, relevance: str | None = None,
          input_chars: int | None = None) -> list[ModelSpec]:
    """动态回退链(仅链,向后兼容)。需要调整后能力的调用方用 route_plan()。"""
    return route_plan(task, complexity=complexity, relevance=relevance,
                      input_chars=input_chars).chain


def resolve(task: TaskClass) -> list[ModelSpec]:
    """Static per-task fallback chain (no complexity/relevance signals). Back-compat entry."""
    return route(task)


def _dedup(specs: list[ModelSpec | None]) -> list[ModelSpec]:
    seen: set[str] = set()
    out: list[ModelSpec] = []
    for s in specs:
        if s and s.id not in seen:
            seen.add(s.id)
            out.append(s)
    return out
