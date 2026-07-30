"""Central configuration. Everything is env-driven so the platform is turnkey:
copy `.env.example` -> `.env`, set ANTHROPIC_API_KEY, and run."""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="XAR_", extra="ignore", case_sensitive=False
    )

    # --- LLM (read provider keys from their conventional env names) ---
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    deepseek_api_key: str = Field(default="", validation_alias="DEEPSEEK_API_KEY")
    # Two-tier routing. Default provider = DeepSeek V4: flash (general/extraction)
    # + pro (reasoning/debate). Override via XAR_MODEL_FAST/STRONG for any
    # LiteLLM-supported model (e.g. claude-haiku-4-5 / claude-opus-4-8).
    model_fast: str = "deepseek/deepseek-v4-flash"
    model_strong: str = "deepseek/deepseek-v4-pro"
    model_bulk: str = ""  # bulk/search default; blank => router uses registry subscription preferred (GLM/Kimi)
    # 动态路由:按输入复杂度(prompt 规模)× 内容相关性(价值)在能力层间升降模型。开=智能选层
    # (简单/大批量→便宜/本地、复杂/高价值→强云),关=退回静态 per-task 路由(向后兼容)。
    dynamic_routing_enabled: bool = Field(default=True, validation_alias="XAR_DYNAMIC_ROUTING")
    dynamic_routing_chars_high: int = 16_000   # prompt 超此字符 → 判为复杂(升层)
    dynamic_routing_chars_low: int = 1_200     # prompt 短于此 → 判为简单(强任务可降层省成本)
    # 推理力度。默认**最大化**:强/推理层任务(chat/debate/editor/synth/audit/earnings/phanny)
    # 一律 "high" —— OpenAI 兼容 reasoning_effort 枚举的最高档。「视任务需求而实际调用」由两处保证:
    #   ① 调用方显式传 reasoning_effort 压过默认(thesis 量产走 low、phanny 显式 high);
    #   ② 非强层(bulk/triage:kg_extract/expert/thesis/wechat_triage)吃 model_effort_bulk 的低档
    #      —— 小 token 预算下思考会烧光 content(Phase 4 实测 k3 30/40 空),不能一刀切拉满。
    model_effort: str = "high"
    model_effort_bulk: str = "low"
    llm_max_usd_per_run: float = 5.0  # hard budget cap per report run
    llm_max_usd_per_batch: float = 20.0  # cap for batch jobs (build_kg/expert/synthesize)
    # GLM (Zhipu) + Kimi (Moonshot): OpenAI-compatible. A token key plus an optional flat
    # subscription / coding-plan key (and base) used to route bulk/search OFF the metered bill.
    glm_api_key: str = Field(default="", validation_alias=AliasChoices("GLM_API_KEY", "ZHIPU_API_KEY", "ZHIPUAI_API_KEY"))
    moonshot_api_key: str = Field(default="", validation_alias=AliasChoices("MOONSHOT_API_KEY", "KIMI_API_KEY"))
    glm_sub_api_key: str = Field(default="", validation_alias="GLM_SUB_API_KEY")
    glm_sub_api_base: str = Field(default="", validation_alias="GLM_SUB_API_BASE")
    moonshot_sub_api_key: str = Field(default="", validation_alias="MOONSHOT_SUB_API_KEY")
    moonshot_sub_api_base: str = Field(default="", validation_alias="MOONSHOT_SUB_API_BASE")
    # MiniMax:token key(chat API)+ coding-plan 订阅(sub key + coding 端点,与 GLM z.ai 同型)。
    minimax_api_key: str = Field(default="", validation_alias=AliasChoices("MINIMAX_API_KEY", "MINIMAXI_API_KEY"))
    minimax_sub_api_key: str = Field(default="", validation_alias="MINIMAX_SUB_API_KEY")
    minimax_sub_api_base: str = Field(default="", validation_alias="MINIMAX_SUB_API_BASE")
    # --- 本地 LLM(minis ollama @ RTX 3090;hardware-solutions/minis-算力调度方案.md §9)---
    # OLLAMA_API_KEY 是占位 key(ollama 不校验),但必须非空 —— key 在场性即"本地已配"开关,
    # 复用 model_usable/complete 的既有 key 门。base 默认走 registry(host.docker.internal),
    # 特殊拓扑用 OLLAMA_API_BASE 覆盖。glm_worker_local_first=true 时工人钉扎链前插 glm4-local
    # (见 glm_worker._fetchy_pin);端点不可达(如 mlrun --exclusive 停机)由候选轮转自动回落云 GLM。
    ollama_api_key: str = Field(default="", validation_alias="OLLAMA_API_KEY")
    ollama_api_base: str = Field(default="", validation_alias="OLLAMA_API_BASE")
    glm_worker_local_first: bool = True    # 默认开:抽取第一顺位=本地 Qwen(零成本/不限流/不饱和),GLM 订阅仅作回退
    glm_worker_local_model: str = "qwen3-14b-local"  # 本地头 registry id(minis ollama qwen3-14b-xar);换代=改 env 重建,零代码
    llm_local_timeout_s: int = 180         # 本地候选 per-call 超时(防挂死;连接拒绝本就秒败→轮转)
    # Claude Max subscription via the Agent SDK (executor="agent_sdk"). Zero per-token bill —
    # runs on the Max plan's OAuth login. Host-only (needs the `claude` CLI + ~/.claude creds);
    # agentsdk.available() gates it, so a docker container without them silently falls back to GLM.
    anthropic_max_enabled: bool = True         # off → Claude-Max specs never route (pure GLM/token)
    anthropic_max_model: str = "claude-opus-4-8"   # default model for the claude-opus-max spec
    anthropic_max_effort: str = "high"         # Agent SDK effort for quality tasks
    anthropic_max_timeout_s: int = 180         # per-call subprocess timeout (single-shot)

    # --- OpenAI Codex CLI subscription (executor="codex_cli") ---
    # The `codex-sub` spec runs single-shot completions via the Codex CLI (`codex exec`) on the
    # ChatGPT Plus/Pro subscription OAuth (~/.codex/auth.json) — zero per-token bill, same
    # "subscription only, never metered" discipline as Claude-Max/GLM. Host-only (needs the
    # `codex` CLI + login); codex_cli.available() gates it → docker falls back to GLM/token.
    # OFF by default: the ChatGPT subscription is intended for interactive Codex use, so driving
    # it as a headless research-model backend is off-label (low-volume, quality tasks only) — arm
    # deliberately with XAR_CODEX_ENABLED=true. See [[deployment-provider-arming]].
    codex_enabled: bool = False                # off → codex-sub never routes (opt-in, ToS-sensitive)
    codex_model: str = "gpt-5.5"               # model id passed to `codex exec -m` (subscription)
    codex_effort: str = "high"                 # model_reasoning_effort for quality tasks
    codex_timeout_s: int = 600                 # per-call subprocess timeout (gpt-5.5 xhigh is slow)

    # --- Earnings event-trading (ET; US-only pre-earnings long/short verdicts) ---
    earnings_watch_days: int = 10              # 观察窗:财报前 N 天进入每日刷新
    earnings_verdict_lead_days: int = 3        # T-N 生成正式裁决(之后锁定)
    earnings_outcome_max_days: int = 5         # 盘后回验兜底收尾天数
    earnings_universe_cap: int = 50            # universe 截断帽
    earnings_verdict_host_only: bool = False   # True → docker worker 裁决 deferred,host 专跑

    # --- Phanny (季报多空事件交易:强制 long/short + conviction 1-10 组合正态 + size 1-15%) ---
    phanny_universe_cap: int = 40              # PHANNY_UNIVERSE ∩ registry 截断帽
    # 覆盖范围(2026-07-29 用户裁定:扩到 Genny 全覆盖库)。
    #   "list"     —— 沿用策展的 EARNINGS_UNIVERSE(~31 只美股旗舰,历史行为);
    #   "registry" —— **全部注册公司**,由数据可得性自然把关(dossier n_facts<4 → no_data、
    #                 无财报日历行直接跳过),每次跳过都进 build_rejections 台账,可用
    #                 `xar phanny why <cid>` 查为何没产出。
    # ET 的 EARNINGS_UNIVERSE **不动** —— 与 conviction 刻度一样,两个模块的 universe 也隔离。
    phanny_universe_mode: str = "registry"
    # 每轮 book 最多裁决多少家。全库模式下财报季会有大量公司同时进窗,而单名完整辩论约
    # 40 次订阅调用 —— 没有这道闸,一次 book 就能把三家订阅额度吃干、饿死 thesis 重建与
    # link 道。worklist 按财报临近度排序,先做最紧迫的,其余下一轮继续(裁决本就幂等加锁)。
    phanny_book_max_per_cycle: int = 12
    # 整本 book 的**墙钟预算**(秒;0=不限)。2026-07-29 加入:phanny 是 glm_worker.run_once
    # 的最后一个阶段,而拉取是第一个 —— 单线程下 phanny 超时多久,下一轮拉取就冻结多久
    # (实测冻结 3.5 小时、全库零新文档)。名次上限(phanny_book_max_per_cycle)挡不住这个:
    # 订阅模型实测 49~124 秒/次(glm-5.2 101s / k3 124s / minimax 49s),单名 N critic ×
    # max_rounds 轮就是半小时起。到点不再开新名,其余按既有 cap 的同一套语义顺延下轮。
    phanny_book_max_seconds: int = 1800
    phanny_watch_days: int = 45                # 观察窗:窗内出财报的选中名进入 book
    phanny_verdict_lead_days: int = 3          # T-N 生成正式裁决
    phanny_outcome_max_days: int = 5           # 盘后回验兜底收尾天数
    phanny_debate_max_rounds: int = 5          # 单名多 critic 辩论收敛上限(到顶补数据,非降 conviction)
    phanny_convergence_conv_delta: float = 1.0  # 收敛:近轮 conviction 变动阈
    phanny_convergence_size_delta: float = 1.5  # 收敛:近轮 size 变动阈(pp)
    phanny_max_book_passes: int = 2            # 组合正态不达标时的 REDEBATE 轮上限
    phanny_ensemble_mean_lo: float = 4.5       # 组合 conviction 均值下界(禁全低聚集)
    phanny_ensemble_mean_hi: float = 6.5       # 均值上界(禁全高聚集)
    phanny_ensemble_sigma_min: float = 1.5     # 组合 conviction 标准差下界(需区分度)
    phanny_ensemble_high_ratio: float = 0.10   # 高信念(≥7)占比下界(高端非空)
    phanny_gross_cap_pct: float = 150.0        # 组合总敞口帽(超限按比例缩 size,不动 conviction)
    phanny_verdict_host_only: bool = False     # True → docker worker 裁决 deferred,host 专跑
    # 异厂商 critic 钉扎头;**仅订阅模型**(2026-07-25 用户裁定:Phanny 移除 deepseek,只用订阅项下
    # minimax/kimi/glm)—— 按 token 计费的 deepseek-v4-pro 曾在 propose/rebut/critic 烧 ~$7.6/天,
    # 与「订阅额度充分利用、零计量支出」目标冲突。相邻轮换厂商仍保证多 LLM 对抗。
    phanny_challenger_models: str = "glm-5.2-sub,kimi-k3-sub,minimax-m3-sub"
    # proposer/rebut(深度研究主力)在无 host 订阅执行器(docker)时的**订阅**钉扎链:
    # MiniMax-M3(1M 上下文/40k 输出) → Kimi-K3(256k/16k) → GLM-5.2(200k/32k),均 usd=0。
    phanny_primary_models: str = "minimax-m3-sub,kimi-k3-sub,glm-5.2-sub"

    # --- Chathy(交互式工具调用聊天)---
    # **精确有序**模型链(csv,registry id;2026-07-29 用户裁定):Kimi-K3 领衔 → GLM-5.2 →
    # MiniMax-M3 → DeepSeek-V4-Pro。前三席为订阅(usd=0),尾席 deepseek 按 token 计费兜底。
    # 与 phanny_* 同纪律:链外模型不参与,不掺 registry 候选 → 没有静默漂到计量模型的路径。
    # 空字符串 = 退回 TaskClass.CHAT 的常规策略路由。换代/回滚 = 改 XAR_CHAT_MODELS,零代码。
    chat_models: str = "kimi-k3-sub,glm-5.2-sub,minimax-m3-sub,deepseek-v4-pro"
    # 链上四席**全是思考型模型**,输出预算必须给足:reasoning 与 content 在 /v1 分离计费,
    # 4000 的旧预算会被思考吃光 → content 空(registry 对 glm-5.2/k3 的注释、Phase 4 实测
    # 30/40 空)。_build_kwargs 再按 spec.max_output 逐候选钳制(k3 16384 / glm-5.2 32768 /
    # m3 40960 / deepseek-v4-pro 8192),订阅席零边际成本。
    chat_max_tokens: int = 16_000

    # --- Embeddings ---
    # 默认英文 bge-small(turnkey);中英混合部署设 XAR_EMBED_MODEL=
    # sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2(384d,多语含中文)
    # 后 xar reembed 全库重嵌;最高质量可用 intfloat/multilingual-e5-large(1024d,慢)。
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384

    # --- Storage ---
    database_url: str = "postgresql://xar:xar@localhost:5432/xar"
    object_store: str = "file://./data/objects"

    # --- Ingestion: market-data providers (all optional; blank = skipped) ---
    edgar_identity: str = "xar-research research@example.com"
    finnhub_api_key: str = Field(default="", validation_alias="FINNHUB_API_KEY")
    fmp_api_key: str = Field(default="", validation_alias="FMP_API_KEY")
    # Massive (Polygon-compatible) — Fenny's primary live IV-surface / correlation source.
    massive_api_key: str = Field(default="", validation_alias="MASSIVE_API_KEY")
    polygon_api_key: str = Field(default="", validation_alias="POLYGON_API_KEY")
    tushare_token: str = Field(default="", validation_alias="TUSHARE_TOKEN")
    # Andy (src/slx macro module) — free-registration macro-data keys, bridged into the
    # vendored connectors by xar.api.andy_mount. All optional: sec_edgar/epoch_ai/fhfa/
    # lbnl/indeed_hiring_lab/bls/stooq run with zero keys.
    fred_api_key: str = Field(default="", validation_alias="FRED_API_KEY")
    bea_api_key: str = Field(default="", validation_alias="BEA_API_KEY")
    eia_api_key: str = Field(default="", validation_alias="EIA_API_KEY")
    ember_api_key: str = Field(default="", validation_alias="EMBER_API_KEY")
    acled_api_key: str = Field(default="", validation_alias="ACLED_API_KEY")
    acled_email: str = Field(default="", validation_alias="ACLED_EMAIL")
    ticketmaster_api_key: str = Field(default="", validation_alias="TICKETMASTER_API_KEY")
    slx_slack_webhook: str = Field(default="", validation_alias="SLX_SLACK_WEBHOOK")
    # X / Twitter. TwitterAPI.io (third-party) uses an X-API-Key; official X API v2
    # uses a bearer token. Set either; TwitterAPI.io is preferred when present.
    twitterapi_key: str = Field(
        default="", validation_alias=AliasChoices("TWITTERAPI_TOKEN", "TWITTERAPI_KEY"))
    # 可选 API token(ARCH P1-5;默认空=中间件关,零行为变化)。在位时变更类 /api/*
    # 请求须携带 X-API-Token 头(或 Bearer)——见 api/app.py:_api_token_gate。
    api_token: str = Field(default="", validation_alias="XAR_API_TOKEN")
    # X 数据源月度总限额(2026-07-20 用户裁定 $20/月;计量外部 API,全调用方共顶,
    # providers/twitter.py 咽喉记账+闸门;费率为 twitterapi.io 牌价估算,可按账单校准)
    x_monthly_budget_usd: float = 20.0     # ≤0 = 数据源禁用
    x_usd_per_1k_tweets: float = 0.15
    x_usd_per_request: float = 0.0002
    x_bearer_token: str = Field(default="", validation_alias="X_BEARER_TOKEN")
    reddit_client_id: str = Field(default="", validation_alias="REDDIT_CLIENT_ID")
    reddit_client_secret: str = Field(default="", validation_alias="REDDIT_CLIENT_SECRET")
    # Polymarket Gamma API is public (no key). Wind requires a local terminal.
    enable_wind: bool = False
    # Provider preference order for structured fundamentals/estimates/prices
    market_data_order: str = "fmp,finnhub,polygon,yahoo"
    # X (Twitter) expert handles to follow (CSV of @handles); blank = keyword-only.
    x_expert_handles: str = Field(default="", validation_alias="X_EXPERT_HANDLES")

    # --- Futu / moomoo OpenAPI (富途) — HK personal retail account -------------
    # The `futu` Python SDK talks to a local OpenD gateway daemon (default
    # 127.0.0.1:11111) that logs in with the account. OFF by default (turnkey-safe);
    # set XAR_ENABLE_FUTU=true + run OpenD to arm. In docker, point FUTU_OPEND_HOST at
    # the host (host.docker.internal or the host LAN IP) since OpenD runs on the host.
    futu_host: str = Field(default="127.0.0.1", validation_alias="FUTU_OPEND_HOST")
    futu_port: int = Field(default=11111, validation_alias="FUTU_OPEND_PORT")
    enable_futu: bool = False
    futu_news_per_stock: int = 10          # get_search_news items per stock per pull
    futu_flow_lookback_days: int = 90      # capital-flow history window
    # --- Gangtise 投研 Open API (open.gangtise.com) — CN sell-side research -----
    # AccessKey/SecretKey → temporary Bearer token (loginV2). Deep fundamental research:
    # financials/valuation/一致预期/主营构成/股东 + 投研文本 (one-pager/线索/KB/研报).
    # OFF unless keys set + enable_gangtise (turnkey-safe). Apply at open-platform.gangtise.com.
    gts_access_key: str = Field(default="", validation_alias="GTS_ACCESS_KEY")
    gts_secret_key: str = Field(default="", validation_alias="GTS_SECRET_KEY")
    enable_gangtise: bool = False
    gangtise_forecast_years: int = 3       # analyst-consensus fiscal-year horizon to pull
    # 非标语义抓取(open-insight 研报/纪要 + MD&A;保守只存摘要,零下载信用)
    gangtise_insight_pages: int = 2        # list 端点每次翻页数(页≤50)
    gangtise_insight_hours: int = 24       # fresh_sweep 节拍(每日刷新)
    gangtise_backfill_units: int = 2       # 每轮回填的 (doc_type,月窗) 单元数
    gangtise_history_months: int = 12      # 研报/纪要回填目标深度(受账户可见窗自适应)
    gangtise_history_quarters: int = 8     # MD&A 历史季度深度(不受账户窗限制)
    gangtise_core_size: int = 30           # 核心公司数(种子∩CN ∪ 覆盖度 top-N)

    # --- Chathy 通讯软件通道:Telegram bot(长轮询,无公网 webhook 依赖)---------
    # BOT_HTTP_API = BotFather 的 HTTP API token;BOT_ID = bot 用户名(展示用)。
    # token 即军备开关:设了就随 app 启动(XAR_ENABLE_TELEGRAM=false 可显式关)。
    # TELEGRAM_ALLOWED_CHATS = 逗号分隔 chat id 白名单;留空 = 不限(私人 bot 假设),
    # 每个新 chat 首次进线会在日志打出 chat id,便于随后收紧白名单。
    telegram_bot_token: str = Field(
        default="", validation_alias=AliasChoices("BOT_HTTP_API", "TELEGRAM_BOT_TOKEN"))
    telegram_bot_id: str = Field(
        default="", validation_alias=AliasChoices("BOT_ID", "TELEGRAM_BOT_ID"))
    telegram_allowed_chats: str = Field(default="", validation_alias="TELEGRAM_ALLOWED_CHATS")
    enable_telegram: bool = Field(default=True, validation_alias="XAR_ENABLE_TELEGRAM")

    # --- AIFINmarket (万得终端) — CN A-share professional source ---------------
    # REST gateway to a Wind/AIFINmarket terminal (base url + token); or set
    # XAR_ENABLE_AIFINMARKET + a local WindPy terminal. Blank -> skipped.
    aifinmarket_base_url: str = Field(default="", validation_alias="AIFINMARKET_BASE_URL")
    aifinmarket_token: str = Field(default="", validation_alias="AIFINMARKET_TOKEN")
    enable_aifinmarket: bool = False
    # Multi-account subscription pool: numbered tokens AIFINMARKET1_TOKEN..N (all
    # subscription seats share identical MCP permissions) round-robin so every seat's
    # daily quota is used, not just one. `.env` is passed to containers via env_file,
    # so numbered vars land in os.environ after a recreate.
    aifinmarket_max_accounts: int = 32          # how many AIFINMARKET{i}_TOKEN slots to scan
    aifinmarket_daily_calls_per_account: int = 0  # per-seat/day safety cap (0 = unlimited)
    aifinmarket_news_top_k: int = 5             # docs/query for industry/strategy/macro dims
    aifinmarket_company_top_k: int = 2          # docs/company for the (bulk) company dim — kept
                                                # low so全库 universe 入流不压垮本地 qwen 抽取吞吐
    aifinmarket_min_interval_seconds: float = 0.3  # dispatcher throttle between MCP calls
    # Company-dim sweep is sharded across N runs so no single run floods the queue /
    # blocks the worker's extraction batch. At the aifin_research 4h cadence, N=6 →
    # full universe covered every 24h (~1/6 of companies per run). Industry/strategy/
    # macro dims run every sweep (deduped by doc_id).
    aifinmarket_company_shards: int = 6  # DEPRECATED(旧 aifin_research 分片站点;已由 fetch_chain 取代,保留防 env 破裂)

    # --- Alpha派 (AlphaPai, 讯兔科技投研 SaaS) — CN/HK/US 投研另类数据源 -----------
    # open-api.rabyte.cn,header `app-agent: <key>`。recall-data(纪要/研报/点评/公告/三方研报)+
    # stock/agent(公司一页纸/投资逻辑)→ documents(source='alphapai')。key 空 -> 跳过。
    alphapai_api_key: str = Field(default="", validation_alias="ALPHAPAI_API_KEY")
    alphapai_base_url: str = Field(default="https://open-api.rabyte.cn",
                                   validation_alias="ALPHAPAI_BASE_URL")
    enable_alphapai: bool = True
    # 全召回类型(信息密度序):内资纪要/IR纪要/美股纪要/研报/外资研报/三方研报/点评/公告/社媒/基金报告/问答。
    # 2026-07-26 补上此前漏抓的 roadShow_ir(IR 纪要)/vps(基金报告)/qa —— provider 的 _DOCTYPE_MAP 全支持。
    alphapai_recall_types: str = ("roadShow,roadShow_ir,roadShow_us,report,foreign_report,"
                                  "third_report,comment,ann,social_media,vps,qa")
    alphapai_lookback_days: int = 30            # recall 只取近 N 天(取 FRESH 内容)
    alphapai_company_shards: int = 6            # DEPRECATED(旧 alphapai_research 分片站点;已由 fetch_chain 取代,保留防 env 破裂)
    alphapai_agent_modes: str = "2,7"           # 核心公司拉的 agent 模式:2=公司一页纸 7=投资逻辑
    alphapai_minutes_types: str = "roadShow,roadShow_ir,roadShow_us"  # 纪要专用召回类型(fetch_chain 首要固定任务)
    alphapai_backoff_seconds: int = 900         # 204(系统繁忙)退避秒数(非当日耗尽)
    # 短窗限流(未文档化 code 42900 ≈ HTTP 429)治理。实测:连打 1~4 次即触发、恢复 ≈10s、4s 间隔
    # 仍失败 → 可持续速率约 1 次/10s。此前该码不被识别,pull_recall 静默返回 0,是 alphapai 量上不去
    # 的真正瓶颈。节流取 11s(留 1s 余量);命中后按 12s 退避重试。
    # 节流。**回调到 20s**:13s 实测把 42900 从 6 次/6h 推到 31 次/12h,触发 fetch_chain 的 3 连击
    # backoff_giveup —— alphapai 与 alphapai_backfill 两段直接被跳过、回溯段一直没机会跑(得不偿失)。
    # 20s 下 42900 极少,段能跑满,才是真正的"更快"。
    alphapai_min_interval_seconds: float = 20.0
    # 命中 42900 后的退避:60s 让令牌桶回满。**只重试 1 次** —— 连打 3 次进已发怒的限流器会阻碍恢复,
    # 剩下的重试交给链路下一拍(300s 后),那时桶早已回满。
    # 退避 60s→25s:实测短窗恢复仅 ~10s,60s 过长会让一个时间片空耗、并更快累积弃权连击。
    alphapai_ratelimit_sleep_seconds: int = 25
    alphapai_ratelimit_retries: int = 1
    # 弃权连击阈值(原硬编码 3)。alphapai 是本链的**目标源**,偶发限流不该让整段被跳过 →
    # 放宽到 6:限流期段内暂停等待,而非把额度让给后面的源;仍保留"病态供应商不拖死整天"的保护。
    fetch_chain_backoff_strikes: int = 6

    # --- 另类语义抓取链 (orchestration/fetch_chain.py) — 相关性×额度紧迫接力调度 --------
    # alphapai纪要 → gangtise → aifinmarket → alphapai agent(尾),每日按序接力:某源当日额度
    # 耗尽(alphapai 203/aifinmarket 全席位冷却)或清单跑完(gangtise 无额度信号)即 fallback 下一源。
    # 相关性 = universe_priority_order(种子辩题公司 → coverage 综合分降序);新→旧 = recall startTime 窗。
    fetch_chain_enabled: bool = True
    # CSV;未来源追加于此。alphapai_backfill 紧随 fresh alphapai 之后 —— 当日新发布的纪要/研报永远先抓,
    # 再用过去一年的逐窗回溯把 GPU 填满(目标:alphapai 独占本地算力)。
    fetch_chain_order: str = ("alphapai,alphapai_backfill,gangtise,aifinmarket,alphapai_agents")
    fetch_chain_step_seconds: int = 300         # 站点节拍(worker cycle=180s → 约每 2 轮一步)
    fetch_chain_slice_seconds: int = 75         # 每步 wall-time 预算(item 之间检查,不抢占单个慢调用)
    fetch_chain_refetch_days: int = 3           # 首轮全扫后 recall 窗口(doc_id upsert 幂等,重叠无害)
    # tier-3 非纪要 recall 覆盖公司数(0=全库)。2026-07-27 由 0 收到 40:实测 fresh 段 960 项 ×20s
    # 需 5.3h 才跑完,把真正产新数据的**回溯段**堵在后面(6h 内没轮到);而 rest 扫的"其余类型"与回溯窗
    # 高度重复(6h 内 ~1000 次调用只落 54 篇新文档 = 绝大多数是幂等重复)。收窄后 fresh ≈ 550 项,
    # 约 2h 进入回溯段 —— 把额度让给真正有增量的窗口。
    fetch_chain_alphapai_rest_top: int = 40
    # 主题维前置:把 76 条主题词(行业/策略/宏观/资金流)排在纪要之前。主题项少(~76×13s≈17min)、
    # 却是当前唯一 0 产出的维度,前置能让 macro/strategy/moneyflow 立刻开始落库;纪要仍优先于 rest 与回溯。
    fetch_chain_alphapai_theme_first: bool = True
    # 日内滚动重跑:整条链跑完后隔 N 秒重开一轮,alphapai 全天持续抓白天新发布的纪要(捕捉时效内容),
    # 直到当日额度耗尽(203)后 alphapai 段自动秒跳过。0 = 关闭(跑完即空转到次日)。
    fetch_chain_repoll_seconds: int = 3600
    fetch_chain_agent_companies: int = 30       # 尾段 agent 合成的公司数(CN A 股)
    fetch_chain_aifin_chunk: int = 25           # 万得公司维每 work-item 公司数
    fetch_chain_gangtise_chunk: int = 10        # gangtise broker/MD&A 每 work-item 公司数
    # --- alphapai 过去一年逐窗回溯(量的主杠杆;目标:让 alphapai 吃满本地 GPU)---------------
    # recall 的 startTime/endTime 实测真按窗过滤:不带窗一个 query 只回 ~28 篇(跨整年),按月切窗
    # 则每窗各回 ~20 篇 → 12 窗 × 全库公司 + 66 条主题词,覆盖量放大一个量级。窗口**新→旧**依次走,
    # 游标在 kvstate 'alphapai_bf';走完 backfill_days 即自然停(此后由 fresh 段维持日增)。
    alphapai_backfill_enabled: bool = True
    alphapai_backfill_days: int = 365           # 回溯深度(过去一年)
    alphapai_backfill_window_days: int = 30     # 单窗宽度(月窗;越窄回得越多、调用也越多)
    # 主题维覆盖面(复用 aifin_catalog 词表):行业 32 + 策略 10 + 宏观 12 + 资金流 12 = 66 条。
    # Andy 宏观/市场策略 与 Moneyflow(北向/两融/ETF申赎/期权情绪…)的**定性研判文本**由此进 documents。
    alphapai_theme_dims: str = "industry,strategy,macro,moneyflow"

    @property
    def aifinmarket_tokens(self) -> list[str]:
        """Ordered, de-duplicated subscription-token pool. Reads numbered seats
        AIFINMARKET{1..max}_TOKEN from the process env plus the legacy single
        AIFINMARKET_TOKEN, dropping blanks/dupes. Tests monkeypatch os.environ."""
        toks: list[str] = []
        for i in range(1, self.aifinmarket_max_accounts + 1):
            v = (os.environ.get(f"AIFINMARKET{i}_TOKEN") or "").strip()
            if v:
                toks.append(v)
        legacy = (self.aifinmarket_token or os.environ.get("AIFINMARKET_TOKEN") or "").strip()
        if legacy:
            toks.append(legacy)
        seen: set[str] = set()
        out: list[str] = []
        for t in toks:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    # --- WeChat Official Accounts (微信公众号) via a we-mp-rss service ---------
    # Self-hosted https://github.com/rachelos/we-mp-rss exposes public feed
    # endpoints. Blank base url -> the connector is skipped (turnkey-safe).
    werss_base_url: str = Field(default="", validation_alias="WERSS_BASE_URL")
    werss_api_token: str = Field(default="", validation_alias="WERSS_API_TOKEN")
    # optional CSV of feed ids; blank = aggregated /rss
    werss_feeds: str = Field(default="", validation_alias="WERSS_FEEDS")
    # optional JSON {feed_id: company_id}
    werss_feed_map: str = Field(default="", validation_alias="WERSS_FEED_MAP")
    werss_max_items: int = Field(default=50, validation_alias="WERSS_MAX_ITEMS")

    # --- WeChat 全网发现 (混合漏斗: 本体种子搜索 → 抓取 → 高产号晋升订阅) ------------
    # 薄连接器消费一个自托管的搜索服务(we-mp-rss 内置搜索 / wechat-download-api 等),
    # XAR 只调它的 HTTP 搜索接口。反爬留在外部服务里。默认关(新脆弱路径,显式 opt-in,
    # 与 twitter 默认关同纪律);base url 为空 → 发现连接器 no-op。
    wechat_search_base_url: str = Field(default="", validation_alias="WECHAT_SEARCH_BASE_URL")
    wechat_search_api_token: str = Field(default="", validation_alias="WECHAT_SEARCH_API_TOKEN")
    wechat_discover_enabled: bool = Field(default=False, validation_alias="XAR_WECHAT_DISCOVER_ENABLED")
    wechat_discover_queries_per_run: int = 40   # 每轮跑的查询数(游标轮转,避免打爆反爬)
    wechat_discover_max_articles: int = 320     # 每轮抓取正文的文章上限(成本闸;广度优先收号后上调)
    wechat_discover_min_chars: int = 200        # 正文短于此 → 跳过(图片/视频号,triage 也会地板掉)
    wechat_discover_lookback_days: int = 14     # 搜索只要近 N 天(高信噪、避免历史回填灌库)
    # 晋升门(混合):WCDA 发现证明的高信噪号 → 晋升为 we-mp-rss 订阅做长期稳定轮询。
    #   keep_rate>=auto_keep_rate 且 >=min_articles → 自动订阅;min_keep_rate..auto_keep_rate → HITL 待批。
    wechat_promote_min_articles: int = 5        # 该号至少发现过 N 篇(已 triage)才够格晋升(证据下限)
    wechat_promote_min_keep_rate: float = 0.5   # HITL 下限:>= 此值才入晋升漏斗(0.5~auto 进人工待批)
    wechat_promote_auto_keep_rate: float = 0.7  # 自动订阅线:keep_rate >= 此值直接订阅(其余进 HITL 队列)
    wechat_promote_max_per_day: int = 5         # 每日自动订阅上限(防打爆 we-mp-rss 会话限流)
    # 账号级发现(Phase 1 后端=we-mp-rss search_Biz):本体词搜公众号 → 自动订阅 → 现有轮询+triage。
    # search/add_mp 端点需鉴权(feed 端点公开);we-mp-rss 支持 AK/SK 非交互凭据(Authorization: AK-SK ak:sk)。
    werss_ak: str = Field(default="", validation_alias="WERSS_AK")   # we-mp-rss 访问密钥(search/订阅鉴权)
    werss_sk: str = Field(default="", validation_alias="WERSS_SK")
    wechat_account_prune_min_articles: int = 5   # 发现订阅的号累计 triage ≥N 篇才评估去留(更快剪废号)
    wechat_account_prune_max_keep_rate: float = 0.15  # keep_rate < 此值 → 停用+退订(证明低信噪,止损)
    # human-in-the-loop 门控:关(默认,轻量)=抓全部非 blocked 号,运营方事后拉黑差号;
    # 开(严格)=只抓 approved 号,新号(pending)进审核队列待批准。blocked 号任何模式都不抓。
    wechat_hitl_gate: bool = Field(default=False, validation_alias="XAR_WECHAT_HITL_GATE")
    # wechat-download-api(wcda)后端:curl_cffi 登录公众号平台,搜号→逐号取文→解析全文。
    # 与 we-mp-rss 相比登录更稳(无 selector 抓取)。base 为空 → wcda 发现路径 no-op。
    wcda_base_url: str = Field(default="", validation_alias="WCDA_BASE_URL")
    wcda_accounts_per_query: int = 6      # 每个关键词取前 N 个公众号
    wcda_accounts_per_run: int = 32       # 每轮最多处理 N 个新账号(界定抓取量;广度优先→给多主题留位)
    wcda_articles_per_account: int = 6     # 每个号取最近 N 篇(逐篇解析全文,成本闸)
    wcda_account_junk_filter: bool = True  # 收号后正文解析前:号名含明显跨域垃圾标记即跳过(挡租房/超市/游戏等,省解析预算)

    # --- Exploration module (frontier research): arXiv is public, no key ---
    arxiv_enabled: bool = True
    arxiv_max_results: int = 60
    arxiv_lookback_days: int = 21

    # --- Daily auto-ingest system (orchestration/daily.py + Dagster sidecar) ---
    # Which sources the daily loop pulls (CSV; each unavailable one is skipped).
    # alphapai 不在 daily 默认源:它的无序 per-company 拉取会抢 fetch_chain 的定序额度
    # (fetch_chain 独占 alphapai 抓取);daily.py 仍保留 alphapai 分支供手动/CLI 显式运行。
    daily_enabled_sources: str = ("edgar,cninfo,finnhub,fmp,twitter,reddit,wechat,"
                              "aifinmarket,futu,polymarket,rss,macro")
    daily_run_hour: int = 6            # nightly schedule hour (cron "0 {hour} * * *")
    daily_universe_shards: int = 8     # full universe split into N nightly shards
    daily_news_lookback_days: int = 7  # default Finnhub/FMP news pull window
    daily_kg_doc_limit: int = 800      # cap KG-extraction docs per run/shard (cost guard)

    # --- GLM 常驻抽取工人 (orchestration/glm_worker.py) ---
    glm_worker_cycle_seconds: int = 180    # normal cadence between cycles
    glm_worker_probe_seconds: int = 900    # probe cadence while quota exhausted (15 min)
    glm_worker_batch_docs: int = 25        # KG-extraction docs per cycle
    glm_worker_backfill_units: int = 4     # (company,source,year) history units per cycle
    # 历史回填深度(年)。2026-07-28 由 10 → 3:edgar 回填持续灌入且自身积压(6h 灌 1390/抽 687、
    # 游标才 168/1062 家),而其 kept_rate 仅 6.0%(vs gangtise 70%/alphapai 38%)—— 占 GPU 多、产出低。
    # 缩到近 3 年保留高价值近端历史,把产能让给研报纪要类源。
    history_backfill_years: int = 3
    glm_worker_alt_limit: int = 120        # alt-tracker company slice per cycle (wiki/github pacing)
    glm_worker_gangtise_limit: int = 15    # Gangtise CN research slice per cycle (rotating cursor)
    glm_worker_thesis_rebuilds: int = 2    # signal-challenged theses rebuilt per cycle (LLM)
    glm_worker_link_companies: int = 15    # thesis-holding companies whose fresh facts get claim-linked per cycle
    # 算力最大化(hardware-solutions/算力调度):本地 GPU 与云端订阅分成三条并行常驻流。
    # bulk KG+expert 交给常驻 qwen_drain 服务(本地 GPU 满载),glm_worker 不再跑 build_kg/expert
    # (避免与 drain 的原子领取双抽);thesis 重建交给 subpool 服务(三订阅并行)。glm_worker 只留
    # 抓取/解析/triage/flow/evidence_link(本地 qwen)+ 审计/季报。
    glm_worker_bulk_extract: bool = False  # False=bulk KG+expert 由 qwen_drain 常驻服务处理(解耦,防双抽)

    # --- 常驻 qwen 抽取 drain (orchestration/qwen_drain.py;docker 服务 qwendrain) ---
    qwen_drain_workers: int = 4            # 并发 worker 数(对齐 OLLAMA_NUM_PARALLEL)
    qwen_drain_batch: int = 8             # 每轮原子领取的文档数(= workers*2)
    qwen_drain_idle_seconds: int = 60      # 队列空时的休眠(常驻,吸收后续灌入)
    qwen_drain_model: str = "qwen3-14b-local"  # 本地抽取模型 registry id(换代改 env)
    # 尾部配额的**队列深度阻尼指数**(2026-07-29 审计加入):有效权重 = kept_rate × pending^alpha。
    # 0=退回纯质量权重(逐位兼容旧行为的回滚位,改 env 即可零代码回滚);0.5=平方根阻尼(默认);
    # 1=按积压总量分配(小源会被饿死)。背景:纯质量下 edgar(占尾部 backlog 1.3%)与
    # finnhub(64.7%)拿到相同绝对份额,深队列长期收敛不动。详见 pipeline_priority。
    qwen_drain_depth_alpha: float = 0.5
    # 抽取排除源(CSV;空=不排除,全源皆抽)。留作应急闸:某源灌入过猛/信噪过低压垮本地 GPU 时,
    # 填入源名即暂停其抽取而不影响其余(价值源 alphapai/aifinmarket/gangtise 等由 pipeline_priority
    # 保持优先领取,不会被大源饿死)。历史:2026-07-25 曾填 "x,finnhub" 暂停低 SNR 碎片(200-440字
    # 推文/新闻头条)以腾出 GPU 清价值源积压;价值源清空后于 2026-07-26 按用户指示恢复全源抽取。
    qwen_drain_exclude_sources: str = ""

    # --- 云端订阅并行池 (models/subpool.py + orchestration/subpool_worker.py;服务 subpool) ---
    # GLM-5.2 / Minimax-M3 / Kimi-K3 三订阅并行跑重任务(thesis 重建)直到各自额度耗尽(5h 窗)。
    # 每 provider 独立额度状态(zhipu/minimax/moonshot),触限即冷却、按 probe 节拍探测恢复。
    subpool_enabled: bool = True
    subpool_pins: str = ("glm-5.2-sub,glm-4.6-sub|minimax-m3-sub|kimi-k3-sub")  # '|' 分 provider,','分同 provider 回退链
    subpool_probe_seconds: int = 900      # 某 provider 触限后的探测节拍(5h 窗内周期性探恢复)
    subpool_batch: int = 12               # 每轮分发到三订阅的 thesis 重建数(消耗额度)
    subpool_idle_seconds: int = 120       # 无待建 thesis 或全 provider 冷却时的休眠
    subpool_thesis_stale_hours: int = 24  # thesis 早于此视为过期、进重建队列(持续吃额度)
    # thesis 输出预算。2026-07-29 实测:给 16000 时 output_tokens **恰好顶到 16000**(llm_usage
    # requested.granted=16000/clamped=false),即完整 CompanyThesis(3-6 支柱 × 证据 + 争论 + VP +
    # 估值)本就写不下 —— 顶格输出正是 JSON 被截断的前兆。链首 glm-5.2-sub 可给 32768,提到 30000
    # 留足余量;若某候选给不起,llm_usage.requested.clamped 会如实记录(不再是静默截断)。
    thesis_max_tokens: int = 30000
    thesis_reasoning_effort: str = "low"  # thesis 走低推理力度:reasoning 模型(GLM-5.2/Kimi/Minimax)high-effort
                                          # 会把预算烧在推理致 content 空,thesis 是高量产任务,low 即出内容(治返空)
    # --- 微信多层级挖掘系统 (mining/) ---
    wechat_miner_enabled: bool = True      # T2 triage 预筛闸门(关闭=退回旧的无差别抽取)
    wechat_deep_min: float = 0.4           # triage_score >= 此值才进深度抽取(精度优先)
    glm_worker_triage_docs: int = 40       # 每轮 triage 的微信文档数(短 prompt,便宜)

    # --- 任务监控 (monitoring/;Jarvy「任务监控」面板)---
    # 2026-07-29 审计产物:当时 Dagster 队列死锁 7 天零执行无人察觉、wechat/futu 静默哑火
    # 数周而 cadence 戳仍绿。巡检跑在 app 容器后台线程(同 telegram.start_background)。
    monitor_enabled: bool = Field(default=True, validation_alias="XAR_MONITOR_ENABLED")
    monitor_sweep_seconds: int = 120       # 一轮 ≈15 条带索引 SQL + 2 次 dagster GraphQL
    # 停摆报警推给哪个 Telegram chat。留空则回退 telegram_allowed_chats 首项;两者皆空 =
    # 只有页内告警(面板会提示,并列出 chat_channels 里已知的 chat id 供复制)。
    monitor_telegram_chat: str = Field(default="", validation_alias="XAR_MONITOR_TELEGRAM_CHAT")
    monitor_remind_hours: int = 24         # 持续 down 且未 ack 时的提醒间隔(防告警疲劳)
    dagster_graphql_url: str = Field(default="", validation_alias="XAR_DAGSTER_GRAPHQL_URL")

    # --- Posture / politeness ---
    data_posture: str = "self_use"
    http_user_agent: str = "xar-research/0.1 (+research)"
    crawl_delay_seconds: float = 2.0

    @property
    def has_llm(self) -> bool:
        return bool(self.anthropic_api_key or self.openai_api_key or self.deepseek_api_key
                    or self.glm_api_key or self.moonshot_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
