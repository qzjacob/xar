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

    # --- Embeddings ---
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384

    # --- Storage ---
    database_url: str = "postgresql://xar:xar@localhost:5432/xar"
    object_store: str = "file://./data/objects"

    # --- Ingestion: market-data providers (all optional; blank = skipped) ---
    edgar_identity: str = "xar-research research@example.com"
    finnhub_api_key: str = Field(default="", validation_alias="FINNHUB_API_KEY")
    fmp_api_key: str = Field(default="", validation_alias="FMP_API_KEY")
    polygon_api_key: str = Field(default="", validation_alias="POLYGON_API_KEY")
    tushare_token: str = Field(default="", validation_alias="TUSHARE_TOKEN")
    # X / Twitter. TwitterAPI.io (third-party) uses an X-API-Key; official X API v2
    # uses a bearer token. Set either; TwitterAPI.io is preferred when present.
    twitterapi_key: str = Field(
        default="", validation_alias=AliasChoices("TWITTERAPI_TOKEN", "TWITTERAPI_KEY"))
    x_bearer_token: str = Field(default="", validation_alias="X_BEARER_TOKEN")
    reddit_client_id: str = Field(default="", validation_alias="REDDIT_CLIENT_ID")
    reddit_client_secret: str = Field(default="", validation_alias="REDDIT_CLIENT_SECRET")
    # Polymarket Gamma API is public (no key). Wind requires a local terminal.
    enable_wind: bool = False
    # Provider preference order for structured fundamentals/estimates/prices
    market_data_order: str = "fmp,finnhub,polygon,yahoo"
    # X (Twitter) expert handles to follow (CSV of @handles); blank = keyword-only.
    x_expert_handles: str = Field(default="", validation_alias="X_EXPERT_HANDLES")

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

    # --- Exploration module (frontier research): arXiv is public, no key ---
    arxiv_enabled: bool = True
    arxiv_max_results: int = 60
    arxiv_lookback_days: int = 21

    # --- Daily auto-ingest system (orchestration/daily.py + Dagster sidecar) ---
    # Which sources the daily loop pulls (CSV; each unavailable one is skipped).
    daily_enabled_sources: str = "edgar,cninfo,finnhub,fmp,twitter,reddit,wechat,aifinmarket,polymarket"
    daily_run_hour: int = 6            # nightly schedule hour (cron "0 {hour} * * *")
    daily_universe_shards: int = 8     # full universe split into N nightly shards
    daily_news_lookback_days: int = 7  # default Finnhub/FMP news pull window
    daily_kg_doc_limit: int = 800      # cap KG-extraction docs per run/shard (cost guard)

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
