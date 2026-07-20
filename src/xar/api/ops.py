"""Control plane — the operations/management layer behind the React console.

Introspects and operates the whole platform from real state: the ontology
(types + standard IRIs + DB counts), the data-source registry (ingestion
connectors + market/alt providers, availability + posture + row counts + run
triggers), LLM vendors/models/routing/usage, MCP & API connectors, the agent
skill graph, and the unstructured data-lake (documents/chunks browse + process).
Plus a self-test that "runs through" every ontology type and data source.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timezone

from ..config import get_settings
from ..logging import get_logger
from ..storage import db

log = get_logger("xar.ops")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _count(table: str, where: str | None = None) -> int:
    sql = f"SELECT count(*) AS c FROM {table}"
    if where:
        sql += f" WHERE {where}"
    try:
        return db.query(sql)[0]["c"]
    except Exception:
        return 0


def _can_import(mod: str) -> bool:
    try:
        importlib.import_module(mod)
        return True
    except Exception:
        return False


# ===========================================================================
# 1) ONTOLOGY
# ===========================================================================
def ontology() -> dict:
    from ..ontology import CATALYST_TYPES, EDGE_TYPES, NODE_TYPES, edge_iri, node_iri
    from ..ontology.catalysts import CatalystType
    from ..ontology.standards import (
        FIBO,
        FINNHUB_METRIC_MAP,
        FMP_MAP,
        RATIO_METRICS,
        SCHEMA,
        SIGNAL_TO_CATALYST,
        YAHOO_INFO_MAP,
        FinMetric,
    )

    def cnt(table, col, val):
        rows = db.query(f"SELECT count(*) c FROM {table} WHERE {col}=%s", (val,))
        return rows[0]["c"] if rows else 0

    from ..ontology import metric_packs as packs
    from ..ontology.sectors import INDUSTRY_SECTOR, SECTORS

    cat_labels = {c.value: c.name.replace("_", " ").title() for c in CatalystType}
    # provider field coverage per canonical metric
    prov_for = {m: [] for m in [x.value for x in FinMetric]}
    for src, mp in (("fmp", FMP_MAP), ("finnhub", FINNHUB_METRIC_MAP), ("yahoo", YAHOO_INFO_MAP)):
        for _field, canon in mp.items():
            if src not in prov_for.get(canon, []):
                prov_for.setdefault(canon, []).append(src)

    return {
        "nodeTypes": [
            {"type": t, "schemaIri": node_iri(t, "schema"), "fiboIri": node_iri(t, "fibo"),
             "count": cnt("kg_nodes", "node_type", t)}
            for t in NODE_TYPES
        ],
        "edgeTypes": [
            {"type": t, "iri": edge_iri(t), "count": cnt("kg_edges", "rel_type", t)}
            for t in EDGE_TYPES
        ],
        "catalystTypes": [
            {"type": t, "label": cat_labels.get(t, t), "count": cnt("kg_events", "event_type", t)}
            for t in CATALYST_TYPES
        ],
        "finMetrics": [
            {"metric": m.value, "isRatio": m.value in RATIO_METRICS,
             "providers": sorted(prov_for.get(m.value, [])),
             "count": cnt("fundamentals", "metric", m.value)}
            for m in FinMetric
        ],
        "signalMap": SIGNAL_TO_CATALYST,
        # whole-economy sector taxonomy + pluggable operating-metric packs (the moat)
        "sectors": [
            {"sector": s, "industries": sorted(i for i, sec in INDUSTRY_SECTOR.items() if sec == s)}
            for s in SECTORS
        ],
        "metricPacks": [
            {"classifier": c, "count": len(keys),
             "metrics": [{"key": k, "label": packs.SPEC_BY_KEY[k].label,
                          "unit": packs.SPEC_BY_KEY[k].unit,
                          "higherIsBetter": packs.SPEC_BY_KEY[k].higher_is_better,
                          "count": cnt("fundamentals", "metric", k)} for k in keys]}
            for c, keys in sorted(packs.PACK_FOR.items()) if c != "*"
        ],
        "standards": {"fibo": FIBO, "schema": SCHEMA},
        "totals": {
            "nodes": _count("kg_nodes"), "edges": _count("kg_edges"),
            "events": _count("kg_events"), "aliases": _count("entity_aliases"),
        },
    }


# ===========================================================================
# 2) DATA SOURCES
# ===========================================================================
SOURCES: list[dict] = [
    {"id": "edgar", "name": "SEC EDGAR", "category": "filing", "permission": "green",
     "keyEnv": "XAR_EDGAR_IDENTITY", "table": "documents", "where": "source='edgar'",
     "runnable": True, "desc": "US filings 10-K/Q/8-K/20-F via edgartools (public domain)."},
    {"id": "cninfo", "name": "cninfo 巨潮 (A股)", "category": "filing", "permission": "green",
     "keyEnv": None, "table": "documents", "where": "source='cninfo'",
     "runnable": True, "desc": "CN statutory disclosure via AKShare (research = metadata only)."},
    {"id": "news", "name": "News / Product pages", "category": "web", "permission": "grey",
     "keyEnv": None, "table": "documents", "where": "source='news'",
     "runnable": False, "desc": "Polite fetch + trafilatura main-content extraction."},
    {"id": "jobs", "name": "ATS Hiring", "category": "web", "permission": "green",
     "keyEnv": None, "table": "documents", "where": "source='jobs'",
     "runnable": False, "desc": "Greenhouse/Lever/Ashby official ATS APIs (hiring signal)."},
    {"id": "wechat", "name": "微信公众号 (we-mp-rss)", "category": "web", "permission": "grey",
     "keyEnv": "WERSS_BASE_URL", "table": "documents", "where": "source='wechat'",
     "runnable": True, "desc": "WeChat Official-Account articles via a we-mp-rss service."},
    {"id": "yahoo", "name": "Yahoo Finance", "category": "market", "permission": "grey",
     "keyEnv": None, "table": "prices", "where": "source='yahoo'",
     "runnable": True, "desc": "Free global prices + fundamentals (yfinance); incl. A-shares."},
    {"id": "finnhub", "name": "Finnhub", "category": "market", "permission": "grey",
     "keyEnv": "FINNHUB_API_KEY", "table": "fundamentals", "where": "source='finnhub'",
     "runnable": True, "desc": "Fundamentals / estimates / ratings / insider transactions."},
    {"id": "fmp", "name": "FMP", "category": "market", "permission": "grey",
     "keyEnv": "FMP_API_KEY", "table": "fundamentals", "where": "source='fmp'",
     "runnable": True, "desc": "Statements / analyst estimates / price targets / daily prices."},
    {"id": "finnhub_news", "name": "Finnhub News", "category": "web", "permission": "grey",
     "keyEnv": "FINNHUB_API_KEY", "table": "documents", "where": "source='finnhub'",
     "runnable": True, "desc": "Company news headlines/summaries → documents → KG + expert layer."},
    {"id": "polygon", "name": "Polygon", "category": "market", "permission": "grey",
     "keyEnv": "POLYGON_API_KEY", "table": "prices", "where": "source='polygon'",
     "runnable": True, "desc": "Deep daily OHLCV + vX reference financials."},
    {"id": "wind", "name": "Wind 万得", "category": "market", "permission": "grey",
     "keyEnv": "XAR_ENABLE_WIND", "table": "fundamentals", "where": "source='wind'",
     "runnable": True, "desc": "CN-A deep fundamentals (requires local Wind terminal)."},
    {"id": "polymarket", "name": "Polymarket", "category": "prediction", "permission": "grey",
     "keyEnv": None, "table": "prediction_markets", "where": None,
     "runnable": True, "desc": "Forward prediction-market odds (public Gamma API)."},
    {"id": "twitter", "name": "X / Twitter", "category": "social", "permission": "grey",
     "keyEnv": "TWITTERAPI_TOKEN", "table": "social_posts", "where": "platform='x'",
     "runnable": True, "desc": "Expert handles + domain search via TwitterAPI.io → expert processing."},
    {"id": "reddit", "name": "Reddit", "category": "social", "permission": "grey",
     "keyEnv": "REDDIT_CLIENT_ID", "table": "social_posts", "where": "platform='reddit'",
     "runnable": True, "desc": "Finance/tech subreddit signal (public fallback)."},
    {"id": "aifinmarket", "name": "AIFINmarket 万得", "category": "market", "permission": "grey",
     "keyEnv": "AIFINMARKET_TOKEN", "table": "fundamentals", "where": "source='aifinmarket'",
     "runnable": True, "desc": "CN A-share professional source: Wind/AIFINmarket fundamentals + announcements/资讯."},
    {"id": "arxiv", "name": "arXiv (Frontier)", "category": "frontier", "permission": "green",
     "keyEnv": None, "table": "documents", "where": "source='arxiv'",
     "runnable": True, "desc": "Frontier-research preprints → the Exploration module (AI, physics, math, …)."},
    {"id": "journals", "name": "Journals / Quanta (Frontier)", "category": "frontier", "permission": "green",
     "keyEnv": None, "table": "documents", "where": "source='journal'",
     "runnable": True, "desc": "Curated top-journal / professional articles (Quanta, Physics World) → Exploration."},
]


def _availability() -> dict[str, bool]:
    from .. import providers
    from ..ingestion import wechat

    st = providers.status()  # fmp/finnhub/polygon/yahoo/wind/polymarket/twitter/reddit
    st.update({
        "edgar": True,
        "cninfo": _can_import("akshare"),
        "news": True,
        "jobs": True,
        "wechat": wechat.available(),
        "finnhub_news": st.get("finnhub", False),  # same key gates company-news pulls
    })
    return st


def sources() -> dict:
    avail = _availability()
    out = []
    for s in SOURCES:
        last = None
        if s["table"] == "documents":
            rows = db.query(
                "SELECT max(ingested_at) m FROM documents WHERE " + (s["where"] or "TRUE")
            )
            last = rows[0]["m"].isoformat() if rows and rows[0]["m"] else None
        out.append({
            **{k: s[k] for k in ("id", "name", "category", "permission", "keyEnv", "runnable", "desc")},
            "available": bool(avail.get(s["id"], False)),
            "rows": _count(s["table"], s["where"]),
            "table": s["table"],
            "lastRun": last,
        })
    cats = sorted({s["category"] for s in SOURCES})
    return {"sources": out, "categories": cats,
            "summary": {"total": len(out), "available": sum(1 for s in out if s["available"]),
                        "rows": sum(s["rows"] for s in out)}}


def run_source(source_id: str) -> dict:
    """Trigger ingestion/pull for one source (caller schedules as a bg task)."""
    from .. import ingestion, providers
    from ..ingestion import wechat
    from ..ingestion.registry import COMPANIES
    from ..kg import extract as kg_extract
    from ..parsing import parse
    from ..providers import polymarket, reddit

    ids = [c["id"] for c in COMPANIES]
    if source_id == "edgar":
        for cid in ids:
            try:
                ingestion.edgar.ingest_company(cid)
            except Exception as e:
                log.warning("edgar %s: %s", cid, e)
        parse.parse_pending()
        kg_extract.build_kg()
    elif source_id == "cninfo":
        for cid in ids:
            try:
                ingestion.cninfo.ingest_company(cid)
            except Exception as e:
                log.warning("cninfo %s: %s", cid, e)
        parse.parse_pending()
        kg_extract.build_kg()
    elif source_id == "wechat":
        if wechat.available():
            ingestion.ingest_wechat()
            parse.parse_pending()
            kg_extract.build_kg()
            from ..kg import expert
            expert.process(("wechat",))  # AI/expert-agent refinement -> high-SNR insights
    elif source_id == "aifinmarket":
        from ..kg import expert
        from ..providers import aifinmarket
        for cid in ids:
            try:
                aifinmarket.pull(cid)
            except Exception as e:
                log.warning("aifinmarket %s: %s", cid, e)
        parse.parse_pending()
        kg_extract.build_kg()
        expert.process(("aifinmarket",))
    elif source_id == "finnhub_news":
        from ..kg import expert
        for cid in ids:
            try:
                providers.finnhub.pull_news(cid)
            except Exception as e:
                log.warning("finnhub_news %s: %s", cid, e)
        parse.parse_pending()
        kg_extract.build_kg()
        expert.process(("finnhub",))
    elif source_id in ("arxiv", "journals"):
        from ..exploration import ingest, synthesis
        ingest.ingest_all(voices=False)  # arXiv preprints + curated journals per domain
        synthesis.synthesize_all()        # LLM-synthesize forward-looking research fronts
    elif source_id == "polymarket":
        polymarket.pull()
        from ..kg import signals
        signals.derive_market_signals()
    elif source_id == "reddit":
        reddit.pull_basket(ids)
    elif source_id in ("yahoo", "finnhub", "fmp", "polygon", "wind", "twitter"):
        prov = getattr(providers, source_id)
        from ..kg import signals
        for cid in ids:
            try:
                if source_id == "twitter":
                    prov.pull_company(cid)
                else:
                    prov.pull(cid)
                signals.derive_for_company(cid)
            except Exception as e:
                log.warning("%s %s: %s", source_id, cid, e)
        if source_id == "twitter":  # expert sweep + AI processing of X
            prov.pull()
            parse.parse_pending()
            from ..kg import expert
            expert.process(("x",))
    else:
        return {"status": "not_runnable", "source": source_id}
    return {"status": "done", "source": source_id}


# ===========================================================================
# 3) LLM VENDORS / MODELS
# ===========================================================================
def llm() -> dict:
    import os

    from ..models import registry, router
    from ..models.llm import _PRICES, _ensure_keys

    _ensure_keys()  # mirror Settings keys → env so presence checks are uniform
    s = get_settings()

    def _present(env: str) -> bool:
        return bool(os.environ.get(env))

    vendors = registry.configured_providers(_present)
    routing_tasks = {tc.value: {"capability": router.POLICIES[tc].capability.value,
                                "preferBilling": router.POLICIES[tc].prefer_billing,
                                "chain": [m.id for m in router.resolve(tc)]}
                     for tc in router.TaskClass}
    models = [{"id": m.id, "provider": m.provider, "litellm": m.litellm_model,
               "billing": m.billing.value, "capabilities": [c.value for c in m.capabilities],
               "inUsd": m.price_in, "outUsd": m.price_out, "status": m.status.value,
               "preferred": m.preferred, "released": m.released} for m in registry.MODELS]

    def _agg(sql):  # tolerate the new columns/table not existing yet (pre-init)
        try:
            return db.query(sql)
        except Exception:  # noqa: BLE001
            return []

    usage_rows = _agg("SELECT model, count(*) calls, COALESCE(sum(input_tokens),0) in_tok, "
                      "COALESCE(sum(output_tokens),0) out_tok, COALESCE(sum(usd),0) usd "
                      "FROM llm_usage GROUP BY model ORDER BY usd DESC")
    # Rows written before this migration have provider/task_class/billing = NULL; label them
    # 'legacy' so historical spend stays visible and attributed rather than forming a phantom
    # null bucket that silently aggregates the majority of past spend.
    by_provider = _agg("SELECT COALESCE(provider,'legacy') provider, count(*) calls, "
                       "COALESCE(sum(usd),0) usd, COALESCE(sum(input_tokens+output_tokens),0) tok "
                       "FROM llm_usage GROUP BY 1 ORDER BY usd DESC")
    by_billing = _agg("SELECT COALESCE(billing,'legacy') billing, count(*) calls, "
                      "COALESCE(sum(usd),0) usd, COALESCE(sum(input_tokens+output_tokens),0) tok "
                      "FROM llm_usage GROUP BY 1")
    by_task = _agg("SELECT COALESCE(task_class,'legacy') task_class, count(*) calls, COALESCE(sum(usd),0) usd FROM llm_usage "
                   "GROUP BY 1 ORDER BY usd DESC")
    overrides = _agg("SELECT key, model_id, updated_at FROM route_overrides ORDER BY key")
    total = (_agg("SELECT count(*) calls, COALESCE(sum(input_tokens),0) in_tok, "
                  "COALESCE(sum(output_tokens),0) out_tok, COALESCE(sum(usd),0) usd FROM llm_usage")
             or [{"calls": 0, "in_tok": 0, "out_tok": 0, "usd": 0}])[0]
    from ..models import agentsdk, codex_cli
    return {
        "vendors": vendors,
        "models": models,
        # Claude Max subscription path (agent_sdk executor). available=True only on a host
        # with the `claude` CLI + Max login; False (e.g. docker) → those specs skip → GLM.
        "anthropicMax": {"enabled": s.anthropic_max_enabled, "available": agentsdk.available(),
                         "model": s.anthropic_max_model, "effort": s.anthropic_max_effort},
        # ChatGPT/Codex subscription path (codex_cli executor). available=True only on a host
        # with the `codex` CLI + login; OFF by default (ToS-sensitive) → arm XAR_CODEX_ENABLED=true.
        "codexSub": {"enabled": s.codex_enabled, "available": codex_cli.available(),
                     "model": s.codex_model, "effort": s.codex_effort},
        "routing": {"tasks": routing_tasks, "fast": s.model_fast, "strong": s.model_strong,
                    "bulk": s.model_bulk or "(registry subscription preferred)", "effort": s.model_effort,
                    "budgetUsdPerRun": s.llm_max_usd_per_run, "budgetUsdPerBatch": s.llm_max_usd_per_batch,
                    "embedModel": s.embed_model, "embedDim": s.embed_dim},
        "overrides": [{"key": r["key"], "modelId": r["model_id"], "updatedAt": str(r["updated_at"])}
                      for r in overrides],
        "prices": [{"model": k, "inUsd": v[0], "outUsd": v[1]} for k, v in _PRICES.items()],
        "usage": {"total": {"calls": total["calls"], "inTok": total["in_tok"],
                            "outTok": total["out_tok"], "usd": round(float(total["usd"]), 4)},
                  "byModel": [{"model": r["model"], "calls": r["calls"], "inTok": r["in_tok"],
                               "outTok": r["out_tok"], "usd": round(float(r["usd"]), 4)}
                              for r in usage_rows],
                  "byProvider": [{"provider": r["provider"], "calls": r["calls"], "tok": r["tok"],
                                  "usd": round(float(r["usd"]), 4)} for r in by_provider],
                  "byBilling": [{"billing": r["billing"], "calls": r["calls"], "tok": r["tok"],
                                 "usd": round(float(r["usd"]), 4)} for r in by_billing],
                  "byTask": [{"taskClass": r["task_class"], "calls": r["calls"],
                              "usd": round(float(r["usd"]), 4)} for r in by_task]},
        "configured": s.has_llm,
    }


def set_route(key: str, model_id: str) -> dict:
    """Runtime route override: point a capability or task_class at a registry model id
    (or clear it with an empty model_id). Persisted to route_overrides + cache refreshed
    → live re-route without a redeploy."""
    from ..models import registry, router

    valid = {c.value for c in registry.Capability} | {t.value for t in router.TaskClass}
    if key not in valid:
        return {"ok": False, "detail": f"key must be a capability or task_class: {sorted(valid)}"}
    if not model_id:
        db.execute("DELETE FROM route_overrides WHERE key=%s", (key,))
        registry.refresh_overrides()
        return {"ok": True, "key": key, "cleared": True}
    if not registry.get(model_id):
        return {"ok": False, "detail": f"unknown model_id: {model_id} (see /api/ops/llm models[])"}
    db.execute("INSERT INTO route_overrides(key,model_id,updated_at) VALUES(%s,%s,now()) "
               "ON CONFLICT(key) DO UPDATE SET model_id=EXCLUDED.model_id, updated_at=now()",
               (key, model_id))
    registry.refresh_overrides()
    return {"ok": True, "key": key, "modelId": model_id}


def test_llm() -> dict:
    """Cheap round-trip to verify the configured LLM actually responds."""
    from ..models import llm as _llm

    s = get_settings()
    if not s.has_llm:
        return {"ok": False, "detail": "no LLM key configured"}
    try:
        # reasoning models (DeepSeek V4) need headroom beyond the answer for thinking
        reply = _llm.complete("Reply with exactly: XAR-OK", tier="fast", node="ops_test", max_tokens=256)
        return {"ok": len(reply.strip()) > 0, "model": s.model_fast, "reply": reply.strip()[:120]}
    except Exception as e:
        return {"ok": False, "model": s.model_fast, "detail": str(e)[:200]}


# ===========================================================================
# 4) MCP & API CONNECTORS
# ===========================================================================
def connectors() -> dict:
    s = get_settings()
    outbound = [
        {"id": "edgar", "name": "SEC EDGAR API", "baseUrl": "https://data.sec.gov",
         "auth": "User-Agent identity", "configured": True, "mcp": False, "category": "filing"},
        {"id": "werss", "name": "we-mp-rss", "baseUrl": s.werss_base_url or "(unset)",
         "auth": "none / bearer", "configured": bool(s.werss_base_url), "mcp": False, "category": "web"},
        {"id": "finnhub", "name": "Finnhub", "baseUrl": "https://finnhub.io/api/v1",
         "auth": "api key", "configured": bool(s.finnhub_api_key), "mcp": False, "category": "market"},
        {"id": "fmp", "name": "Financial Modeling Prep", "baseUrl": "https://financialmodelingprep.com/api",
         "auth": "api key", "configured": bool(s.fmp_api_key), "mcp": True, "category": "market"},
        {"id": "polygon", "name": "Polygon.io", "baseUrl": "https://api.polygon.io",
         "auth": "api key", "configured": bool(s.polygon_api_key), "mcp": False, "category": "market"},
        {"id": "yahoo", "name": "Yahoo (yfinance)", "baseUrl": "https://query2.finance.yahoo.com",
         "auth": "none", "configured": _can_import("yfinance"), "mcp": False, "category": "market"},
        {"id": "polymarket", "name": "Polymarket Gamma", "baseUrl": "https://gamma-api.polymarket.com",
         "auth": "none", "configured": True, "mcp": False, "category": "prediction"},
        {"id": "x", "name": "X API v2", "baseUrl": "https://api.twitter.com/2",
         "auth": "bearer", "configured": bool(s.x_bearer_token), "mcp": False, "category": "social"},
        {"id": "reddit", "name": "Reddit", "baseUrl": "https://oauth.reddit.com",
         "auth": "oauth / public", "configured": True, "mcp": False, "category": "social"},
    ]
    inbound = [
        {"group": "Dashboard", "desc": "Industry-chain terminal data",
         "endpoints": ["/api/ui/overview", "/api/ui/companies", "/api/ui/signals",
                       "/api/ui/catalysts", "/api/ui/company/{id}", "/api/ui/segment/{id}"]},
        {"group": "Knowledge graph", "desc": "Bitemporal supply-chain KG",
         "endpoints": ["/api/graph/{id}", "/api/signals/{id}"]},
        {"group": "Structured", "desc": "Fundamentals / estimates / prices",
         "endpoints": ["/api/fundamentals/{id}", "/api/estimates/{id}", "/api/prices/{id}",
                       "/api/prediction-markets", "/api/social/{id}"]},
        {"group": "Reports", "desc": "Multi-agent research products",
         "endpoints": ["/api/report", "/api/report/{id}", "/api/report/{id}/approve", "/api/runs"]},
        {"group": "Operations", "desc": "This control plane",
         "endpoints": ["/api/ops/ontology", "/api/ops/sources", "/api/ops/llm",
                       "/api/ops/connectors", "/api/ops/skills", "/api/ops/datalake",
                       "/api/ops/selftest"]},
    ]
    return {
        "outbound": outbound,
        "inbound": inbound,
        "mcpNote": ("Outbound APIs are first-class connectors; FMP additionally exposes an MCP "
                    "server. The inbound XAR API is MCP-ready (read tools) and is consumed by the "
                    "React terminal + this console."),
        "summary": {"outbound": len(outbound), "configured": sum(1 for c in outbound if c["configured"]),
                    "inboundGroups": len(inbound)},
    }


# ===========================================================================
# 5) AGENT SKILLS (the report DAG)
# ===========================================================================
def skills() -> dict:
    from ..agents.nodes import ANALYSTS

    pipeline: list[dict] = [
        {"id": "scope", "name": "Scope / Planner", "stage": 1, "tier": "-",
         "desc": "Resolve the request to a canonical company entity via deterministic resolution; lock the data snapshot."},
        {"id": "graph_retrieve", "name": "Graph Retrieve", "stage": 2, "tier": "-",
         "desc": "Bitemporal KG traversal: suppliers, customers, equity stakes, single-source risks, dated catalyst events."},
    ]
    for name, query, instr, tier, numeric in ANALYSTS:
        pipeline.append({
            "id": f"analyst:{name}", "name": f"Analyst · {name.replace('_', ' ').title()}",
            "stage": 3, "tier": tier, "numeric": numeric,
            "desc": instr, "query": query})
    pipeline += [
        {"id": "debate", "name": "Bull / Bear Debate", "stage": 4, "tier": "strong",
         "desc": "Bounded adversarial subgraph (capped rounds) grounded only in the analysts' cited findings."},
        {"id": "risk", "name": "Risk Stress-Test", "stage": 5, "tier": "strong",
         "desc": "Stress the thesis: EML shortage, CPO/LPO disruption to DSP attach, customer concentration, single-source exposure."},
        {"id": "editor", "name": "Editor Synthesis", "stage": 6, "tier": "strong",
         "desc": "Data-CoT → Concept-CoT → Thesis-CoT synthesis into deep report / tracking summary / takeaways."},
        {"id": "evidence_gate", "name": "Evidence Coverage Gate", "stage": 7, "tier": "strong",
         "desc": "Citation coverage + numeric tie-out + hallucination LLM-judge; low-confidence reports flagged for human review."},
        {"id": "approval", "name": "Human Approval", "stage": 8, "tier": "-",
         "desc": "interrupt() gate: report stays awaiting_approval until a human approves; non-advice disclaimer enforced."},
    ]
    capabilities = [
        {"id": "hybrid_retrieval", "name": "Hybrid Retrieval (RRF)",
         "desc": "pgvector dense + pg_trgm lexical fused via Reciprocal Rank Fusion (k=60)."},
        {"id": "entity_resolution", "name": "Entity Resolution",
         "desc": "Deterministic alias table + trigram fuzzy match before every KG write."},
        {"id": "numeric_tieout", "name": "Numeric Tie-Out Gate",
         "desc": "Table totals reconciled to column sums; conclusions never grounded on un-tied numbers."},
        {"id": "kg_extract", "name": "Schema-Constrained Extraction",
         "desc": "LLM extraction constrained to the ontology; entity-resolved + event-deduped on write."},
        {"id": "signals_bridge", "name": "Structured → Ontology Signals",
         "desc": "Estimate revisions / insider clusters / prediction-market shifts distilled into catalyst events."},
        {"id": "embeddings", "name": "Embeddings (fastembed)",
         "desc": "CPU ONNX embeddings; default BAAI/bge-small-en-v1.5 (384d), swappable to BGE-M3."},
    ]
    return {"pipeline": pipeline, "capabilities": capabilities,
            "summary": {"stages": 8, "skills": len(pipeline), "capabilities": len(capabilities)}}


# ===========================================================================
# 6) UNSTRUCTURED DATA LAKE
# ===========================================================================
def datalake() -> dict:
    totals = {
        "documents": _count("documents"),
        "chunks": _count("chunks"),
        "parsed": db.query(
            "SELECT count(DISTINCT doc_id) c FROM chunks")[0]["c"],
        "extracted": db.query(
            "SELECT count(DISTINCT source_doc_id) c FROM ("
            "SELECT source_doc_id FROM kg_edges WHERE source_doc_id IS NOT NULL "
            "UNION SELECT source_doc_id FROM kg_events WHERE source_doc_id IS NOT NULL) t")[0]["c"],
    }
    by_source = db.query(
        "SELECT d.source, count(*) docs, "
        "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM chunks c WHERE c.doc_id=d.id)) parsed, "
        "COALESCE(sum((SELECT count(*) FROM chunks c WHERE c.doc_id=d.id)),0) chunks "
        "FROM documents d GROUP BY d.source ORDER BY docs DESC"
    )
    by_perm = db.query("SELECT permission, count(*) c FROM documents GROUP BY permission ORDER BY c DESC")
    return {
        "totals": totals,
        "bySource": [{"source": r["source"], "docs": int(r["docs"]),
                      "parsed": int(r["parsed"]), "chunks": int(r["chunks"])} for r in by_source],
        "byPermission": [{"permission": r["permission"], "c": int(r["c"])} for r in by_perm],
        "pending": totals["documents"] - totals["parsed"],
    }


def datalake_documents(limit: int = 40, offset: int = 0, source: str | None = None,
                       q: str | None = None) -> dict:
    where = ["TRUE"]
    params: list = []
    if source:
        where.append("d.source=%s")
        params.append(source)
    if q:
        where.append("(d.title ILIKE %s OR d.text ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    wsql = " AND ".join(where)
    total = db.query(f"SELECT count(*) c FROM documents d WHERE {wsql}", params)[0]["c"]
    rows = db.query(
        f"SELECT d.id, d.company_id, d.source, d.doc_type, d.title, d.url, d.permission, "
        f"d.license_tag, d.published_at, d.ingested_at, length(d.text) AS chars, "
        f"(SELECT count(*) FROM chunks c WHERE c.doc_id=d.id) AS chunks, "
        f"EXISTS(SELECT 1 FROM kg_events e WHERE e.source_doc_id=d.id) "
        f" OR EXISTS(SELECT 1 FROM kg_edges g WHERE g.source_doc_id=d.id) AS extracted "
        f"FROM documents d WHERE {wsql} "
        f"ORDER BY d.ingested_at DESC LIMIT %s OFFSET %s",
        params + [limit, offset],
    )
    docs = []
    for r in rows:
        d = dict(r)
        for k in ("published_at", "ingested_at"):
            d[k] = d[k].isoformat() if d.get(k) and hasattr(d[k], "isoformat") else None
        docs.append(d)
    return {"total": total, "limit": limit, "offset": offset, "documents": docs}


def process_datalake() -> dict:
    """Parse+embed pending docs, then extract the KG (caller runs in background)."""
    from ..kg import extract as kg_extract
    from ..parsing import parse

    chunks = parse.parse_pending()
    kg = kg_extract.build_kg()
    return {"status": "done", "chunks_embedded": chunks, "kg": kg}


# ===========================================================================
# 6b) ALT-DATA EXPERT PROCESSING (the AI signal-to-noise layer)
# ===========================================================================
def altdata() -> dict:
    from ..kg import expert

    return {"stats": expert.stats(), "insights": expert.top_insights(30)}


def process_altdata(sources: tuple[str, ...] | None = None) -> dict:
    from ..kg import expert

    return expert.process(sources or expert.ALT_SOURCES)


def gangtise() -> dict:
    """Gangtise 投研覆盖总览:连通性 + 结构化(财报/估值/一致预期)与投研文本落库计数。"""
    from ..config import get_settings
    from ..providers import gangtise as gts
    from ..storage import db

    funds = db.query("SELECT count(*) n, count(DISTINCT company_id) c FROM fundamentals "
                     "WHERE source='gangtise'")[0]
    ests = db.query("SELECT count(*) n, count(DISTINCT company_id) c FROM estimates "
                    "WHERE source='gangtise'")[0]
    docs = db.query("SELECT doc_type, count(*) n FROM documents WHERE source='gangtise' "
                    "GROUP BY doc_type ORDER BY n DESC")
    return {
        "enabled": get_settings().enable_gangtise,
        "reachable": gts.available(),
        "fundamentals": funds["n"], "fundamentals_companies": funds["c"],
        "estimates": ests["n"], "estimates_companies": ests["c"],
        "research_docs": {r["doc_type"]: r["n"] for r in docs},
    }


# ===========================================================================
# 7) SELF-TEST — run through every ontology type + data source
# ===========================================================================
def selftest() -> dict:
    checks: list[dict] = []

    # platform
    try:
        db.query("SELECT 1")
        checks.append({"id": "database", "group": "platform", "status": "ok", "detail": "Postgres reachable"})
    except Exception as e:
        checks.append({"id": "database", "group": "platform", "status": "fail", "detail": str(e)[:120]})
    s = get_settings()
    checks.append({"id": "llm", "group": "platform",
                   "status": "ok" if s.has_llm else "unconfigured",
                   "detail": f"strong={s.model_strong} fast={s.model_fast}" if s.has_llm else "no LLM key"})
    checks.append({"id": "embeddings", "group": "platform",
                   "status": "ok" if _can_import("fastembed") else "fail",
                   "detail": f"{s.embed_model} ({s.embed_dim}d)"})

    # ontology presence
    ont = ontology()
    for grp, key, label in (("nodeTypes", "type", "node"), ("edgeTypes", "type", "edge"),
                            ("catalystTypes", "type", "catalyst")):
        present = sum(1 for x in ont[grp] if x["count"] > 0)
        total = len(ont[grp])
        checks.append({"id": f"ontology:{label}", "group": "ontology",
                       "status": "ok" if present else "empty",
                       "detail": f"{present}/{total} {label} types populated"})

    # data sources
    avail = _availability()
    src = {x["id"]: x for x in sources()["sources"]}
    for s_ in SOURCES:
        sid = s_["id"]
        rows = src[sid]["rows"]
        if not avail.get(sid):
            status = "unconfigured"
            detail = f"not configured ({s_['keyEnv'] or 'n/a'})"
        elif rows > 0:
            status = "ok"
            detail = f"{rows} rows"
        else:
            status = "degraded"
            detail = "available, no data yet"
        checks.append({"id": f"source:{sid}", "group": "sources", "status": status, "detail": detail})

    counts = {"ok": 0, "degraded": 0, "unconfigured": 0, "fail": 0, "empty": 0}
    for c in checks:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return {"checks": checks, "summary": counts, "ranAt": _now()}


# ── Fetchy:glmworker 管理面(Jarvy 前端)──────────────────────────────────────
def _wechat_discover_summary() -> dict:
    """微信「全网发现」漏斗观测:开关 + 已发现文档数 + 晋升漏斗统计(供 Jarvy)。"""
    from ..config import get_settings
    from ..ingestion import wcda_api, wechat_search, werss_api
    from ..mining.wechat_promote import promotion_stats

    s = get_settings()
    out = {"enabled": bool(s.wechat_discover_enabled),
           "wcdaConfigured": wcda_api.available(),                    # 文章级(wechat-download-api,主用)
           "articleSearchConfigured": wechat_search.available(),      # 文章级(通用 /api/search)
           "accountSearchConfigured": werss_api.available()}          # 账号级(we-mp-rss AK/SK)
    try:
        out["discoveredDocs"] = _count("documents",
                                       "source='wechat' AND meta->>'via'='discover'")
        out["wcdaDocs"] = _count("documents",
                                 "source='wechat' AND meta->>'backend'='wcda'")
        out["funnel"] = promotion_stats()      # 发现候选/已订阅/够格待晋升
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:160]
    return out


def fetchy() -> dict:
    """工人状态 + 生效配置 + 可选模型/数据源/阶段目录(供 Jarvy Fetchy 页渲染)。"""
    from ..models.registry import MODELS
    from ..orchestration import glm_worker as gw

    st = gw.status()
    cadence = st.get("cadence") or {}
    sources = [{"key": k, "label": v["label"], "hours": v["hours"],
                "last": cadence.get(k)}
               for k, v in gw.FETCHY_SOURCES.items()]
    stages = [{"key": k, "label": v} for k, v in gw.FETCHY_STAGES.items()]
    # 目录只列工人容器内**实际可服务**的模型(ACTIVE + litellm 执行器 + provider key 在位)
    # —— host-only 执行器(agent_sdk/codex_cli)或缺 key 的模型选了也是静默空转,不给选。
    models = [{"id": m.id, "provider": m.provider, "billing": m.billing.value,
               "preferred": m.preferred, "notes": m.notes[:80]}
              for m in MODELS if gw.model_usable(m.id) is None]
    # 订阅制在前(工人常驻批量,订阅=零边际成本),同组内 preferred 在前
    models.sort(key=lambda m: (m["billing"] != "subscription", not m["preferred"], m["id"]))
    from ..providers import twitter

    return {"config": gw.fetchy_config(), "defaults": gw.fetchy_defaults(),
            "sources": sources, "stages": stages, "models": models,
            "xBudget": twitter.spend_summary(),   # X 源月度限额账本(估算;usd=None=账本不可读)
            "wechatDiscover": _wechat_discover_summary(),  # 微信全网发现漏斗(开关+发现数+晋升)
            "status": {"quota": st.get("quota"), "counters": st.get("counters"),
                       "backlog_docs": st.get("extraction_backlog_docs"),
                       "pin": st.get("pin")}}


def set_fetchy(cfg: dict) -> dict:
    """保存 Fetchy 配置(app 写共享 DB,工人下一轮生效 —— 无需重启容器)。"""
    from ..orchestration import glm_worker as gw

    return {"config": gw.save_fetchy(cfg or {})}
