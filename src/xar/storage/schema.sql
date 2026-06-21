-- XAR database schema. {EMBED_DIM} is substituted at init time.
-- Single Postgres holds: documents/chunks (RAG), bitemporal KG (nodes/edges/events),
-- entity aliases (resolution), report runs/checkpoints, and LLM usage.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- Company registry (the watched basket; the industry-chain anchors)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companies (
    id            TEXT PRIMARY KEY,           -- stable slug, e.g. "innolight"
    name          TEXT NOT NULL,
    aliases       TEXT[] NOT NULL DEFAULT '{}',
    tickers       TEXT[] NOT NULL DEFAULT '{}',
    region        TEXT,                        -- US | CN | JP | KR ...
    chain_role    TEXT,                        -- module_maker | component | customer ...
    cik           TEXT,                        -- SEC CIK if US filer
    cn_code       TEXT,                        -- A-share code if CN
    meta          JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- A company can belong to several parallel industry-chain themes; its per-theme
-- segment is stored in meta.segments. (idempotent for existing databases)
ALTER TABLE companies ADD COLUMN IF NOT EXISTS themes TEXT[] NOT NULL DEFAULT '{}';

-- ---------------------------------------------------------------------------
-- Source documents (provenance + data-permission posture on every row)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    id            TEXT PRIMARY KEY,            -- content hash or source id
    company_id    TEXT REFERENCES companies(id),
    source        TEXT NOT NULL,               -- edgar | cninfo | news | product | jobs | research_meta
    doc_type      TEXT,                        -- 10-K | 8-K | 20-F | announcement | article ...
    title         TEXT,
    url           TEXT,
    published_at  TIMESTAMPTZ,
    permission    TEXT NOT NULL DEFAULT 'green',-- green | grey | red (self-use risk tag)
    license_tag   TEXT,
    object_key    TEXT,                        -- raw artifact location in object store
    text          TEXT,                        -- extracted text (facts, not redistribution)
    meta          JSONB NOT NULL DEFAULT '{}',
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_documents_company ON documents(company_id);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source);

-- ---------------------------------------------------------------------------
-- RAG chunks (hybrid: dense vector + trigram/text for BM25-ish lexical)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chunks (
    id            BIGSERIAL PRIMARY KEY,
    doc_id        TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    company_id    TEXT,
    ordinal       INT NOT NULL,
    text          TEXT NOT NULL,
    tie_out_ok    BOOLEAN NOT NULL DEFAULT TRUE,  -- numeric tie-out gate result
    embedding     vector({EMBED_DIM}),
    meta          JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_trgm ON chunks USING gin (text gin_trgm_ops);
-- IVFFlat created lazily after data exists (see db.ensure_vector_index)

-- ---------------------------------------------------------------------------
-- Bitemporal knowledge graph
--   t_valid_*  : when the fact is true in the world
--   observed_at: when WE learned it (so later docs never overwrite earlier truth)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kg_nodes (
    id            TEXT PRIMARY KEY,            -- canonical slug
    node_type     TEXT NOT NULL,               -- ModuleMaker | UpstreamComponent | DownstreamCustomer | TechRoute
    name          TEXT NOT NULL,
    aliases       TEXT[] NOT NULL DEFAULT '{}',
    tickers       TEXT[] NOT NULL DEFAULT '{}',
    attrs         JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kg_edges (
    id            BIGSERIAL PRIMARY KEY,
    src_id        TEXT NOT NULL REFERENCES kg_nodes(id),
    dst_id        TEXT NOT NULL REFERENCES kg_nodes(id),
    rel_type      TEXT NOT NULL,               -- supplies|second_sources|single_source_risk|uses_techroute|invests_in|competes_with|substitutes|qualified_by
    attrs         JSONB NOT NULL DEFAULT '{}',
    t_valid_from  DATE,
    t_valid_to    DATE,                         -- NULL = still valid
    observed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    invalidated_at TIMESTAMPTZ,                 -- superseded marker (bitemporal)
    confidence    REAL NOT NULL DEFAULT 0.7,
    source_doc_id TEXT REFERENCES documents(id),
    license_tag   TEXT
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON kg_edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON kg_edges(dst_id);
CREATE INDEX IF NOT EXISTS idx_edges_rel ON kg_edges(rel_type);

-- Dated catalyst / order events (supersedable bitemporal 5-tuples)
CREATE TABLE IF NOT EXISTS kg_events (
    id            BIGSERIAL PRIMARY KEY,
    company_id    TEXT,
    node_id       TEXT REFERENCES kg_nodes(id),
    event_type    TEXT NOT NULL,               -- capex_guidance|order|qualification|product_ramp|accelerator_launch|capacity_expansion|supply_constraint|earnings|equity_investment|tech_substitution
    event_date    DATE,
    magnitude     TEXT,
    polarity      TEXT,                         -- positive | negative | neutral
    tech_route_tag TEXT,
    summary       TEXT,
    attrs         JSONB NOT NULL DEFAULT '{}',
    observed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    invalidated_at TIMESTAMPTZ,
    confidence    REAL NOT NULL DEFAULT 0.7,
    source_doc_id TEXT REFERENCES documents(id),
    license_tag   TEXT,
    dedup_key     TEXT UNIQUE                   -- event-level dedup across sources
);
CREATE INDEX IF NOT EXISTS idx_events_company ON kg_events(company_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON kg_events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_date ON kg_events(event_date);

-- Deterministic entity resolution: alias -> canonical node
CREATE TABLE IF NOT EXISTS entity_aliases (
    alias_norm    TEXT PRIMARY KEY,            -- normalized (lower, stripped)
    node_id       TEXT NOT NULL REFERENCES kg_nodes(id),
    source        TEXT NOT NULL DEFAULT 'seed' -- seed | learned
);

-- ---------------------------------------------------------------------------
-- Report runs (checkpoint + human-in-the-loop interrupt) and outputs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS report_runs (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,               -- deep_report | tracking_summary | takeaways
    request       JSONB NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending', -- pending|running|awaiting_approval|approved|published|failed
    state         JSONB NOT NULL DEFAULT '{}', -- checkpointed node outputs
    snapshot_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reports (
    id            BIGSERIAL PRIMARY KEY,
    run_id        TEXT REFERENCES report_runs(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,
    content_md    TEXT NOT NULL,
    citations     JSONB NOT NULL DEFAULT '[]',
    metrics       JSONB NOT NULL DEFAULT '{}', -- evidence coverage, hallucination risk
    snapshot_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS llm_usage (
    id            BIGSERIAL PRIMARY KEY,
    run_id        TEXT,
    node          TEXT,
    model         TEXT,
    input_tokens  INT NOT NULL DEFAULT 0,
    output_tokens INT NOT NULL DEFAULT 0,
    usd           REAL NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ===========================================================================
-- STRUCTURED DATA LAYER
-- Multi-provider (Finnhub / FMP / Polygon / Yahoo / Wind / EDGAR) numeric facts,
-- normalized onto a single canonical metric vocabulary (ontology/standards.py).
-- All carry `source` + `as_of` so the same fact from different providers and the
-- evolution of consensus over time are both first-class (bitemporal-friendly).
-- ===========================================================================

-- Reported financial statement facts (income/balance/cashflow line items + ratios)
CREATE TABLE IF NOT EXISTS fundamentals (
    id            BIGSERIAL PRIMARY KEY,
    company_id    TEXT REFERENCES companies(id),
    metric        TEXT NOT NULL,               -- canonical key (FinMetric); e.g. revenue, gross_margin, capex
    period        TEXT,                        -- 'FY2025' | 'Q3-2025' | 'TTM'
    period_end    DATE,
    freq          TEXT,                        -- annual | quarter | ttm
    value         DOUBLE PRECISION,
    unit          TEXT NOT NULL DEFAULT 'USD',
    source        TEXT NOT NULL,               -- finnhub | fmp | polygon | yahoo | wind | edgar
    as_of         TIMESTAMPTZ NOT NULL DEFAULT now(),
    meta          JSONB NOT NULL DEFAULT '{}',
    UNIQUE (company_id, metric, period, source)
);
CREATE INDEX IF NOT EXISTS idx_fund_company ON fundamentals(company_id);
CREATE INDEX IF NOT EXISTS idx_fund_metric ON fundamentals(metric);

-- Forward estimates / analyst consensus (revenue, eps, ebitda, capex ...)
CREATE TABLE IF NOT EXISTS estimates (
    id            BIGSERIAL PRIMARY KEY,
    company_id    TEXT REFERENCES companies(id),
    metric        TEXT NOT NULL,
    period        TEXT,                        -- target fiscal period being estimated
    period_end    DATE,
    value         DOUBLE PRECISION,            -- mean / consensus
    high          DOUBLE PRECISION,
    low           DOUBLE PRECISION,
    n_analysts    INT,
    unit          TEXT NOT NULL DEFAULT 'USD',
    source        TEXT NOT NULL,
    as_of         DATE NOT NULL,               -- consensus snapshot date (revision tracking)
    meta          JSONB NOT NULL DEFAULT '{}',
    UNIQUE (company_id, metric, period, source, as_of)
);
CREATE INDEX IF NOT EXISTS idx_est_company ON estimates(company_id);
CREATE INDEX IF NOT EXISTS idx_est_metric ON estimates(metric);

-- Analyst rating distribution + price targets
CREATE TABLE IF NOT EXISTS analyst_ratings (
    id            BIGSERIAL PRIMARY KEY,
    company_id    TEXT REFERENCES companies(id),
    as_of         DATE NOT NULL,
    strong_buy    INT, buy INT, hold INT, sell INT, strong_sell INT,
    pt_mean       DOUBLE PRECISION,
    pt_high       DOUBLE PRECISION,
    pt_low        DOUBLE PRECISION,
    source        TEXT NOT NULL,
    meta          JSONB NOT NULL DEFAULT '{}',
    UNIQUE (company_id, as_of, source)
);

-- Daily OHLCV prices (context + catalyst backtest)
CREATE TABLE IF NOT EXISTS prices (
    company_id    TEXT REFERENCES companies(id),
    ticker        TEXT NOT NULL,
    d             DATE NOT NULL,
    open          DOUBLE PRECISION,
    high          DOUBLE PRECISION,
    low           DOUBLE PRECISION,
    close         DOUBLE PRECISION,
    volume        DOUBLE PRECISION,
    source        TEXT NOT NULL,
    PRIMARY KEY (ticker, d, source)
);
CREATE INDEX IF NOT EXISTS idx_prices_company ON prices(company_id);

-- Insider transactions (Form 4 style) — a supply-securing / conviction signal
CREATE TABLE IF NOT EXISTS insider_trades (
    id            BIGSERIAL PRIMARY KEY,
    company_id    TEXT REFERENCES companies(id),
    insider       TEXT,
    role          TEXT,
    txn_date      DATE,
    txn_type      TEXT,                        -- buy | sell
    shares        DOUBLE PRECISION,
    price         DOUBLE PRECISION,
    value         DOUBLE PRECISION,
    source        TEXT NOT NULL,
    dedup_key     TEXT UNIQUE,
    meta          JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_insider_company ON insider_trades(company_id);

-- Prediction-market signals (Polymarket etc.) — forward probabilities for
-- AI-capex / accelerator-launch / tech-route catalysts the chain is levered to.
CREATE TABLE IF NOT EXISTS prediction_markets (
    id            BIGSERIAL PRIMARY KEY,
    market_id     TEXT NOT NULL,
    question      TEXT,
    outcome       TEXT,
    probability   DOUBLE PRECISION,
    volume        DOUBLE PRECISION,
    close_date    DATE,
    tags          TEXT[] NOT NULL DEFAULT '{}',
    company_id    TEXT,                        -- optional linkage to a watched name
    tech_route_tag TEXT,
    source        TEXT NOT NULL DEFAULT 'polymarket',
    as_of         TIMESTAMPTZ NOT NULL DEFAULT now(),
    meta          JSONB NOT NULL DEFAULT '{}',
    UNIQUE (market_id, outcome, as_of)
);
CREATE INDEX IF NOT EXISTS idx_pm_company ON prediction_markets(company_id);

-- Social posts (X / Reddit) — unstructured, self-use posture (permission=grey).
-- Notable posts are mirrored into `documents` so they flow through RAG + KG.
CREATE TABLE IF NOT EXISTS social_posts (
    id            TEXT PRIMARY KEY,            -- platform:postid
    platform      TEXT NOT NULL,               -- x | reddit
    company_id    TEXT,
    author        TEXT,
    url           TEXT,
    posted_at     TIMESTAMPTZ,
    text          TEXT,
    metrics       JSONB NOT NULL DEFAULT '{}', -- likes / reposts / score / comments
    sentiment     REAL,
    permission    TEXT NOT NULL DEFAULT 'grey',
    meta          JSONB NOT NULL DEFAULT '{}',
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_social_company ON social_posts(company_id);
CREATE INDEX IF NOT EXISTS idx_social_platform ON social_posts(platform);

-- Expert-agent processed insights: the AI refinement layer over alt-data
-- (X / WeChat / news / AIFINmarket). Every processed doc gets one row; `kept`
-- marks the ones that passed the relevance + signal-quality gate and were
-- written into the ontology (kg_events, license_tag='expert'). Raises SNR by
-- distilling raw posts/articles into curated, decision-useful claims.
CREATE TABLE IF NOT EXISTS expert_insights (
    id            BIGSERIAL PRIMARY KEY,
    doc_id        TEXT UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
    source        TEXT,                        -- wechat | x | news | aifinmarket
    company_id    TEXT,
    stance        TEXT,                         -- bull | bear | neutral
    polarity      TEXT,
    catalyst_type TEXT,
    thesis        TEXT,                         -- refined professional takeaway
    evidence      TEXT,
    tech_route_tag TEXT,
    signal_quality REAL NOT NULL DEFAULT 0,     -- 0..1
    kept          BOOLEAN NOT NULL DEFAULT FALSE,
    kg_event_id   BIGINT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_expert_company ON expert_insights(company_id);
CREATE INDEX IF NOT EXISTS idx_expert_kept ON expert_insights(kept);

-- ============================================================================
-- Exploration module: frontier-research synthesis (NOT tied to companies).
-- `frontier_fronts` = LLM-synthesized research fronts (trends) per domain, each
-- a forward-looking directional thesis grounded in recent arXiv preprints + voices.
-- `frontier_domain_state` = per-domain rollup (headline + momentum + counts).
-- ============================================================================
CREATE TABLE IF NOT EXISTS frontier_fronts (
    id            TEXT PRIMARY KEY,            -- domain:slug
    domain        TEXT NOT NULL,               -- ai | physics | math | cs_systems | neuro | complex
    title         TEXT NOT NULL,
    summary       TEXT,                        -- what is happening now
    direction     TEXT,                        -- forward-looking directional thesis
    significance  TEXT,                        -- why it matters / second-order implications
    maturity      TEXT,                        -- emerging | accelerating | maturing
    horizon       TEXT,                        -- near | mid | long
    momentum      INT NOT NULL DEFAULT 50,     -- 0..100 activity/acceleration
    confidence    REAL NOT NULL DEFAULT 0.6,   -- 0..1
    key_papers    TEXT[] NOT NULL DEFAULT '{}',-- arxiv ids
    key_terms     TEXT[] NOT NULL DEFAULT '{}',
    key_voices    TEXT[] NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fronts_domain ON frontier_fronts(domain);
CREATE INDEX IF NOT EXISTS idx_fronts_momentum ON frontier_fronts(momentum);

CREATE TABLE IF NOT EXISTS frontier_domain_state (
    domain        TEXT PRIMARY KEY,
    headline      TEXT,                        -- one-line state-of-the-frontier
    momentum      INT NOT NULL DEFAULT 50,
    paper_count   INT NOT NULL DEFAULT 0,
    voice_count   INT NOT NULL DEFAULT 0,
    front_count   INT NOT NULL DEFAULT 0,
    synthesized_by TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
