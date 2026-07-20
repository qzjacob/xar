"""Code-as-truth model library for the LLM task manager.

This is the single place that knows which models exist, what they cost, how they
bill (per-token vs flat subscription), and what they're good for. Keeping a new
model generation up to date ("换代") is a one-file edit: add a `ModelSpec`, set
`preferred=True`, flip the old one to `Status.DEPRECATED`. Everything downstream
(price table, router candidate chains, the ops console) reads `MODELS`, so it
follows automatically.

Three override layers decide the *active* model for a capability/task, strongest
last so an operator can re-route live without a redeploy:
    preferred flag (code)  <  env (XAR_MODEL_*)  <  route_overrides table (ops API)
The `route_overrides` lookup is TTL-cached so a live switch propagates across the
app/dagster processes within ~a minute without a per-call DB hit.

No DB or network at import — pure dataclasses + stdlib, like the ontology modules.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum


class Billing(str, Enum):
    TOKEN = "token"                # metered per-token API — unbounded bill risk
    SUBSCRIPTION = "subscription"  # flat plan / package — bounded bill


class Capability(str, Enum):
    FAST = "fast"                  # cheap general / extraction / classification
    STRONG = "strong"             # reasoning / debate / editor
    REASONING = "reasoning"       # explicit deep-think tier
    LONG_CONTEXT = "long_context"
    CHEAP_BULK = "cheap_bulk"     # high-volume extraction / search economics


class Status(str, Enum):
    ACTIVE = "active"
    PREVIEW = "preview"
    DEPRECATED = "deprecated"


@dataclass(frozen=True)
class Provider:
    id: str                       # our id: deepseek | anthropic | openai | zhipu | moonshot
    litellm_prefix: str           # how LiteLLM addresses it (model ids already carry it)
    key_env: str                  # env var holding the token-billed API key
    api_base: str | None = None   # OpenAI-compatible base (GLM/Kimi); None = LiteLLM default
    sub_key_env: str | None = None    # env var for the flat/subscription key (None = no plan)
    sub_api_base: str | None = None   # subscription endpoint base (None = reuse api_base)


@dataclass(frozen=True)
class ModelSpec:
    id: str                       # registry key, e.g. "glm-4.6-sub"
    provider: str                 # Provider.id
    litellm_model: str            # full id passed to litellm.completion (incl. prefix)
    capabilities: tuple[Capability, ...]
    billing: Billing
    price_in: float = 0.0         # $/1M input tokens (TOKEN only; 0 for subscription)
    price_out: float = 0.0        # $/1M output tokens
    context_window: int = 0
    max_output: int = 8192
    supports_json: bool = True
    supports_reasoning: bool = False
    status: Status = Status.ACTIVE
    preferred: bool = False        # the chosen model for its (provider, primary capability)
    released: str = ""             # ISO date — audit + 换代 ordering
    notes: str = ""
    executor: str = "litellm"      # "litellm" (HTTP) | "agent_sdk" (Claude Max) | "codex_cli" (ChatGPT/Codex sub)


# --- Providers -------------------------------------------------------------
# GLM (Zhipu) and Kimi (Moonshot) are OpenAI-compatible, so they ride LiteLLM's
# generic openai/ path with an explicit api_base (most robust across LiteLLM
# versions). Their flat "subscription/coding-plan" is a distinct key (sub_key_env)
# and optionally a distinct base (settable via config); see llm._build_kwargs.
PROVIDERS: dict[str, Provider] = {
    "deepseek": Provider("deepseek", "deepseek/", "DEEPSEEK_API_KEY"),
    "anthropic": Provider("anthropic", "anthropic/", "ANTHROPIC_API_KEY"),
    "openai": Provider("openai", "openai/", "OPENAI_API_KEY"),
    "zhipu": Provider("zhipu", "openai/", "GLM_API_KEY",
                      api_base="https://open.bigmodel.cn/api/paas/v4",
                      sub_key_env="GLM_SUB_API_KEY"),
    "moonshot": Provider("moonshot", "openai/", "MOONSHOT_API_KEY",
                         api_base="https://api.moonshot.cn/v1",
                         sub_key_env="MOONSHOT_SUB_API_KEY"),
    # minis 本地 ollama(RTX 3090)— OpenAI 兼容端点。host.docker.internal 在容器内由
    # compose extra_hosts 提供、在宿主由 /etc/hosts 同名映射,两侧同一 URL;特殊拓扑用
    # 设置 OLLAMA_API_BASE 覆盖(llm._SUB_BASE_ATTR)。key_env=sub_key_env 同名占位 key
    # (ollama 不校验)→ used_sub=True:usd=0、永不被预算帽跳过,与 GLM 订阅同纪律。
    "ollama": Provider("ollama", "openai/", "OLLAMA_API_KEY",
                       api_base="http://host.docker.internal:11434/v1",
                       sub_key_env="OLLAMA_API_KEY"),
}


# --- Models (the updatable library) ----------------------------------------
# Exact GLM/Kimi model ids move fast; update them here when a generation ships.
MODELS: list[ModelSpec] = [
    # DeepSeek — token-billed pool (default fast/strong; cheap_bulk fallback for bulk)
    ModelSpec("deepseek-v4-flash", "deepseek", "deepseek/deepseek-v4-flash",
              (Capability.FAST, Capability.CHEAP_BULK), Billing.TOKEN,
              0.28, 1.14, 128_000, supports_reasoning=True, preferred=True, released="2026-01"),
    ModelSpec("deepseek-v4-pro", "deepseek", "deepseek/deepseek-v4-pro",
              (Capability.STRONG, Capability.REASONING), Billing.TOKEN,
              0.60, 2.40, 128_000, supports_reasoning=True, preferred=True, released="2026-01"),
    ModelSpec("deepseek-chat", "deepseek", "deepseek/deepseek-chat",
              (Capability.FAST, Capability.CHEAP_BULK), Billing.TOKEN,
              0.27, 1.10, 64_000, status=Status.DEPRECATED, released="2024-06"),
    ModelSpec("deepseek-reasoner", "deepseek", "deepseek/deepseek-reasoner",
              (Capability.REASONING, Capability.STRONG), Billing.TOKEN,
              0.55, 2.19, 64_000, supports_reasoning=True, status=Status.DEPRECATED, released="2025-01"),
    # Anthropic — token-billed quality pool
    ModelSpec("claude-opus-4-8", "anthropic", "anthropic/claude-opus-4-8",
              (Capability.STRONG, Capability.REASONING, Capability.LONG_CONTEXT), Billing.TOKEN,
              5.0, 25.0, 200_000, released="2026-01"),
    ModelSpec("claude-haiku-4-5", "anthropic", "anthropic/claude-haiku-4-5",
              (Capability.FAST,), Billing.TOKEN, 1.0, 5.0, 200_000, released="2025-10"),
    ModelSpec("claude-sonnet-4-6", "anthropic", "anthropic/claude-sonnet-4-6",
              (Capability.STRONG, Capability.LONG_CONTEXT), Billing.TOKEN, 3.0, 15.0, 200_000),
    # Anthropic — MAX SUBSCRIPTION via the Claude Agent SDK (executor="agent_sdk").
    # Runs single-shot completions on the Max plan's OAuth login (~/.claude/.credentials.json),
    # zero per-token bill — the same "subscription, never metered" discipline as the GLM pool.
    # Host-only (needs the `claude` CLI + creds); llm skips them when agentsdk.available()=False
    # (e.g. in docker) and rotates to GLM/DeepSeek. ~6.5s/call → low-volume QUALITY tasks only;
    # they sit as a peer/fallback in the STRONG chains (prefer_billing=TOKEN keeps token leads).
    # Distinct litellm_model ("anthropic-max/…") avoids colliding with the token specs' index;
    # agentsdk derives the real model id from the part after "/".
    # litellm_model bare name is the registry id ('claude-opus-max'), NOT the real Anthropic
    # model — so the PRICES/_BY_LITELLM bare-name index can't collide with (and zero the price
    # of) the metered claude-opus-4-8/sonnet specs. agentsdk._real_model() maps id → real model.
    # No Capability.FAST: a 6.5s subprocess must never lead the FAST chains (analyst/judge/eval).
    ModelSpec("claude-opus-max", "anthropic", "anthropic-max/claude-opus-max",
              (Capability.STRONG, Capability.REASONING, Capability.LONG_CONTEXT),
              Billing.SUBSCRIPTION, 0.0, 0.0, context_window=200_000, supports_reasoning=True,
              released="2026-07", executor="agent_sdk",
              notes="Claude Opus 4.8 on the Max subscription via Agent SDK; host-only, usd=0"),
    ModelSpec("claude-sonnet-max", "anthropic", "anthropic-max/claude-sonnet-max",
              (Capability.STRONG, Capability.LONG_CONTEXT),
              Billing.SUBSCRIPTION, 0.0, 0.0, context_window=200_000,
              released="2026-07", executor="agent_sdk",
              notes="Claude Sonnet on the Max subscription via Agent SDK; host-only, usd=0"),
    # OpenAI — CHATGPT/CODEX SUBSCRIPTION via the Codex CLI (executor="codex_cli").
    # Single-shot completions via `codex exec` on the ChatGPT Plus/Pro OAuth (~/.codex/auth.json),
    # zero per-token bill — same "subscription, never metered" discipline as Claude-Max. A peer
    # candidate in the STRONG/REASONING chains (deep-research tasks) alongside claude-opus-max;
    # prefer_billing=TOKEN keeps token models leading, so it sits as a subscription peer/fallback
    # (pin CODEX_PIN to force it). Host-only + OFF by default (codex_enabled; ToS-sensitive) →
    # codex_cli.available() gates it, docker/undialed falls back to GLM/DeepSeek. No Capability.FAST:
    # a slow subprocess must never lead the FAST chains. litellm_model bare name = registry id
    # ('codex-sub'), NOT the real model — so the PRICES bare-name index can't collide with an
    # openai/ token spec; codex_cli._real_model() maps id → real model (config codex_model).
    ModelSpec("codex-sub", "openai", "codex-cli/codex-sub",
              (Capability.STRONG, Capability.REASONING, Capability.LONG_CONTEXT),
              Billing.SUBSCRIPTION, 0.0, 0.0, context_window=256_000, supports_reasoning=True,
              released="2026-07", executor="codex_cli",
              notes="GPT-5.x on the ChatGPT/Codex subscription via `codex exec`; host-only, usd=0"),
    # GLM (Zhipu) — SUBSCRIPTION / Coding Plan: the bulk + search default pool.
    # price_in/out = the per-token LIST rate, used ONLY if the call falls back to the
    # metered key (no sub key configured); on the flat plan the recorded usd is 0.
    # GLM-5.2 = the current Coding Plan model (5h-window/weekly quota, NO overage bill);
    # the quota-aware resident worker (orchestration/glm_worker.py) pins to it.
    ModelSpec("glm-5.2-sub", "zhipu", "openai/glm-5.2",
              (Capability.CHEAP_BULK, Capability.FAST, Capability.STRONG, Capability.REASONING),
              Billing.SUBSCRIPTION, 0.60, 2.20, context_window=200_000,
              supports_reasoning=True, preferred=True, released="2026-06",
              notes="GLM Coding Plan flat-rate (GLM-5.2); z.ai 国际版订阅支持;quota-windowed, zero overage risk"),
    ModelSpec("glm-4.6-sub", "zhipu", "openai/glm-4.6",
              (Capability.CHEAP_BULK, Capability.FAST, Capability.STRONG), Billing.SUBSCRIPTION,
              0.60, 2.20, context_window=200_000, supports_reasoning=True,
              released="2026-01",
              notes="GLM Coding Plan legacy model; fallback behind glm-5.2-sub"),
    # 本地 GLM-4-9B @ minis RTX 3090 / ollama(算力调度方案 §9 Phase 3)。glmworker 的
    # 本地优先头(XAR_GLM_WORKER_LOCAL_FIRST):本地零成本、co-located 零延迟;服务被
    # mlrun --exclusive 独占停机时连接即拒,llm.complete 轮转回云 GLM(抢占协议的消费端)。
    # litellm_model = ollama 侧派生模型 glm4-xar(FROM glm4:9b-chat-q4_K_M + num_ctx 16k,
    # VRAM ~8.5G,守推理 ≤10G 规则);9B 无 reasoning、走短超时(llm_local_timeout_s)。
    # capabilities=() = 不入任何默认路由链(price_in=0 会把它排进 CHEAP_BULK 链第二席,
    # 让 9B 本地模型静默接住 dagster/批量任务的回退流量 —— 越权)。仅供 glmworker 钉扎
    # (_fetchy_pin 前插)与 Fetchy 显式选用;pin 走 registry.get,不看 capabilities。
    ModelSpec("glm4-local", "ollama", "openai/glm4-xar",
              (), Billing.SUBSCRIPTION,
              0.0, 0.0, context_window=16_384, max_output=4096,
              released="2026-07",
              notes="GLM-4-9B q4_K_M @ minis 3090 (ollama);本地零成本,宕机自动回落云 GLM;仅钉扎/显式选用"),
    # Phase 4 赛马候选(算力调度方案 §9 Phase 4)。与 glm4-local 同纪律:capabilities=()
    # 不入默认链、SUBSCRIPTION usd=0、短超时自动继承(llm._build_kwargs 按 provider=="ollama")。
    # status=PREVIEW = 赛马期隔离:llm.pinned()/评测可驱动(registry.get 不看 status),但
    # model_usable() 拒绝 → 不进 Fetchy 目录、不可被 save_fetchy 选中、不领 _fetchy_pin 本地头。
    # 胜者晋升 ACTIVE(一行改动),败者删 spec + ollama rm;评测判定门见 bench/phase4/。
    ModelSpec("qwen35-local", "ollama", "openai/qwen35-xar",
              (), Billing.SUBSCRIPTION,
              0.0, 0.0, context_window=16_384, max_output=4096,
              status=Status.PREVIEW, released="2026-07",
              notes="Qwen3.5-9B Instruct q4_K_M @ minis 3090;赛马主推候选(C-Eval 88.2/IFEval ~91.5)"),
    ModelSpec("qwen3-local", "ollama", "openai/qwen3-xar",
              (), Billing.SUBSCRIPTION,
              0.0, 0.0, context_window=16_384, max_output=4096,
              status=Status.PREVIEW, released="2026-07",
              notes="Qwen3-8B 非思考 q4_K_M @ minis 3090;赛马保底候选"),
    ModelSpec("glm4-0414-local", "ollama", "openai/glm4-0414-xar",
              (), Billing.SUBSCRIPTION,
              0.0, 0.0, context_window=16_384, max_output=4096,
              status=Status.PREVIEW, released="2026-07",
              notes="GLM-4-9B-0414 q4_K_M @ minis 3090;赛马低迁移风险候选(现役直系升级)"),
    ModelSpec("qwen3-14b-local", "ollama", "openai/qwen3-14b-xar",
              (), Billing.SUBSCRIPTION,
              0.0, 0.0, context_window=16_384, max_output=4096,
              released="2026-07",
              notes="Qwen3-14B 非思考 q4_K_M @ minis 3090;赛马胜者(2026-07-19:ok 0.825/F1 0.224 "
                    "= 基线 2.6x,唯一 ok 率超云锚;VRAM 实测 11.17G,红线记 11.2G 用户裁定)"),
    # Kimi (Moonshot) — SUBSCRIPTION: bulk fallback + long-context
    ModelSpec("kimi-k2-sub", "moonshot", "openai/kimi-k2-0905-preview",
              (Capability.CHEAP_BULK, Capability.FAST, Capability.STRONG, Capability.LONG_CONTEXT),
              Billing.SUBSCRIPTION, 0.60, 2.50, context_window=256_000, released="2026-01",
              notes="Kimi subscription; bulk fallback + long-context"),
]

_BY_ID = {m.id: m for m in MODELS}
_BY_LITELLM: dict[str, ModelSpec] = {}
for _m in MODELS:                                   # index by full id AND bare name
    _BY_LITELLM[_m.litellm_model] = _m
    _BY_LITELLM.setdefault(_m.litellm_model.split("/")[-1], _m)

# Price table consumed by llm._price — derived from the registry (both the full
# litellm id and the bare model name resolve, since _provider_model strips/adds prefixes).
PRICES: dict[str, tuple[float, float]] = {}
for _m in MODELS:
    PRICES[_m.litellm_model] = (_m.price_in, _m.price_out)
    PRICES[_m.litellm_model.split("/")[-1]] = (_m.price_in, _m.price_out)


def get(model_id: str) -> ModelSpec | None:
    return _BY_ID.get(model_id)


def by_litellm(litellm_model: str) -> ModelSpec | None:
    return _BY_LITELLM.get(litellm_model) or _BY_LITELLM.get(_strip(litellm_model))


def _strip(s: str) -> str:
    return s if "/" not in s else s.split("/", 1)[-1]


def provider_of(model_or_provider: str) -> Provider | None:
    """Accepts a ModelSpec.id, a provider id, or a litellm model string."""
    if model_or_provider in PROVIDERS:
        return PROVIDERS[model_or_provider]
    spec = get(model_or_provider) or by_litellm(model_or_provider)
    return PROVIDERS.get(spec.provider) if spec else None


# --- runtime override (route_overrides table), TTL-cached ------------------
_OVERRIDES: dict[str, str] = {}
_OVERRIDES_AT: float = 0.0
_OVERRIDES_TTL = 60.0  # seconds — a live ops switch propagates within this window


def _overrides() -> dict[str, str]:
    """{override_key -> model_id} from the route_overrides table, TTL-cached.
    Never raises (a missing table / DB hiccup just yields no overrides)."""
    global _OVERRIDES, _OVERRIDES_AT
    now = time.monotonic()
    if now - _OVERRIDES_AT < _OVERRIDES_TTL:
        return _OVERRIDES
    try:
        from ..storage import db
        rows = db.query("SELECT key, model_id FROM route_overrides")
        _OVERRIDES = {r["key"]: r["model_id"] for r in rows if get(r["model_id"])}
        _OVERRIDES_AT = now
    except Exception:  # noqa: BLE001 — overrides are best-effort
        # serve-stale-until-recover: on a transient DB hiccup keep the LAST good overrides
        # and do NOT advance the timestamp, so the next call retries immediately. Caching an
        # empty dict for a full TTL would silently disable a live ops route switch for 60s.
        pass
    return _OVERRIDES


def refresh_overrides() -> None:
    """Force the next _overrides() call to re-read (called after an ops write)."""
    global _OVERRIDES_AT
    _OVERRIDES_AT = 0.0


def override_for(*keys: str) -> ModelSpec | None:
    """The override ModelSpec for the first matching key (e.g. a task_class then its
    capability), or None."""
    ov = _overrides()
    for k in keys:
        if k in ov:
            return get(ov[k])
    return None


def candidates_for(capability: Capability, *, billing_pref: str = "any",
                   status: Status = Status.ACTIVE) -> list[ModelSpec]:
    """Active models with `capability`, ordered as a fallback chain:
    preferred-billing first (NOT dropping the others — they are the fallback tail),
    then `preferred` flag, then cheaper input price. Deterministic."""
    cands = [m for m in MODELS if m.status == status and capability in m.capabilities]

    def sort_key(m: ModelSpec) -> tuple:
        billing_rank = 0 if (billing_pref != "any" and m.billing.value == billing_pref) else 1
        return (billing_rank, 0 if m.preferred else 1, m.price_in, m.id)

    return sorted(cands, key=sort_key)


def preferred(provider: str, capability: Capability) -> ModelSpec | None:
    for m in MODELS:
        if m.provider == provider and m.preferred and capability in m.capabilities \
                and m.status == Status.ACTIVE:
            return m
    return None


def configured_providers(key_present) -> list[dict]:
    """For the ops console: each provider + whether its key is set (key_present is a
    callable env_name -> bool). Pure data + the injected predicate (no config import)."""
    out = []
    for p in PROVIDERS.values():
        models = [m.id for m in MODELS if m.provider == p.id and m.status != Status.DEPRECATED]
        out.append({"id": p.id, "keyEnv": p.key_env, "subKeyEnv": p.sub_key_env,
                    "configured": bool(key_present(p.key_env)),
                    "subConfigured": bool(p.sub_key_env and key_present(p.sub_key_env)),
                    "models": models})
    return out
