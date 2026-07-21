"""Central configuration. Everything is env-driven so the platform is turnkey:
copy `.env.example` -> `.env`, set ANTHROPIC_API_KEY, and run."""
from __future__ import annotations

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
    model_effort: str = "high"
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
    # MiniMax(OpenAI 兼容,token 计费):强推理 + 1M 长上下文。空 key => 该 provider 跳过。
    minimax_api_key: str = Field(default="", validation_alias=AliasChoices("MINIMAX_API_KEY", "MINIMAXI_API_KEY"))
    minimax_sub_api_key: str = Field(default="", validation_alias="MINIMAX_SUB_API_KEY")
    # --- 本地 LLM(minis ollama @ RTX 3090;hardware-solutions/minis-算力调度方案.md §9)---
    # OLLAMA_API_KEY 是占位 key(ollama 不校验),但必须非空 —— key 在场性即"本地已配"开关,
    # 复用 model_usable/complete 的既有 key 门。base 默认走 registry(host.docker.internal),
    # 特殊拓扑用 OLLAMA_API_BASE 覆盖。glm_worker_local_first=true 时工人钉扎链前插 glm4-local
    # (见 glm_worker._fetchy_pin);端点不可达(如 mlrun --exclusive 停机)由候选轮转自动回落云 GLM。
    ollama_api_key: str = Field(default="", validation_alias="OLLAMA_API_KEY")
    ollama_api_base: str = Field(default="", validation_alias="OLLAMA_API_BASE")
    glm_worker_local_first: bool = False   # on → glmworker 本地优先(需 OLLAMA_API_KEY + GLM 订阅 key 双在场)
    glm_worker_local_model: str = "glm4-local"  # 本地头 registry id;换代/回滚 = 改 env 重建容器,零代码(Phase 4)
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
    wechat_discover_max_articles: int = 200     # 每轮抓取正文的文章上限(成本闸)
    wechat_discover_min_chars: int = 200        # 正文短于此 → 跳过(图片/视频号,triage 也会地板掉)
    wechat_discover_lookback_days: int = 14     # 搜索只要近 N 天(高信噪、避免历史回填灌库)
    # 晋升门:发现文档 triage 产出达标的号,自动加入 we-mp-rss 订阅做长期稳定轮询。
    wechat_promote_min_articles: int = 3        # 该号至少发现过 N 篇(已 triage)才够格晋升
    wechat_promote_min_keep_rate: float = 0.5   # 且 triage 保留率 >= 此值(高信噪号才晋升)
    wechat_promote_max_per_day: int = 5         # 每日自动订阅上限(防打爆 we-mp-rss 会话限流)
    # 账号级发现(Phase 1 后端=we-mp-rss search_Biz):本体词搜公众号 → 自动订阅 → 现有轮询+triage。
    # search/add_mp 端点需鉴权(feed 端点公开);we-mp-rss 支持 AK/SK 非交互凭据(Authorization: AK-SK ak:sk)。
    werss_ak: str = Field(default="", validation_alias="WERSS_AK")   # we-mp-rss 访问密钥(search/订阅鉴权)
    werss_sk: str = Field(default="", validation_alias="WERSS_SK")
    wechat_account_prune_min_articles: int = 8   # 发现订阅的号累计 triage ≥N 篇才评估去留
    wechat_account_prune_max_keep_rate: float = 0.15  # keep_rate < 此值 → 停用(证明低信噪,止损)
    # wechat-download-api(wcda)后端:curl_cffi 登录公众号平台,搜号→逐号取文→解析全文。
    # 与 we-mp-rss 相比登录更稳(无 selector 抓取)。base 为空 → wcda 发现路径 no-op。
    wcda_base_url: str = Field(default="", validation_alias="WCDA_BASE_URL")
    wcda_accounts_per_query: int = 6      # 每个关键词取前 N 个公众号
    wcda_accounts_per_run: int = 12       # 每轮最多处理 N 个新账号(界定抓取量)
    wcda_articles_per_account: int = 6     # 每个号取最近 N 篇(逐篇解析全文,成本闸)

    # --- Exploration module (frontier research): arXiv is public, no key ---
    arxiv_enabled: bool = True
    arxiv_max_results: int = 60
    arxiv_lookback_days: int = 21

    # --- Daily auto-ingest system (orchestration/daily.py + Dagster sidecar) ---
    # Which sources the daily loop pulls (CSV; each unavailable one is skipped).
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
    glm_worker_alt_limit: int = 120        # alt-tracker company slice per cycle (wiki/github pacing)
    glm_worker_gangtise_limit: int = 15    # Gangtise CN research slice per cycle (rotating cursor)
    glm_worker_thesis_rebuilds: int = 2    # signal-challenged theses rebuilt per cycle (LLM)
    glm_worker_link_companies: int = 15    # thesis-holding companies whose fresh facts get claim-linked per cycle
    # --- 微信多层级挖掘系统 (mining/) ---
    wechat_miner_enabled: bool = True      # T2 triage 预筛闸门(关闭=退回旧的无差别抽取)
    wechat_deep_min: float = 0.4           # triage_score >= 此值才进深度抽取(精度优先)
    glm_worker_triage_docs: int = 40       # 每轮 triage 的微信文档数(短 prompt,便宜)

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
