"""FastAPI app + built-in web UI. Turnkey: on startup it initializes the schema,
seeds the company basket, and bootstraps the KG seed graph (all idempotent)."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import get_settings
from ..logging import get_logger
from ..storage import db

log = get_logger("xar.api")
_STATIC = Path(__file__).with_name("static")
# Compiled React SPA (web/dist). Docker sets XAR_WEB_DIST=/app/webdist; otherwise
# we look for a sibling webdist/. Absent (plain pip install w/o a build) -> the
# legacy vanilla UI is served at / so that path stays turnkey.
_WEBDIST = Path(os.getenv("XAR_WEB_DIST") or Path(__file__).with_name("webdist"))


def _spa_index() -> Path | None:
    idx = _WEBDIST / "index.html"
    return idx if idx.exists() else None

app = FastAPI(title="XAR — Industry-Chain Investment Research", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    try:
        db.init_schema()
        from ..ingestion import seed_companies
        from ..kg import store

        seed_companies()
        store.bootstrap_seed()
        log.info("startup complete; LLM configured=%s", get_settings().has_llm)
    except Exception as e:  # don't crash the server if DB is briefly unavailable
        log.warning("startup deferred: %s", e)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Serve the compiled React SPA when present, else the legacy vanilla UI."""
    idx = _spa_index()
    return (idx or (_STATIC / "index.html")).read_text()


@app.get("/legacy", response_class=HTMLResponse)
def legacy() -> str:
    """The original zero-build vanilla UI (kept as a fallback / reference)."""
    return (_STATIC / "index.html").read_text()


@app.get("/api/health")
def health() -> dict:
    s = get_settings()
    ok = True
    try:
        db.query("SELECT 1")
    except Exception:
        ok = False
    from .. import providers

    return {"ok": ok, "llm_configured": s.has_llm, "embed_model": s.embed_model,
            "model_strong": s.model_strong, "model_fast": s.model_fast,
            "data_posture": s.data_posture, "providers": providers.status()}


@app.get("/api/companies")
def companies() -> list[dict]:
    return db.query(
        "SELECT id,name,tickers,region,chain_role, "
        "(SELECT count(*) FROM documents d WHERE d.company_id=c.id) AS docs, "
        "(SELECT count(*) FROM kg_events e WHERE e.company_id=c.id) AS events "
        "FROM companies c ORDER BY region, name"
    )


class IngestReq(BaseModel):
    company_ids: list[str] | None = None
    edgar_limit: int = 6
    cn_limit: int = 15


@app.post("/api/ingest")
def ingest(req: IngestReq, bg: BackgroundTasks) -> dict:
    from .. import ingestion
    from ..kg import extract as kg_extract
    from ..parsing import parse

    def _job() -> None:
        from ..ingestion.registry import COMPANIES

        ids = req.company_ids or [c["id"] for c in COMPANIES]
        for cid in ids:
            try:
                ingestion.ingest_company(cid, edgar_limit=req.edgar_limit, cn_limit=req.cn_limit)
            except Exception as e:
                log.warning("ingest %s failed: %s", cid, e)
        parse.parse_pending()
        kg_extract.build_kg()
        log.info("ingest job complete")

    bg.add_task(_job)
    return {"status": "started", "companies": req.company_ids or "all"}


class ReportReq(BaseModel):
    kind: str = "deep_report"          # deep_report | tracking_summary | takeaways
    company_id: str
    since: str | None = None
    auto_approve: bool = False


@app.post("/api/report")
def report(req: ReportReq) -> dict:
    from ..agents import run_report

    return run_report(req.model_dump(), auto_approve=req.auto_approve)


@app.post("/api/report/{run_id}/approve")
def approve(run_id: str) -> dict:
    from ..agents import approve as _approve

    return _approve(run_id)


@app.get("/api/report/{run_id}")
def get_report(run_id: str) -> dict:
    from ..agents import get_report as _get

    r = _get(run_id)
    return r or JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/runs")
def runs() -> list[dict]:
    return db.query(
        "SELECT id, kind, status, request->>'company_id' AS company_id, created_at "
        "FROM report_runs ORDER BY created_at DESC LIMIT 50"
    )


@app.get("/api/providers")
def providers_status() -> dict:
    from .. import providers
    from ..ingestion import wechat

    return providers.status() | {"wechat": wechat.available()}


@app.post("/api/ingest/wechat")
def ingest_wechat(bg: BackgroundTasks) -> dict:
    from ..ingestion import wechat

    if not wechat.available():
        return {"status": "skipped", "reason": "WERSS_BASE_URL not set"}

    def _job() -> None:
        from .. import ingestion
        from ..kg import extract as kg_extract
        from ..parsing import parse

        try:
            n = len(ingestion.ingest_wechat())
            parse.parse_pending()
            kg_extract.build_kg()
            log.info("wechat ingest complete: %d articles", n)
        except Exception as e:
            log.warning("wechat ingest failed: %s", e)

    bg.add_task(_job)
    return {"status": "started", "source": "wechat"}


class PullReq(BaseModel):
    company_ids: list[str] | None = None
    social: bool = True


@app.post("/api/pull")
def pull(req: PullReq, bg: BackgroundTasks) -> dict:
    from .. import providers

    def _job() -> None:
        try:
            if req.company_ids and len(req.company_ids) == 1:
                providers.pull_company(req.company_ids[0], with_social=req.social)
            else:
                providers.pull_basket(req.company_ids, with_social=req.social)
            # newly mirrored social -> embed + extract into the ontology
            from ..kg import extract as kg_extract
            from ..parsing import parse

            parse.parse_pending()
            kg_extract.build_kg()
        except Exception as e:
            log.warning("pull job failed: %s", e)

    bg.add_task(_job)
    return {"status": "started", "companies": req.company_ids or "all"}


@app.get("/api/fundamentals/{company_id}")
def fundamentals(company_id: str) -> list[dict]:
    from ..storage import structured

    return structured.latest_fundamentals(company_id)


@app.get("/api/estimates/{company_id}")
def estimates(company_id: str) -> list[dict]:
    return db.query(
        "SELECT metric,period,period_end,value,high,low,n_analysts,source,as_of "
        "FROM estimates WHERE company_id=%s ORDER BY as_of DESC, metric LIMIT 100",
        (company_id,))


@app.get("/api/prices/{company_id}")
def prices(company_id: str, days: int = 180) -> list[dict]:
    return db.query(
        "SELECT d,close,volume,source FROM prices WHERE company_id=%s "
        "ORDER BY d DESC LIMIT %s", (company_id, days))


@app.get("/api/prediction-markets")
def prediction_markets() -> list[dict]:
    return db.query(
        "SELECT market_id,question,outcome,probability,volume,close_date,company_id,"
        "tech_route_tag FROM prediction_markets ORDER BY volume DESC NULLS LAST LIMIT 60")


@app.get("/api/social/{company_id}")
def social(company_id: str) -> list[dict]:
    return db.query(
        "SELECT platform,author,url,posted_at,sentiment,metrics,left(text,400) AS excerpt "
        "FROM social_posts WHERE company_id=%s ORDER BY posted_at DESC NULLS LAST LIMIT 50",
        (company_id,))


@app.get("/api/signals/{company_id}")
def signals(company_id: str) -> list[dict]:
    return db.query(
        "SELECT event_type,event_date,polarity,magnitude,summary,confidence "
        "FROM kg_events WHERE company_id=%s AND license_tag='signal' "
        "ORDER BY event_date DESC NULLS LAST, id DESC LIMIT 80", (company_id,))


@app.get("/api/graph/{company_id}")
def graph(company_id: str) -> dict:
    from ..retrieval import graphrag

    sc = graphrag.supply_chain(company_id)
    evs = graphrag.events(company_id, limit=60)
    return {"supply_chain": sc, "events": evs}


@app.get("/api/backtest")
def backtest() -> dict:
    from ..backtest import backtest as _bt

    return _bt()


@app.get("/api/eval")
def evaluate() -> dict:
    from ..eval import eval_retrieval

    return eval_retrieval()


# --- UI dashboard endpoints (real-data shapes the React terminal consumes) ---
@app.get("/api/ui/overview")
def ui_overview(theme: str = "ai_optical") -> dict:
    from . import dashboard

    return dashboard.overview(theme)


@app.get("/api/ui/companies")
def ui_companies(theme: str = "ai_optical") -> list[dict]:
    from . import dashboard

    return dashboard.companies(theme)


@app.get("/api/ui/signals")
def ui_signals(theme: str = "ai_optical") -> list[dict]:
    from . import dashboard

    return dashboard.signals(theme)


@app.get("/api/ui/catalysts")
def ui_catalysts(theme: str = "ai_optical") -> list[dict]:
    from . import dashboard

    return dashboard.catalysts(theme)


@app.get("/api/ui/calendar")
def ui_calendar(theme: str = "ai_optical", days: int = 90) -> list[dict]:
    from . import dashboard

    return dashboard.calendar(theme, days=days)


@app.get("/api/ui/landscape")
def ui_landscape(theme: str = "ai_optical") -> dict:
    from . import dashboard

    return dashboard.landscape(theme)


@app.get("/api/ui/company/{cid}")
def ui_company(cid: str, theme: str | None = None):
    from . import dashboard

    return dashboard.company_detail(cid, theme) or JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/ui/segment/{sid}")
def ui_segment(sid: str):
    from . import dashboard

    return dashboard.segment_detail(sid) or JSONResponse({"error": "not found"}, status_code=404)


# --- Operations control plane (ontology / sources / llm / connectors / skills
#     / data-lake / self-test) consumed by the React console ---
@app.get("/api/ops/ontology")
def ops_ontology() -> dict:
    from . import ops

    return ops.ontology()


@app.get("/api/ops/sources")
def ops_sources() -> dict:
    from . import ops

    return ops.sources()


@app.post("/api/ops/sources/{sid}/run")
def ops_run_source(sid: str, bg: BackgroundTasks) -> dict:
    from . import ops

    if not any(s["id"] == sid and s["runnable"] for s in ops.SOURCES):
        return {"status": "not_runnable", "source": sid}
    bg.add_task(ops.run_source, sid)
    return {"status": "started", "source": sid}


@app.get("/api/ops/llm")
def ops_llm() -> dict:
    from . import ops

    return ops.llm()


@app.post("/api/ops/llm/test")
def ops_llm_test() -> dict:
    from . import ops

    return ops.test_llm()


@app.post("/api/ops/llm/route")
def ops_llm_route(body: dict) -> dict:
    """Runtime route override: {key, model_id} re-points a capability/task to a model
    live (empty model_id clears it). No redeploy."""
    from . import ops

    return ops.set_route(str(body.get("key", "")), str(body.get("model_id", "")))


@app.get("/api/ops/connectors")
def ops_connectors() -> dict:
    from . import ops

    return ops.connectors()


@app.get("/api/ops/skills")
def ops_skills() -> dict:
    from . import ops

    return ops.skills()


@app.get("/api/ops/datalake")
def ops_datalake() -> dict:
    from . import ops

    return ops.datalake()


@app.get("/api/ops/datalake/documents")
def ops_datalake_documents(limit: int = 40, offset: int = 0,
                           source: str | None = None, q: str | None = None) -> dict:
    from . import ops

    return ops.datalake_documents(limit=limit, offset=offset, source=source, q=q)


@app.post("/api/ops/datalake/process")
def ops_datalake_process(bg: BackgroundTasks) -> dict:
    from . import ops

    bg.add_task(ops.process_datalake)
    return {"status": "started"}


@app.get("/api/ops/altdata")
def ops_altdata() -> dict:
    from . import ops

    return ops.altdata()


@app.post("/api/ops/altdata/process")
def ops_altdata_process(bg: BackgroundTasks) -> dict:
    from . import ops

    bg.add_task(ops.process_altdata)
    return {"status": "started"}


@app.get("/api/ops/selftest")
def ops_selftest() -> dict:
    from . import ops

    return ops.selftest()


# --- Exploration module (frontier research) -------------------------------
@app.get("/api/exploration/overview")
def exploration_overview() -> dict:
    from . import exploration

    return exploration.overview()


@app.get("/api/exploration/section/{domain_id}")
def exploration_section(domain_id: str) -> dict:
    from . import exploration

    r = exploration.section(domain_id)
    if r is None:
        raise HTTPException(status_code=404, detail="unknown frontier domain")
    return r


@app.post("/api/exploration/refresh")
def exploration_refresh(bg: BackgroundTasks, domain: str = None) -> dict:
    """Ingest latest preprints/voices + re-synthesize fronts (background)."""
    from ..exploration import ingest, synthesis

    def _job() -> None:
        if domain:
            ingest.ingest_domain(domain)
            synthesis.synthesize(domain)
        else:
            ingest.ingest_all()
            synthesis.synthesize_all()

    bg.add_task(_job)
    return {"status": "started", "domain": domain or "all"}


# legacy vanilla-UI assets
if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
# compiled SPA assets (hashed JS/CSS under /assets)
if (_WEBDIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(_WEBDIST / "assets")), name="assets")


@app.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
def spa_fallback(full_path: str):
    """SPA client-side routing fallback. API/doc/asset paths fall through to 404."""
    if full_path.startswith(("api", "static", "assets", "legacy", "docs", "redoc", "openapi")):
        return JSONResponse({"error": "not found"}, status_code=404)
    idx = _spa_index()
    return HTMLResponse((idx or (_STATIC / "index.html")).read_text())
