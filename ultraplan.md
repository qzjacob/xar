# Plan — Ontology-anchored, timestamped semantic database + daily auto-ingest system

> **STATUS: IMPLEMENTED / SHIPPED** on branch `feat/semantic-db-daily-ingest` — 36 pytest pass, `ruff` clean,
> running in docker (app http://localhost:8000, Dagster http://localhost:3001, Postgres+pgvector). All of
> Pillars A/B/C below are applied and idempotent (`init_schema()` re-runs clean): `semantic_facts` view +
> additive columns + `ingest_runs`; `run_daily` + `runlog` + Dagster `definitions.py` (`pull_shard`×8 @06:00,
> `extract_all` @06:30); `pull_news` on `finnhub.py`/`fmp.py`; CLI `xar daily`. **Added on top after evaluating
> GLM-5.2's `SEMANTIC_DB_PLAN.md`:** the forward-claim resolution lifecycle (`kg/resolve_claims.py`
> `resolve_forward_claims` → `kg_events.resolution`, CLI `xar resolve-claims`) and the strict-PIT backtest
> (`catalyst_returns.py` entry = `GREATEST(as_of, observed_at)`, local prices first).

## Context (why)

On top of the existing platform, build — around the **ontology** and the news/feed sources
(TwitterAPI / Finnhub subscription news / WeChat 公众号 / etc.) — a **timestamped, backtestable
semantic database**: it carries the *semantic-level* data the ontology's standard numeric tables
don't (catalyst narrative, stance, causality, sentiment) to serve LLM reasoning; and a
**daily automatic system** that continuously pulls from every reachable source and updates it.

**Verified against the codebase (every file claim below was read): ~70% of the mechanism already
exists.** This plan is reuse + increment — almost entirely additive and idempotent.

- **The semantic DB already exists**, spread across three timestamped + provenance + ontology-anchored tables:
  - `kg_events` (schema.sql:99-119) — catalyst event stream: `event_date`(valid-time) / `observed_at`(tx-time) /
    `polarity` / `tech_route_tag` / `summary` / `source_doc_id` / `dedup_key`(UNIQUE) / `invalidated_at`.
  - `kg_edges` (schema.sql:80-96) — semantic relations, bitemporal `t_valid_from/to` + `observed_at` +
    `invalidated_at` + `confidence` + `source_doc_id`.
  - `expert_insights` (schema.sql:325-342) — narrative/stance layer: `stance` / `thesis` / `evidence` /
    `signal_quality` / `kept`; kept rows mirror into `kg_events(license_tag='expert')`.
- All three derive from `documents` (carries `published_at` — the backtestable timestamp). X / WeChat /
  Reddit / AIFINmarket already land in `documents` / `social_posts`; `kg.extract.build_kg()` +
  `kg.expert.process()` already mine them (both incremental via `NOT EXISTS` on `source_doc_id` / `doc_id`).
- Bitemporal point-queries exist: `retrieval/graphrag.py` `neighbors(as_of=)` / `events(since=)` /
  `changes_since()`. Backtest `backtest/catalyst_returns.py` does event-study off `event_date` —
  **but only over `kg_events`, not expert/sentiment semantics.**
- Scheduling: `orchestration/definitions.py` is a Dagster DAG (filings→chunks→KG, 6am daily) but
  **filings-only and optional/not-wired**; Docker `app` runs `xar init && xar serve` once.

**Confirmed decisions:** (1) full universe (~947 cos) daily LLM semantic extraction (shard + incremental
for cost); (2) enrich the semantic layer with causal-chain / forward-vs-backward stance / narrative
fields; (3) Dagster sidecar as the daily runtime; (4) the daily pipeline does **not** auto-generate
reports (reports stay on-demand).

**Design spine:** reuse the three bitemporal tables as the "semantic DB"; **do not add a parallel
mega-table** (that would fork dedup / grounding / backtest / dashboard consumers). Instead:
**(A)** close source gaps (Finnhub/FMP news); **(B)** additively enrich the three tables (causal / stance /
narrative + ontology anchor + `as_of`) and add one unified `semantic_facts` view as the single
timestamped semantic-fact surface for the LLM and backtest; **(C)** build `run_daily()` + `ingest_runs`
log + Dagster sidecar that runs "all sources → docs → parse/embed → semantic extract → expert/signals"
incrementally each night.

```mermaid
flowchart LR
  subgraph Pillar C — daily runtime
    D[orchestration/daily.py<br/>run_daily shard k/N] --> RL[(ingest_runs<br/>run log + cursor)]
    DG[Dagster sidecar<br/>StaticPartitions×8 @6am] --> D
    CLI[xar daily CLI] --> D
  end
  subgraph Pillar A — sources
    FH[finnhub.pull_news]:::new
    FM[fmp.pull_news]:::new
    EX[edgar/cninfo/x/wechat<br/>reddit/aifinmarket/polymarket]
  end
  D --> FH & FM & EX
  FH & FM & EX --> DOC[(documents / social_posts)]
  DOC --> P[parse.parse_pending]
  P --> KG[kg.extract.build_kg<br/>+B.1 causal/stance/narrative]:::new
  P --> XP[kg.expert.process<br/>+as_of/theme/orientation]:::new
  KG --> EV[(kg_events +cols)]:::new
  KG --> ED[(kg_edges +causally_linked)]:::new
  XP --> EI[(expert_insights +cols)]:::new
  EV & EI --> V[[semantic_facts VIEW]]:::new
  V --> AG[agents/nodes graph_retrieve]
  V --> BT[backtest/catalyst_returns]
  classDef new fill:#dff,stroke:#0aa;
```

---

## Pillar A — close source gaps (feed the semantic DB from all sources)

- **`src/xar/providers/finnhub.py`** (the one real gap — today purely `structured.upsert_*`, no news, no
  `Doc`/`save`). Add — *this is the file's first use of `ingestion.base.Doc`/`save`*:
  - `pull_news(company_id, *, since=None, until=None) -> int`:
    `GET /stock/company-news?symbol=&from=&to=&token=` (Finnhub requires from/to, ≤1yr). Default window
    `today - settings.daily_news_lookback_days`. Each item →
    `save(Doc(company_id, source="finnhub", doc_type="news", title=headline, text=summary or headline,
    url, published_at=datetime.fromtimestamp(epoch, tz=utc), permission="grey",
    license_tag="finnhub-news-extracted-facts-self-use", meta={finnhub_id,category,source}))`.
    `Doc.id` = `finnhub:sha256(url+title+text[:200])[:20]` → overlapping windows dedup via `ON CONFLICT(id)`
    (base.py:46-66). Guard symbol with existing `us_ticker()` (finnhub.py:28).
  - `pull_general_news(category="technology")` (optional): `GET /news?category=`, `company_id=None`,
    same save path (mirrors `aifinmarket.pull_theme_news()` → `_save_docs(company_id=None,…)`).
  - Wire into existing `pull(company_id)`: add `out["news"] = pull_news(company_id, since=...)`.
- **`src/xar/providers/fmp.py`** (symmetric, second priority — also `structured`-only today):
  `pull_news(company_id, *, limit=20) -> int` → `/v3/stock_news?tickers=&limit=&apikey=` →
  `Doc(source="fmp", doc_type="news", permission="grey", license_tag="fmp-news-extracted-facts-self-use")`.
  Per-company only (registry = relevance filter); no broad discovery scan.
- **`src/xar/api/ops.py`**: register `finnhub_news` in `SOURCES` (ops.py:121 — fields
  `id/name/category=web/permission=grey/keyEnv=FINNHUB_API_KEY/table=documents/where="source='finnhub'"/runnable=True/desc`);
  add a `finnhub_news` branch in `run_source()` (ops.py:211): per-company `pull_news` → `parse.parse_pending()`
  → `kg_extract.build_kg()` → `expert.process(("finnhub",))`. Add `"finnhub","fmp"` to
  `expert.ALT_SOURCES` (expert.py:29 — currently `("wechat","x","news","aifinmarket","social","product")`)
  so news docs enter the expert channel.
- **No change needed** (already on the semantic path; only need to be in the daily loop): X (`providers/twitter.py`
  `pull_company`/`pull`), WeChat (`ingestion/wechat.py` `ingest`/`available`), Reddit (`reddit.pull_basket`),
  AIFINmarket (`aifinmarket.pull`). `build_kg` already extracts every non-`red` doc regardless of source, so
  finnhub/fmp news automatically reaches KG extraction.

---

## Pillar B — semantic-layer enrichment (additive, point-query, causal/stance)

### B.1 Extraction schema/prompt (decision 2: causality + stance)
- **`src/xar/ontology/schema.py` `ExtractedEvent`** (currently company/event_type/event_date/magnitude/
  polarity/tech_route_tag/summary/confidence/evidence) — add three fields:
  - `time_orientation: str = "backward_looking"` (`forward_looking` = guidance/orders/forecast; `backward_looking` = earnings/results).
  - `narrative: str = ""` (≤2-sentence causal/forward context — "why / will drive what", a step past `summary`).
  - `drivers: list[str] = []` (entities/factors driving the event).
- **`src/xar/kg/extract.py`**: extend the inline prompt (extract.py:110-122) to require LLM to fill
  `time_orientation`/`narrative`/`drivers`, and on explicit causal statements emit a `driver→company`
  causal edge (B.2). The grounding gate (`_grounded`, extract.py:73-93) still applies — if `narrative`
  isn't evidence-supported, blank it (don't drop the whole event). Tier stays `"fast"`.
- **`src/xar/kg/expert.py` `ExpertInsight`** (expert.py:32-40) — add `time_orientation`; `thesis` keeps
  carrying causal reasoning. In `process_document()` (expert.py:72-109) set `as_of` (= source doc
  `published_at::date`), `theme`, `segment` on the `expert_insights` upsert.

### B.2 Causal modeling (additive)
- Primary: store `kg_events.attrs.drivers` (JSONB) — zero schema risk.
- For drivers that resolve to a KG node, also emit one `kg_edges` row with a new
  `EdgeType.CAUSALLY_LINKED = "causally_linked"` (`src/xar/ontology/edges.py`), plus an `EDGE_IRI` entry
  in `src/xar/ontology/standards.py` (e.g. `f"{SCHEMA}/causeOf"` or `""` if no clean anchor — needed so
  JSON-LD export covers the new type). Bitemporal fields reuse `add_edge()` logic → "A's catalyst →
  drives B" becomes a point-queryable semantic edge.

### B.3 Additive columns + unified view (`src/xar/storage/schema.sql`, all `IF NOT EXISTS`/`OR REPLACE`, idempotent)
```sql
ALTER TABLE kg_events       ADD COLUMN IF NOT EXISTS theme            TEXT;
ALTER TABLE kg_events       ADD COLUMN IF NOT EXISTS segment          TEXT;
ALTER TABLE kg_events       ADD COLUMN IF NOT EXISTS narrative        TEXT;
ALTER TABLE kg_events       ADD COLUMN IF NOT EXISTS time_orientation TEXT;
ALTER TABLE kg_edges        ADD COLUMN IF NOT EXISTS theme            TEXT;
ALTER TABLE expert_insights ADD COLUMN IF NOT EXISTS as_of            DATE;
ALTER TABLE expert_insights ADD COLUMN IF NOT EXISTS theme            TEXT;
ALTER TABLE expert_insights ADD COLUMN IF NOT EXISTS segment          TEXT;
ALTER TABLE expert_insights ADD COLUMN IF NOT EXISTS time_orientation TEXT;
CREATE INDEX IF NOT EXISTS idx_expert_asof  ON expert_insights(as_of);
CREATE INDEX IF NOT EXISTS idx_events_theme ON kg_events(theme);

CREATE OR REPLACE VIEW semantic_facts AS
  SELECT 'event'::text AS kind, e.id::text AS id, e.company_id, e.event_type AS category,
         e.event_date AS as_of, e.observed_at, e.polarity, e.summary AS content,
         e.narrative, e.time_orientation, e.tech_route_tag, e.confidence,
         e.source_doc_id, e.license_tag, e.theme, e.segment
    FROM kg_events e WHERE e.invalidated_at IS NULL
  UNION ALL
  SELECT 'insight', x.id::text, x.company_id, x.catalyst_type,
         x.as_of, x.created_at, x.polarity, x.thesis,
         NULL, x.time_orientation, x.tech_route_tag, x.signal_quality,
         x.doc_id, 'expert', x.theme, x.segment
    FROM expert_insights x WHERE x.kept;
```
- Each row then satisfies four properties: point-query (`observed_at` + `event_date`/`as_of`),
  provenance (`source_doc_id`/`doc_id`), ontology anchor (`theme`/`segment` + `company_id` +
  `event_type`/`catalyst_type`), and stance/narrative/causality
  (`polarity`/`thesis`/`narrative`/`time_orientation`/`attrs.drivers`).
- **Idempotency basis (verified):** `init_schema()` (db.py:44-53) reads the whole file and executes it
  once with `autocommit=True`; schema.sql already uses `ALTER TABLE … ADD COLUMN IF NOT EXISTS`
  (line 25, `companies.themes`) — the new statements follow the same pattern. No migration runner, no
  destructive migration.

### B.4 Write + retrieve wiring
- **`src/xar/kg/store.py` `add_event()`** (store.py:71-91): add kwargs `theme=None, segment=None,
  narrative=None, time_orientation=None`; add a small `_anchor(company_id)` helper that reads the registry
  for the company's first theme + segment to fill defaults (no such helper exists today). The `dedup_key`
  formula (`company_id|event_type|event_date|magnitude|tech_route_tag`, store.py:76-78) is **unchanged** —
  the new columns are company-derived and don't change event identity.
- **`src/xar/retrieval/graphrag.py`**: add `semantic(company_id=None, theme=None, as_of=None, since=None,
  limit=100)` → `SELECT * FROM semantic_facts WHERE as_of<=%s [/ >=since] [AND company_id/theme]
  ORDER BY as_of DESC`. This is the single entry for "all semantic facts as of day D for a company/theme",
  unifying `kg_events` + `expert_insights` (closes the gap that `events()` covers only `kg_events`).
- **`src/xar/agents/nodes.py`**: in `graph_retrieve()` (nodes.py:39-58, alongside `supply_chain()` +
  `events()`), call `graphrag.semantic(cid, as_of=<snapshot>)` and fold the facts into the analyst brief
  (`_graph_brief`, nodes.py:61-74) → correct point-query → backtestable reasoning.

### B.5 Backtest extension — `src/xar/backtest/catalyst_returns.py`
- `backtest(horizons=(5,20), limit=500)` (line 76) currently `SELECT … FROM kg_events e JOIN companies c`
  grouped by dict key `(event_type, polarity)`. Change the driving query to
  `FROM semantic_facts s JOIN companies c ON c.id = s.company_id WHERE s.as_of IS NOT NULL` (keep the
  companies JOIN — the view has no `tickers`), and the aggregation key to
  `(category, polarity, kind, time_orientation)`. `as_of` = public-info timestamp (expert `as_of` defaults
  to doc `published_at`); the event-study t0 (first close on/after `as_of`, lines 90-102) is unchanged.
  This answers "does the semantic/sentiment layer predict forward returns" — especially the
  `forward_looking` subset.

---

## Pillar C — daily automatic system (core deliverable)

### C.1 Orchestrator `src/xar/orchestration/daily.py` (new — pure Python, no Dagster dep, unit-testable)
```
def run_daily(sources=None, *, since=None, full_universe=True,
              shard=None, n_shards=1, run_id=None) -> dict
```
Control flow (all calls compose existing functions):
```
s=get_settings(); enabled = sources or s.daily_enabled_sources.split(",")
run_id = run_id or llm.new_batch_run_id("batch")   # "batch" prefix → llm_max_usd_per_batch cap (llm.py:54,61-64)
parent = runlog.start("daily")
try:
  seed_companies(); store.bootstrap_seed()         # idempotent skeleton refresh
  ids = [c["id"] for c in COMPANIES]               # decision 1 = full universe daily
  if n_shards>1 and shard is not None: ids = ids[shard::n_shards]
  for src in enabled:                              # 1) incremental PULL; one source failing != whole run
    r=runlog.start(src); cur = since or runlog.last_success_ts(src)
    try:
      edgar/cninfo: for cid in ids: ingestion.<edgar|cninfo>.ingest_company(cid)
      finnhub: for cid in ids: finnhub.pull_news(cid, since=cur); finnhub.pull(cid)
      fmp:     for cid in ids: fmp.pull(cid); fmp.pull_news(cid)
      twitter: for cid in ids: twitter.pull_company(cid); twitter.pull()   # expert-handle sweep
      reddit:  reddit.pull_basket(ids)
      wechat:  if wechat.available(): ingestion.ingest_wechat()
      aifinmarket: for cid in ids: aifinmarket.pull(cid)
      polymarket:  polymarket.pull(); signals.derive_market_signals()
      runlog.finish(r,"ok")
    except Exception as e: runlog.finish(r,"failed",error=str(e))
  stats["chunks"] = parse.parse_pending()                                   # 2) parse+embed (incremental)
  stats["kg"]     = kg_extract.build_kg(limit=s.daily_kg_doc_limit, run_id=run_id)  # 3) semantic extract (incremental; B.1)
  stats["expert"] = expert.process(run_id=run_id)
  for cid in ids: signals.derive_for_company(cid)                           # 4) structured→ontology signals
  runlog.finish(parent,"ok",stats=stats)
except BudgetExceeded as e: runlog.finish(parent,"ok",stats={**stats,"budget_capped":str(e)})
except Exception as e: runlog.finish(parent,"failed",error=str(e)); raise
return stats
```
- **Idempotent/resumable (verified):** `save()` upserts by content hash (base.py:46-66); `parse_pending`
  only parses chunk-less docs; `build_kg` only takes docs with no `kg_*` row via `NOT EXISTS` on
  `source_doc_id` (extract.py:205-209); `expert.process` only takes docs with no `expert_insights` row
  (expert.py:116-118); `add_event`/`add_edge` dedup. After a crash the next run resumes via these
  `NOT EXISTS` cursors. `last_success_ts` only governs the pull window (over-fetch is harmless — `save` dedups).

### C.2 Run log `src/xar/storage/schema.sql` + `src/xar/storage/runlog.py` (new — closes the observability gap)
```sql
CREATE TABLE IF NOT EXISTS ingest_runs (
  id BIGSERIAL PRIMARY KEY, kind TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'running',         -- running|ok|failed|skipped
  since_ts TIMESTAMPTZ, stats JSONB NOT NULL DEFAULT '{}', error TEXT);
CREATE INDEX IF NOT EXISTS idx_ingest_runs_kind    ON ingest_runs(kind);
CREATE INDEX IF NOT EXISTS idx_ingest_runs_started ON ingest_runs(started_at DESC);
```
`runlog.py`: `start(kind, since_ts=None)->int`, `finish(run_id, status, stats=None, error=None)`,
`last_success_ts(kind)->datetime|None` (`max(finished_at) where status='ok'` — the per-source incremental cursor).

### C.3 Dagster sidecar (decision 3)
- **Rewrite `src/xar/orchestration/definitions.py`** — core logic moves to `daily.run_daily()` (dep-free);
  this file is just the Dagster wrapper:
  - Full-universe sharding: `StaticPartitionsDefinition([f"shard-{i}" for i in range(N)])`
    (N = `settings.daily_universe_shards`, default 8); `@asset(partitions_def=…)` calls
    `run_daily(full_universe=True, shard=i, n_shards=N)` → full daily coverage, each shard bounded and
    independently retryable.
  - `ScheduleDefinition(job=universe_daily, cron_schedule=f"0 {hour} * * *")` (keep 6am; nightly fires all partitions).
  - Keep an optional `core_job` (core 42 only, lighter), not scheduled.
- **Deps:** `pyproject.toml` already has the `orchestration` extra (`dagster>=1.7`, `dagster-webserver>=1.7`);
  install it in the dagster container only (main `app` image unchanged).
- **`docker-compose.yml`** — add a `dagster` service (reuse same image + `.env`, no init/serve duplication):
```yaml
  dagster:
    build: .
    restart: unless-stopped
    env_file: .env
    environment:
      XAR_DATABASE_URL: postgresql://xar:xar@db:5432/xar
      XAR_OBJECT_STORE: file:///data/objects
      DAGSTER_HOME: /dagster
    depends_on: { db: {condition: service_healthy}, app: {condition: service_started} }
    command: bash -lc "pip install '.[orchestration]' && dagster dev -m xar.orchestration.definitions -h 0.0.0.0 -p 3000"
    ports: ["3000:3000"]
    volumes: [ xar_data:/data, xar_models:/root/.cache, dagster_home:/dagster ]
```
  Only `app` runs `xar init && xar serve` (creates schema); `dagster` runs daemon+webserver (UI :3000,
  retries, backfills). Add a `dagster_home` named volume. Single worker — no distributed lock.
- **CLI fallback `src/xar/cli.py`** (Typer; no `daily` today): add
  `xar daily [--sources][--since auto|ISO|full][--shard k --n-shards N]` calling `run_daily` directly
  (ad-hoc/manual + ops-button trigger).

### C.4 Config `src/xar/config.py` (pydantic-settings, `XAR_` prefix; no `daily_*` today)
- `daily_enabled_sources: str = "edgar,cninfo,finnhub,fmp,twitter,reddit,wechat,aifinmarket,polymarket"`
- `daily_run_hour: int = 6`, `daily_universe_shards: int = 8`, `daily_news_lookback_days: int = 7`,
  `daily_kg_doc_limit: int = 800`
- Reuse existing `llm_max_usd_per_batch` (config.py:27, default 20.0) as the per-run/shard LLM budget gate.

### C.5 Cost/scale (the levers that make decision-1 "full universe daily" affordable — all pre-existing)
- **Incremental nature:** `build_kg`/`expert.process` touch only new docs → steady-state daily cost ≈
  "new docs today", not 947×corpus; Finnhub `since` window + `daily_kg_doc_limit` double-cap it.
- **Sharding:** universe split into 8 nightly shards, each bounded + independently retried (Dagster partition concurrency if parallel).
- **Budget gate:** `batch`-prefix run_id → `llm.complete_json` raises `BudgetExceeded` → graceful stop +
  `budget_capped` recorded.
- **Fast tier:** extract/expert already `tier="fast"` — unchanged.
- **Observability:** `ingest_runs.stats` per round (docs/events); `llm_usage` (llm.py `_record`) per run_id;
  ops `/api/ops/llm` already surfaces it.

---

## Changed-files summary
- **New:** `src/xar/orchestration/daily.py`, `src/xar/storage/runlog.py`
- **Edit `src/xar/storage/schema.sql`:** additive columns + `semantic_facts` view + `ingest_runs` (all idempotent)
- **Edit providers:** `finnhub.py` (+`pull_news`/`pull_general_news`, first `Doc`/`save` use), `fmp.py` (+`pull_news`)
- **Edit semantic layer:** `ontology/schema.py` (`ExtractedEvent` +3 fields), `ontology/edges.py`
  (+`CAUSALLY_LINKED`), `ontology/standards.py` (+`EDGE_IRI` entry), `kg/extract.py` (prompt + causal edge),
  `kg/expert.py` (`as_of`/`theme`/`segment`/`orientation`), `kg/store.py` (`add_event` kwargs + `_anchor`)
- **Edit consumers:** `retrieval/graphrag.py` (+`semantic()`), `agents/nodes.py` (inject semantic facts),
  `backtest/catalyst_returns.py` (read `semantic_facts`)
- **Edit runtime/config:** `orchestration/definitions.py` (Dagster wrapper + partitioned schedule),
  `cli.py` (+`daily`), `config.py` (`daily_*`), `api/ops.py` (register + run `finnhub_news`),
  `docker-compose.yml` (+`dagster` service + `dagster_home` volume)

## Reuse, not rewrite
Doc save `ingestion.base.save`/`polite`; parse+embed `parsing.parse.parse_pending`; semantic extract
`kg.extract.build_kg` + grounding gate; expert layer `kg.expert.process`; structured signals `kg.signals.*`;
entity resolution `kg.resolve`; point-query `retrieval.graphrag` + `retrieval.vector.hybrid_search`;
budget/usage `models.llm`; bitemporal schema (existing `kg_edges`/`kg_events` temporal columns).

## Storage deltas
**Zero new business mega-table, zero destructive migration.** Semantic enrichment = three tables' additive
columns + `semantic_facts` view + `attrs.drivers`; the one new table is `ingest_runs` (run log).
`init_schema()` idempotent re-run applies everything.

---

## Verification

### Unit (no DB/network)
- `finnhub.pull_news`: monkeypatch `get_json` to return sample company-news → assert Doc fields
  (source/permission/`published_at` from epoch/url/content hash stable across re-runs).
- `run_daily` dispatch: monkeypatch each connector to a counter → assert each enabled source is called over
  `ids`; one source raising does not abort the round (its `ingest_runs`='failed', others 'ok').
- `runlog.last_success_ts`: seed rows → returns only `status='ok'` `max(finished_at)`.
- `ExtractedEvent` new-field defaults; extract causal-edge construction.

### DB-gated (Postgres, existing gating convention)
- `init_schema()` run twice against a populated DB → no error, new columns present
  (`information_schema.columns`), `semantic_facts` queryable, `ingest_runs` exists (**the idempotency proof**).
- `store.add_event(theme,segment,narrative,time_orientation)` writes columns and still dedups by `dedup_key`.
- `expert.process_document` sets `as_of/theme/segment`; `semantic_facts` union returns both event and
  insight for the same company.
- `backtest()` over `semantic_facts` returns groups by `(category,polarity,kind,time_orientation)`, no CN-ticker loss.

### End-to-end (CLI + Dashboard/Ops API)
- `xar init` (applies additive schema) → `xar daily --sources finnhub --since 2026-06-01` (real
  `FINNHUB_API_KEY`) → `xar status` row counts grow, `ingest_runs` has daily+finnhub 'ok' rows.
- `GET /api/ops/sources` shows `finnhub_news` (rows>0, lastRun); `POST /api/ops/sources/finnhub_news/run`
  triggers bg pull; `GET /api/ops/altdata` reflects new-source expert output.
- `GET /api/backtest` includes semantic-layer signals; `GET /api/ui/company/<id>` signals/narrative reflect
  enriched `kg_events` (narrative/time_orientation).
- Start `dagster` service: UI :3000 shows `universe_daily` partitioned job; after the nightly schedule an
  `ingest_runs(kind='daily')` row appears; `app` unaffected (no duplicate init).
- `ruff check src tests scripts` clean; `pytest -q` green (incl. new tests); `docker compose up --build`
  still one-shot (app + db + dagster + optional werss).

## Out of scope
- No P5 decision/trading layer (still deferred, task #59).
- Daily pipeline does not auto-generate reports (decision 4); reports stay `xar report` on-demand.
- No LinkedIn / paid-terminal restricted sources; keep the extracted-facts self-use posture + permission tags.
- No change to the existing 5-chain + 3-cycle ontology semantic axes.
