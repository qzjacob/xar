"""FastAPI app + built-in web UI. Turnkey: on startup it initializes the schema,
seeds the company basket, and bootstraps the KG seed graph (all idempotent)."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
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
    try:
        # Chathy 的 Telegram 通道(BOT_HTTP_API 在场即启;长轮询守护线程,记录与前端同源)
        from ..chathy import telegram

        telegram.start_background()
    except Exception as e:  # noqa: BLE001 — 通道失败绝不拖垮 API 启动
        log.warning("telegram channel not started: %s", e)


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


@app.get("/api/ops/futu")
def ops_futu() -> dict:
    """富途接入总览:资讯/资金流/板块覆盖 + 本体缺口(DB),外加一次 OpenD 连通性探测
    (opend_reachable=futu.available() —— enable_futu 时会连一次 OpenD 并缓存连接;
    管理页,低频)。"""
    from ..config import get_settings
    from ..ontology.altdata import bindings
    from ..providers import futu
    from ..storage import db

    docs = db.query("SELECT count(*) n FROM documents WHERE source='futu'")[0]["n"]
    flow = db.query("SELECT count(DISTINCT company_id) n FROM alt_signals "
                    "WHERE signal_key='alt.futu_main_capital_flow'")[0]["n"]
    plates = db.query("SELECT count(*) n, count(DISTINCT company_id) c FROM futu_plates")
    metrics = db.query("SELECT count(*) n FROM fundamentals WHERE source='futu'")[0]["n"]
    bound = sum(1 for b in bindings().values() if b.futu_code)
    return {
        "enabled": get_settings().enable_futu,
        "opend_reachable": futu.available(),
        "news_docs": docs, "snapshot_metrics": metrics,
        "capital_flow_companies": flow, "flow_bindable": bound,
        "plates": {"rows": plates[0]["n"], "companies": plates[0]["c"]},
        "ontology_gaps": futu.plate_theme_gaps(limit=30),
    }


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


@app.get("/api/ops/gangtise")
def ops_gangtise() -> dict:
    """Gangtise 投研接入总览:连通性 + 财报/估值/一致预期/投研文本覆盖(DB)。"""
    from . import ops

    return ops.gangtise()


@app.get("/api/ops/selftest")
def ops_selftest() -> dict:
    from . import ops

    return ops.selftest()


@app.get("/api/ops/coverage")
def ops_coverage() -> dict:
    """360° coverage: per-theme × per-dimension fill rates (ontology/coverage360)."""
    from ..ontology import coverage360

    return {"dimensions": [{"key": d.key, "name": d.name, "name_cn": d.name_cn,
                            "weight": d.weight} for d in coverage360.DIMENSIONS],
            "themes": coverage360.summary_by_theme()}


@app.get("/api/alt/company/{cid}")
def alt_company(cid: str):
    """另类数据信号快照 + 支柱信号分(高频校正面板)。"""
    from ..research import thesis_signals

    return {"signals": thesis_signals.signal_snapshot(cid),
            "pillar_scores": thesis_signals.pillar_signal_scores(cid)}


@app.get("/api/ops/wechat-mining")
def ops_wechat_mining():
    """微信挖掘面板:triage 保留率(vs 旧 3.75%)+ 策展名册 + 当前猎取目标。"""
    from ..mining import roster, targeting, triage

    try:
        tstats = triage.stats()
    except Exception:  # noqa: BLE001
        tstats = {}
    try:
        rstatus = roster.status()
    except Exception:  # noqa: BLE001
        rstatus = {}
    try:
        targets = [{"company_id": t.company_id, "name": t.name,
                    "priority": t.priority, "themes": list(t.themes),
                    "hunt_terms": list(t.hunt_terms_zh)[:6],
                    "watch_event_types": list(t.watch_event_types)[:4],
                    "challenged": bool(t.challenged_pillars)}
                   for t in targeting.build_targets(15)]
    except Exception:  # noqa: BLE001
        targets = []
    return {"triage": tstats, "roster": rstatus, "targets": targets}


@app.get("/api/ops/research-crawl")
def ops_research_crawl():
    """CN 非标语义抓取面板:各 doc_type 完整性 + 最近独立审计 + 回填态。"""
    from ..orchestration import research_audit
    from ..providers.gangtise import planner
    from ..storage import kvstate

    try:
        integrity = research_audit.integrity_report()
    except Exception as e:  # noqa: BLE001
        integrity = {"error": str(e)[:160]}
    try:
        backfill = planner.backfill_status()
    except Exception:  # noqa: BLE001
        backfill = {}
    return {"integrity": integrity, "backfill": backfill,
            "lastAudit": kvstate.get_state("research_audit")}


@app.get("/api/ops/earnings")
def ops_earnings():
    """季报事件面板:观察窗队列 + 最近裁决 + conviction 校准。"""
    from .. import config
    from ..ontology.earnings_events import EARNINGS_UNIVERSE
    from ..research import earnings
    from ..storage import db, structured

    s = config.get_settings()
    out: dict = {}
    try:
        rows = structured.upcoming_calendar(list(EARNINGS_UNIVERSE),
                                            days=s.earnings_watch_days + 5, limit=100)
        queue = []
        for r in rows:
            if r.get("event_type") != "earnings":
                continue
            v = earnings.latest_verdict(r["company_id"], r["scheduled_for"])
            queue.append({"cid": r["company_id"], "date": str(r["scheduled_for"]),
                          "session": (r.get("meta") or {}).get("session"),
                          "verdict": ({"direction": v["direction"], "conviction": v["conviction"],
                                       "version": v["version"]} if v else None)})
        out["queue"] = queue
    except Exception as e:  # noqa: BLE001
        out["queue"] = {"error": str(e)[:160]}
    try:
        recent = db.query(
            "SELECT company_id, event_date, direction, conviction, model, as_of, "
            "outcome->>'direction_hit' hit FROM earnings_verdicts ORDER BY created_at DESC LIMIT 15")
        out["recent"] = [dict(r) for r in recent]
    except Exception as e:  # noqa: BLE001
        out["recent"] = {"error": str(e)[:160]}
    try:
        out["calibration"] = earnings.calibration()
    except Exception as e:  # noqa: BLE001
        out["calibration"] = {"error": str(e)[:160]}
    return out


@app.post("/api/ops/earnings/{cid}/judge")
def ops_earnings_judge(cid: str, bg: BackgroundTasks, force: bool = False):
    """后台生成某公司季报裁决。UA-P1:走统一 capability_runs(返回 run_id 可轮询;活跃去重防双跑)。"""
    from ..capabilities import runs

    sched = runs.schedule("build_earnings_verdict", {"company_id": cid, "force": force}, origin="ui")
    bg.add_task(runs.execute_run, sched["run_id"])
    return {**sched, "status": "scheduled", "company_id": cid, "force": force,
            "run_id": sched["run_id"]}


@app.get("/api/ops/altdata/trackers")
def ops_alt_trackers():
    """alt 追踪器覆盖 + 每信号库存(ops 面板)。"""
    from ..ontology import altdata
    from ..storage import db

    try:
        stock = db.query("SELECT signal_key, count(*) AS rows, count(DISTINCT company_id) AS companies, "
                         "max(period_end) AS latest FROM alt_signals GROUP BY 1 ORDER BY 1")
        stock = [{**dict(r), "latest": str(r["latest"])} for r in stock]
    except Exception:  # noqa: BLE001
        stock = []
    return {"coverage": altdata.coverage_summary(),
            "signals": [{"key": x.key, "name_cn": x.name_cn, "cadence": x.cadence,
                         "scope": x.scope, "good_when": x.good_when, "source": x.source}
                        for x in altdata.ALT_SIGNALS],
            "stock": stock}


@app.post("/api/thesis/{cid}/build")
def thesis_build(cid: str, force: bool = False):
    """Build/refresh the company's investment thesis (sync; seconds on the bulk pool)."""
    from ..research import thesis

    out = thesis.build(cid, force=force)
    if out["status"] == "no_data" and out.get("reason") == "unknown company":
        raise HTTPException(status_code=404, detail=out["reason"])
    return out


@app.get("/api/thesis/{cid}/health")
def thesis_health(cid: str) -> dict:
    """争论感知健康度(health_v3:事件⊕信号⊕争论天平)。"""
    from ..research import thesis_health as thh

    h = thh.health_v3(cid)
    if h is None:
        raise HTTPException(status_code=404, detail="no thesis")
    return h


@app.get("/api/thesis/{cid}/links")
def thesis_links(cid: str, limit: int = 50) -> dict:
    """最近的证据裁决(相对主张链接 + VP 数值裁决)。"""
    from ..storage import db

    rows = db.query(
        "SELECT as_of, fact_kind, fact_ref, target_kind, target_key, verdict, strength, "
        "origin, rationale_zh, created_at FROM thesis_fact_links "
        "WHERE company_id=%s AND target_kind<>'none' ORDER BY created_at DESC LIMIT %s",
        (cid, limit))
    return {"company_id": cid, "links": rows}


@app.get("/api/themes/{tid}/debates")
def theme_debates(tid: str) -> dict:
    """主题级核心争论健康度(成员旗舰 lean 聚合 + 翻转清单)。"""
    from ..research import thesis_health as thh

    return thh.theme_debate_health(tid)


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


# ── UA-P1:统一能力入口(一处定义 → Chathy/UI/CLI/API 共用)────────────────────────────
@app.get("/api/capabilities")
def list_capabilities():
    """能力登记簿清单(name/kind/duration/chathy/description)。"""
    from ..capabilities import registry

    return [{"name": c.name, "kind": c.kind, "duration": c.duration, "chathy": c.chathy,
             "description": c.description, "parameters": c.parameters} for c in registry.CAPABILITIES]


@app.post("/api/run/{name}")
def run_capability(name: str, bg: BackgroundTasks, body: dict | None = None):
    """跑一个能力。read/fast → 内联执行返回 {status:'done', result};
    build/slow → schedule + 后台 execute_run,返回 {run_id, status, dedup?}。未知能力 404。"""
    from ..capabilities import registry, runs

    spec = registry.by_name(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown capability {name}")
    args = body or {}
    if spec.kind == "read" and spec.duration == "fast":
        import json as _json
        return {"status": "done", "result": _json.loads(registry.execute(name, args))}
    sched = runs.schedule(name, args, origin="api")
    bg.add_task(runs.execute_run, sched["run_id"])
    return sched


@app.get("/api/run/{run_id}")
def get_run(run_id: str):
    from ..capabilities import runs

    st = runs.status(run_id)
    if st is None:
        raise HTTPException(status_code=404, detail="run not found")
    return st


@app.get("/api/runs")
def list_runs(capability: str = None, limit: int = 20):
    from ..capabilities import runs

    return runs.recent(capability, limit=min(int(limit or 20), 100))


# legacy vanilla-UI assets
if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
# compiled SPA assets (hashed JS/CSS under /assets)
if (_WEBDIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(_WEBDIST / "assets")), name="assets")

# Chathy (conversational analyst) — session CRUD + streaming chat (SSE).
@app.post("/api/chathy/sessions")
def chathy_create_session(body: dict | None = None):
    from . import chathy
    return chathy.create_session((body or {}).get("title"))


@app.get("/api/chathy/sessions")
def chathy_list_sessions():
    from . import chathy
    return chathy.list_sessions()


@app.get("/api/chathy/sessions/{sid}/messages")
def chathy_get_messages(sid: str):
    from . import chathy
    return chathy.get_messages(sid)


@app.delete("/api/chathy/sessions/{sid}")
def chathy_delete_session(sid: str):
    from . import chathy
    return chathy.delete_session(sid)


@app.post("/api/chathy/sessions/{sid}/chat")
def chathy_chat(sid: str, body: dict):
    from . import chathy
    return chathy.chat_stream(sid, (body or {}).get("message", ""))


# Genny Data Room — upload / browse / download report documents per theme·segment.
@app.post("/api/genny/dataroom/upload")
async def dataroom_upload(background: BackgroundTasks, file: UploadFile = File(...),
                          theme: str = Form(...), segment: str | None = Form(None),
                          company_id: str | None = Form(None), doc_type: str = Form("report"),
                          title: str | None = Form(None)):
    from . import dataroom
    data = await file.read()
    try:
        res = dataroom.ingest_upload(
            data=data, filename=file.filename or "upload", content_type=file.content_type or "",
            theme=theme, segment=segment, company_id=company_id, doc_type=doc_type, title=title)
    except ValueError as e:
        raise HTTPException(status_code=415, detail=str(e))
    from ..parsing import parse
    background.add_task(parse.parse_document, res["id"])   # chunk + embed THIS doc
    return res


@app.get("/api/genny/dataroom/docs")
def dataroom_docs(theme: str | None = None, segment: str | None = None,
                  company_id: str | None = None, q: str | None = None):
    from . import dataroom
    return dataroom.list_docs(theme=theme, segment=segment, company_id=company_id, q=q)


@app.get("/api/genny/dataroom/docs/{doc_id}/download")
def dataroom_download(doc_id: str):
    from . import dataroom
    got = dataroom.get_download(doc_id)
    if got is None:
        raise HTTPException(status_code=404, detail="document or artifact not found")
    data, content_type, filename = got
    return Response(content=data, media_type=content_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.delete("/api/genny/dataroom/docs/{doc_id}")
def dataroom_delete(doc_id: str):
    from . import dataroom
    return {"deleted": dataroom.delete_doc(doc_id)}


# Fenny (structured-notes / options desk) — vendored sub-app mounted under /api/fenny.
# MUST be registered before the SPA catch-all below, or its routes never match. Wrapped
# so a broken/absent fcn dependency can't stop the rest of XAR from booting.
try:
    from .fenny_mount import get_fenny_app
    app.mount("/api/fenny", get_fenny_app())
except Exception as e:  # noqa: BLE001
    log.warning("fenny module not mounted: %s", e)


# Andy 勾稽 (crosswalk) — XAR-native macro ↔ industry-chain fusion routes. These are
# registered BEFORE the /api/andy mount below so they shadow it for their exact paths
# (Starlette matches in registration order); do not move them after the mount.
@app.get("/api/andy/link/themes")
def andy_link_themes():
    from . import andy_links
    return andy_links.link_themes()


@app.get("/api/andy/link/theme/{theme}")
def andy_link_theme(theme: str, as_of: str | None = None):
    from . import andy_links
    out = andy_links.link_theme(theme, as_of)
    if out is None:
        raise HTTPException(status_code=404, detail=f"unknown theme {theme}")
    return out


@app.get("/api/andy/link/metric/{metric_key}")
def andy_link_metric(metric_key: str):
    from . import andy_links
    out = andy_links.link_metric(metric_key)
    if out is None:
        raise HTTPException(status_code=404, detail=f"metric {metric_key} has no crosswalk entry")
    return out


@app.post("/api/andy/link/sync-events")
def andy_link_sync_events(as_of: str | None = None):
    from . import andy_links
    return andy_links.sync_events(as_of)


@app.get("/api/andy/sources")
def andy_sources():
    """数据源面板:连接器运行状态 + key 就绪(布尔) + 指标观测新鲜度。"""
    from . import andy_links
    return andy_links.sources_status()


# Andy (siliconomics macro-indicator platform) — vendored sub-app mounted under /api/andy.
# XAR-native 勾稽 routes (/api/andy/link/*) are @app.get-registered above and shadow the
# mount for their exact paths; everything else under /api/andy/* falls through to the slx
# app. MUST stay before the SPA catch-all below.
try:
    from .andy_mount import get_andy_app
    app.mount("/api/andy", get_andy_app())
except Exception as e:  # noqa: BLE001
    log.warning("andy module not mounted: %s", e)


@app.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
def spa_fallback(full_path: str):
    """SPA client-side routing fallback. API/doc/asset paths fall through to 404."""
    if full_path.startswith(("api", "static", "assets", "legacy", "docs", "redoc", "openapi")):
        return JSONResponse({"error": "not found"}, status_code=404)
    idx = _spa_index()
    return HTMLResponse((idx or (_STATIC / "index.html")).read_text())
