# Andy (`src/slx`) — vendored upstream (siliconomics)

`src/slx` is a **plain vendored copy** (not a git subtree/submodule) of the
`siliconomics` 硅基经济指标库 (Silicon-Index: theory-anchored macro-indicator
registry + bitemporal point-in-time store + connectors + overclaim registry),
absorbed into XAR as the **XAR Andy** macro module under the same
"mount first, merge later" plan as Fenny.

- **Upstream:** https://github.com/qzjacob/xar-andi.git (project lives in `siliconomics/`)
- **Vendored commit:** `d9c411d` (Merge PR #1 — Phase 2 识别引擎)
- **Vendored on:** 2026-07-03
- **Tests:** upstream `siliconomics/tests/` → `tests/andy/` (28 tests: 21 offline, 7 `requires_db`).
- **Dropped (not vendored):** `dashboard/` (Streamlit — replaced by the React `/andy`
  module), `orchestration/dagster/` (replaced by `xar andy ...` CLI + the XAR daily
  pipeline's opt-in `macro` source), `dbt/` + `quality/soda/` (SQL-mirror parity tests
  only; the Python engines are the runtime truth here), upstream `pyproject.toml` /
  `docker-compose.yml` / `uv.lock` / root essays.

## Layout mapping

Upstream repo-root packages fold into the single `slx` package:

| upstream (`siliconomics/`) | vendored |
|---|---|
| `slx/{__init__,db}.py` | `src/slx/{__init__,db}.py` |
| `engine/` | `src/slx/engine/` |
| `ingestion/` (+ `connectors/`) | `src/slx/ingestion/` |
| `api/` (+ `routers/`) | `src/slx/api/` |
| `tools/` | `src/slx/tools/` |
| `registry/` (YAML + JSON schemas) | `src/slx/registry/` |
| `db/schema.sql` | `src/slx/schema.sql` (next to `db.py`, xar.storage idiom) |
| `tests/` | `tests/andy/` |

## Mechanical import rewrite (re-apply on every upstream re-sync)

Upstream imports its packages as top level (`from engine…`, `from ingestion…`,
`from tools…`; `from slx.db import connect` already matches). Three sed rules,
applied to `src/slx/**/*.py` and `tests/andy/**/*.py`:

```
s/from engine\./from slx.engine./g;    s/from engine import/from slx.engine import/g
s/from ingestion\./from slx.ingestion./g; s/from ingestion import/from slx.ingestion import/g
s/from tools\./from slx.tools./g;      s/from tools import/from slx.tools import/g
```

plus the two dynamic-import strings in `ingestion/discovery.py`
(`ingestion.connectors.*` / `ingestion.<source_id>` → `slx.ingestion.…`).
The `connector = "ingestion.connectors.x"` class attributes are audit-log
provenance labels, kept byte-identical to upstream.

## Local modifications (kept current)

1. **`src/slx/db.py`** — rewritten (~30 lines): drops dotenv; DSN from
   `SLX_DATABASE_URL` (fallback `DATABASE_URL`), bridged by `xar.api.andy_mount`
   from XAR settings; every connection pins `search_path={SLX_SCHEMA},public`
   (default `slx`) so **all slx objects live in a dedicated Postgres schema**
   inside XAR's shared DB (generic names like `observation`/`audit_log` cannot
   collide); adds `init_schema()` (CREATE SCHEMA + idempotent schema.sql).
   `tests/andy/conftest.py` sets `SLX_SCHEMA=slx_test` so the vendored tests run
   in a pristine sandbox — real connector data in `slx` never breaks their
   seed-only PIT assertions.
2. **`src/slx/schema.sql`** — de-TimescaleDB'd: `CREATE EXTENSION timescaledb` and the
   `create_hypertable('observation', …)` call removed (kept as comments). XAR's shared
   Postgres runs pgvector/pg_trgm only; at 43-metric cardinality the plain table +
   `idx_obs_pit` covers all point-in-time queries.
3. **`src/slx/ingestion/connectors/epoch_ai.py`** — one line: the upstream
   `_INFERENCE_PRICE_URL = None` TODO is completed as an env override
   (`SILICON_INFERENCE_PRICE_CSV_URL`); the documented field assumptions and the
   parse skeleton are unchanged, so the connector activates the moment a CSV link
   is supplied and stays a graceful no-op otherwise.
4. **`tests/andy/conftest.py`** — `REG` resolves via the `slx` package; DSN bridged from
   `xar.config` when `SLX_DATABASE_URL`/`DATABASE_URL` are unset; the `seeded` session
   fixture runs `slx.db.init_schema()` first (upstream relied on docker initdb).

Nothing else in `src/slx` is modified. The dependency points one way (`xar` → `slx`);
`slx` never imports `xar`. The XAR-side fusion lives outside the vendored tree:
`xar/api/andy_mount.py` (env bridge + mount), `xar/api/andy_links.py` (勾稽 API),
`xar/ontology/macro_links.py` (metric ↔ theme/segment/tech-route crosswalk),
`xar/ingestion/macro_bridge.py` (macro_print → kg_events/semantic_facts).

## Keys

Zero-key connectors run as-is: `sec_edgar` (uses `SEC_EDGAR_USER_AGENT`, bridged from
`XAR_EDGAR_IDENTITY`), `epoch_ai`, `fhfa`, `lbnl`, `indeed_hiring_lab`, `bls` (v1),
`stooq`. Free-registration keys unlock more: `FRED_API_KEY`, `BEA_API_KEY`,
`EIA_API_KEY`, `EMBER_API_KEY`, `ACLED_API_KEY`+`ACLED_EMAIL`, `TICKETMASTER_API_KEY`
(see `.env.example`). No secrets were present in the upstream repo.
