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
-- LLM task-manager: billing-aware routing observability (additive, idempotent) so spend
-- can be audited per provider / task / billing (token vs subscription).
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS provider   TEXT;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS task_class TEXT;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS billing    TEXT;
CREATE INDEX IF NOT EXISTS idx_llm_usage_provider ON llm_usage(provider);
CREATE INDEX IF NOT EXISTS idx_llm_usage_task     ON llm_usage(task_class);
-- Runtime route override: re-point a capability/task to a new model generation live
-- (ops API) without a redeploy. Strongest layer of override > env > registry preferred.
CREATE TABLE IF NOT EXISTS route_overrides (
    key        TEXT PRIMARY KEY,    -- a capability ('cheap_bulk') or task_class ('kg_extract')
    model_id   TEXT NOT NULL,       -- registry ModelSpec.id
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
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

-- Forward event calendar — SCHEDULED, dated, future events (earnings, investor
-- days, product launches, conferences, ex-div, lockups, policy meetings, index
-- rebalances, PDUFA). Kept distinct from the past-tense `kg_events` catalyst
-- stream: a calendar item is scheduled -> confirmed -> occurred/cancelled, and
-- once it occurs the normal extraction path writes the observed kg_events row
-- (linked back via meta.calendar_id). This is the "what's coming" dimension.
CREATE TABLE IF NOT EXISTS event_calendar (
    id            BIGSERIAL PRIMARY KEY,
    company_id    TEXT REFERENCES companies(id),
    event_type    TEXT NOT NULL,               -- earnings | investor_day | product_launch |
                                               -- conference | ex_dividend | lockup_expiry |
                                               -- policy_meeting | index_rebalance | pdufa | guidance_update
    scheduled_for DATE NOT NULL,
    window_end    DATE,                          -- for ranges (multi-day conferences)
    title         TEXT,
    status        TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled | confirmed | occurred | cancelled
    importance    INT NOT NULL DEFAULT 2,        -- 1..3
    tech_route_tag TEXT,
    source        TEXT NOT NULL,                 -- fmp | finnhub | manual
    as_of         TIMESTAMPTZ NOT NULL DEFAULT now(),
    meta          JSONB NOT NULL DEFAULT '{}',
    dedup_key     TEXT UNIQUE                    -- company|type|date|title-hash
);
CREATE INDEX IF NOT EXISTS idx_cal_company ON event_calendar(company_id);
CREATE INDEX IF NOT EXISTS idx_cal_date ON event_calendar(scheduled_for);

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

-- ===========================================================================
-- SEMANTIC LAYER ENRICHMENT (additive, idempotent)
-- The existing kg_events / kg_edges / expert_insights tables already ARE the
-- timestamped, ontology-anchored "semantic database". These additive columns
-- give every semantic fact a stable ontology anchor (theme/segment), a public-
-- information timestamp (as_of) and the causal/forward-looking semantics
-- (narrative, time_orientation) the numeric tables don't carry. All statements
-- are `ADD COLUMN IF NOT EXISTS` so init_schema() re-runs cleanly (same pattern
-- as companies.themes above) — no migration runner, no destructive change.
-- ===========================================================================
ALTER TABLE kg_events       ADD COLUMN IF NOT EXISTS theme            TEXT;
ALTER TABLE kg_events       ADD COLUMN IF NOT EXISTS segment          TEXT;
ALTER TABLE kg_events       ADD COLUMN IF NOT EXISTS narrative        TEXT;  -- ≤2-sentence causal/forward context
ALTER TABLE kg_events       ADD COLUMN IF NOT EXISTS time_orientation TEXT;  -- forward_looking | backward_looking
ALTER TABLE kg_edges        ADD COLUMN IF NOT EXISTS theme            TEXT;
ALTER TABLE expert_insights ADD COLUMN IF NOT EXISTS as_of            DATE;  -- public-info date (= source doc published_at)
ALTER TABLE expert_insights ADD COLUMN IF NOT EXISTS theme            TEXT;
ALTER TABLE expert_insights ADD COLUMN IF NOT EXISTS segment          TEXT;
ALTER TABLE expert_insights ADD COLUMN IF NOT EXISTS time_orientation TEXT;
-- Forward-claim resolution lifecycle: close the loop on forward_looking catalysts
-- (did the expectation realize?). Written ONLY on forward_looking rows by the daily
-- resolve stage; backward hard-fact rows are never touched, so the event log stays
-- effectively append-only where it matters. resolution: NULL = unresolved (re-checked
-- each run) → terminal hit | miss | stale. realizes_event_id links the claim to the
-- later realized event that closed it.
ALTER TABLE kg_events       ADD COLUMN IF NOT EXISTS resolution        TEXT;
ALTER TABLE kg_events       ADD COLUMN IF NOT EXISTS resolved_at       TIMESTAMPTZ;
ALTER TABLE kg_events       ADD COLUMN IF NOT EXISTS realizes_event_id BIGINT REFERENCES kg_events(id);
CREATE INDEX IF NOT EXISTS idx_expert_asof  ON expert_insights(as_of);
CREATE INDEX IF NOT EXISTS idx_events_theme ON kg_events(theme);
CREATE INDEX IF NOT EXISTS idx_events_resolution ON kg_events(time_orientation, resolution);

-- Single timestamped "semantic fact stream": the surface the LLM agent and the
-- backtest read. Unions the catalyst-event layer (kg_events) and the expert
-- narrative/stance layer (expert_insights, kept rows) into one point-queryable
-- shape. Every row carries: as_of (valid-time) + observed_at (tx-time) for point
-- queries, source_doc_id for provenance, theme/segment/company_id for the
-- ontology anchor, and polarity/narrative/time_orientation for stance/causality.
CREATE OR REPLACE VIEW semantic_facts AS
  SELECT 'event'::text AS kind, e.id::text AS id, e.company_id, e.event_type AS category,
         e.event_date AS as_of, e.observed_at, e.polarity, e.summary AS content,
         e.narrative, e.time_orientation, e.tech_route_tag, e.confidence,
         e.source_doc_id, e.license_tag, e.theme, e.segment, e.resolution
    -- exclude expert-mirrored events: the expert_insights arm below is their canonical
    -- representation, so without this filter every kept insight would appear twice.
    FROM kg_events e WHERE e.invalidated_at IS NULL AND e.license_tag IS DISTINCT FROM 'expert'
  UNION ALL
  SELECT 'insight', x.id::text, x.company_id, x.catalyst_type,
         x.as_of, x.created_at, x.polarity, x.thesis,
         NULL, x.time_orientation, x.tech_route_tag, x.signal_quality,
         x.doc_id, 'expert', x.theme, x.segment, e2.resolution
    -- surface the resolution written onto the expert-mirrored kg_event: those rows are
    -- excluded from the event arm above (license_tag='expert'), so without this join a
    -- resolved expert forward-claim would read back as NULL everywhere downstream.
    FROM expert_insights x
    LEFT JOIN kg_events e2 ON e2.id = x.kg_event_id
   WHERE x.kept;

-- ---------------------------------------------------------------------------
-- Daily-ingest run log (observability + per-source incremental cursor).
-- One row per pull/daily run; last_success_ts(kind) = max(finished_at) where ok.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingest_runs (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL,                -- 'daily' | source id (finnhub/edgar/...)
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    status        TEXT NOT NULL DEFAULT 'running',  -- running | ok | failed | skipped
    since_ts      TIMESTAMPTZ,                  -- pull window lower bound used by this run
    stats         JSONB NOT NULL DEFAULT '{}',
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_runs_kind    ON ingest_runs(kind);
CREATE INDEX IF NOT EXISTS idx_ingest_runs_started ON ingest_runs(started_at DESC);

-- ---------------------------------------------------------------------------
-- Genny Data Room: uploaded reports are ordinary `documents` rows (source='upload')
-- tagged to a theme/segment so a per-sector room can filter them. Additive, same
-- pattern as the kg_events theme/segment columns.
-- ---------------------------------------------------------------------------
-- KG 抽取尝试戳:build_kg 每次尝试后盖戳(含零产出/毒文档),pending 口径即
-- kg_extracted_at IS NULL —— 取代 kg_edges/kg_events 反连接(见 kg/extract.py)。
ALTER TABLE documents ADD COLUMN IF NOT EXISTS kg_extracted_at TIMESTAMPTZ;
-- 迁移回填(幂等):已产出过边/事件的文档视为已抽取
UPDATE documents SET kg_extracted_at = now()
 WHERE kg_extracted_at IS NULL
   AND (EXISTS (SELECT 1 FROM kg_edges  e WHERE e.source_doc_id = documents.id)
     OR EXISTS (SELECT 1 FROM kg_events v WHERE v.source_doc_id = documents.id));
CREATE INDEX IF NOT EXISTS idx_docs_kg_pending ON documents(kg_extracted_at) WHERE kg_extracted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_edges_srcdoc  ON kg_edges(source_doc_id);
CREATE INDEX IF NOT EXISTS idx_events_srcdoc ON kg_events(source_doc_id);

ALTER TABLE documents ADD COLUMN IF NOT EXISTS theme   TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS segment TEXT;
CREATE INDEX IF NOT EXISTS idx_documents_theme_segment ON documents(theme, segment);

-- ---------------------------------------------------------------------------
-- Chathy: persistent tool-calling chat sessions (ChatGPT-style). One session has an
-- ordered message log; assistant rows may carry tool_calls, tool rows carry a result.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_sessions (
    id         TEXT PRIMARY KEY,
    title      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id           BIGSERIAL PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role         TEXT NOT NULL,           -- user | assistant | tool
    content      TEXT,
    tool_calls   JSONB,                   -- assistant-side function calls (role=assistant)
    tool_call_id TEXT,                    -- which call this result answers (role=tool)
    name         TEXT,                    -- tool name (role=tool)
    usage        JSONB,                   -- token usage for the LLM turn that produced this
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);

-- ---------------------------------------------------------------------------
-- Fenny: options-desk position blotter (replaces the vendored ~/.fcn/blotter.json
-- file store — see src/xar/fenny/blotter_pg.py). Strategy + valuation kept as JSONB.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fenny_blotter (
    id        TEXT PRIMARY KEY,
    ts        TIMESTAMPTZ NOT NULL,
    strategy  JSONB NOT NULL,
    valuation JSONB NOT NULL,
    notes     TEXT NOT NULL DEFAULT '',
    status    TEXT NOT NULL DEFAULT 'open'   -- open | closed | rolled
);

-- ---------------------------------------------------------------------------
-- Company 360: first-class investment-thesis object (ontology/thesis.py) +
-- typed evidence anchors + institutional ownership (13F). Additive, idempotent.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS company_thesis (
    id            BIGSERIAL PRIMARY KEY,
    company_id    TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    version       INT  NOT NULL,               -- monotonic per company
    as_of         DATE NOT NULL,               -- information cutoff the thesis reflects
    stance        TEXT NOT NULL,               -- bull | neutral | bear
    conviction    REAL,                        -- 1..5, evidence-disciplined
    one_liner     TEXT,
    content       JSONB NOT NULL,              -- full ontology.thesis.CompanyThesis payload
    quality       JSONB,                       -- evidence_coverage / numeric_grounding / …
    changed_because TEXT,                      -- refresh diff note vs previous version
    model         TEXT,                        -- model id that generated it
    run_id        TEXT,                        -- llm_usage correlation
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id, version)
);
CREATE INDEX IF NOT EXISTS idx_thesis_company ON company_thesis(company_id, version DESC);

CREATE TABLE IF NOT EXISTS thesis_evidence (
    id         BIGSERIAL PRIMARY KEY,
    thesis_id  BIGINT NOT NULL REFERENCES company_thesis(id) ON DELETE CASCADE,
    slot       TEXT NOT NULL,                  -- pillar key | 'bull' | 'bear' | 'risk:<type>'
    kind       TEXT NOT NULL,                  -- event | edge | chunk | insight | fundamental | estimate | registry
    ref_id     TEXT NOT NULL,                  -- id in the referenced table (typed drill-down)
    quote      TEXT,
    note       TEXT
);
CREATE INDEX IF NOT EXISTS idx_thesis_evidence ON thesis_evidence(thesis_id);

-- 另类数据信号库(ontology/altdata.py 的信号谱系;providers/alt/* 写入)。
-- PIT 安全:period_end=经济期,observed_at=知晓时;读取按 observed_at <= as_of。
CREATE TABLE IF NOT EXISTS alt_signals (
    id          BIGSERIAL PRIMARY KEY,
    signal_key  TEXT NOT NULL,               -- ontology.altdata.ALT_SIGNALS 之一
    company_id  TEXT REFERENCES companies(id) ON DELETE CASCADE,  -- NULL = theme 级
    theme       TEXT,                        -- theme 级信号的归属链
    period_end  DATE NOT NULL,               -- 经济期末(月营收=月末,周频=周末)
    value       DOUBLE PRECISION NOT NULL,
    unit        TEXT,
    meta        JSONB NOT NULL DEFAULT '{}',  -- 细分(如 AI 岗位数/星标增量/yoy)
    source      TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 唯一键需处理 NULL 列(theme/company_id 互斥为 NULL);默认 UNIQUE 的 NULLS DISTINCT
-- 会使 ON CONFLICT 永不触发 → 每次重拉都重复。一次性迁移(仅在索引缺失时执行):
-- 去掉旧 NULLS-DISTINCT 约束 → 去重历史行 → 建 COALESCE 表达式唯一索引(可移植、自愈)。
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'uq_alt_signal') THEN
        ALTER TABLE alt_signals DROP CONSTRAINT IF EXISTS
            alt_signals_signal_key_company_id_theme_period_end_key;
        DELETE FROM alt_signals a USING alt_signals b
         WHERE a.id < b.id AND a.signal_key = b.signal_key
           AND COALESCE(a.company_id, '') = COALESCE(b.company_id, '')
           AND COALESCE(a.theme, '') = COALESCE(b.theme, '')
           AND a.period_end = b.period_end;
        CREATE UNIQUE INDEX uq_alt_signal ON alt_signals
            (signal_key, COALESCE(company_id, ''), COALESCE(theme, ''), period_end);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_alt_company ON alt_signals(company_id, signal_key, period_end DESC);
CREATE INDEX IF NOT EXISTS idx_alt_theme   ON alt_signals(theme, signal_key, period_end DESC);

-- GLM 常驻工人状态(额度治理 + 回填游标 + 节拍;orchestration/glm_worker.py)
CREATE TABLE IF NOT EXISTS glm_worker_state (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS holdings (
    id          BIGSERIAL PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    holder      TEXT NOT NULL,                 -- manager name
    holder_cik  TEXT,                          -- SEC CIK when known
    shares      NUMERIC,
    value_usd   NUMERIC,
    pct_out     NUMERIC,                       -- % of shares outstanding, if computable
    as_of       DATE NOT NULL,                 -- report period (13F quarter end)
    filed_at    DATE,
    source      TEXT NOT NULL DEFAULT 'edgar_13f',
    UNIQUE(company_id, holder, as_of)
);
CREATE INDEX IF NOT EXISTS idx_holdings_company ON holdings(company_id, as_of DESC);
