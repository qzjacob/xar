"""Daily auto-ingest orchestrator — pure Python, no Dagster dependency, unit-testable.

`run_daily()` composes the EXISTING pipeline functions into one incremental nightly
pass over the whole universe:

    seed/bootstrap → per-source PULL → parse+embed → semantic extract → expert → signals

Everything it calls is idempotent and resumable: `save()` upserts by content hash,
`parse_pending` only parses chunk-less docs, `build_kg`/`expert.process` only touch
documents that have no extraction yet (NOT EXISTS cursors), and `add_event`/`add_edge`
dedup. So a crashed run simply continues on the next invocation. Each source runs
inside its own `ingest_runs` row, so one source failing never aborts the round; the
parent 'daily' run records the aggregate stats. The shared `batch`-prefixed run_id
applies the per-batch LLM budget cap; a `BudgetExceeded` ends the run gracefully and
records `budget_capped` rather than failing.

The Dagster sidecar (definitions.py) and the `xar daily` CLI are thin wrappers over
this function.
"""
from __future__ import annotations

from ..ingestion import seed_companies
from ..ingestion.registry import COMPANIES
from ..logging import get_logger
from ..models import llm
from ..storage import runlog

log = get_logger("xar.daily")


def _run_source(src: str, ids: list[str], since) -> dict:
    """Pull one source incrementally for the given company shard. Returns a small
    stats dict. Per-company failures are logged, not raised (a bad ticker must not
    sink the whole source); a source-wide failure propagates to the caller, which
    records it as a failed `ingest_runs` row."""
    from .. import providers
    from ..ingestion import cninfo, edgar, ingest_wechat, wechat
    from ..kg import signals

    pulled = 0
    if src == "edgar":
        for cid in ids:
            try:
                pulled += len(edgar.ingest_company(cid))
            except Exception as e:  # noqa: BLE001
                log.warning("edgar %s: %s", cid, e)
    elif src == "cninfo":
        for cid in ids:
            try:
                pulled += len(cninfo.ingest_company(cid))
            except Exception as e:  # noqa: BLE001
                log.warning("cninfo %s: %s", cid, e)
    elif src == "finnhub":
        for cid in ids:
            try:
                pulled += providers.finnhub.pull_news(cid, since=since)
                providers.finnhub.pull(cid)
            except Exception as e:  # noqa: BLE001
                log.warning("finnhub %s: %s", cid, e)
    elif src == "fmp":
        for cid in ids:
            try:
                providers.fmp.pull(cid)
                pulled += providers.fmp.pull_news(cid)
            except Exception as e:  # noqa: BLE001
                log.warning("fmp %s: %s", cid, e)
    elif src == "twitter":
        for cid in ids:
            try:
                providers.twitter.pull_company(cid)
            except Exception as e:  # noqa: BLE001
                log.warning("twitter %s: %s", cid, e)
        providers.twitter.pull()  # expert-handle / domain sweep
    elif src == "reddit":
        providers.reddit.pull_basket(ids)
    elif src == "wechat":
        if wechat.available():
            pulled += len(ingest_wechat())
    elif src == "aifinmarket":
        for cid in ids:
            try:
                providers.aifinmarket.pull(cid)
            except Exception as e:  # noqa: BLE001
                log.warning("aifinmarket %s: %s", cid, e)
    elif src == "polymarket":
        providers.polymarket.pull()
        signals.derive_market_signals()
    else:
        return {"skipped": f"unknown source {src}"}
    return {"pulled": pulled}


def _cursor(src: str, since):
    """Resolve the pull-window lower bound for a source. `since`: None/'auto' → the
    last successful run (incremental); 'full' → no bound (full default window);
    otherwise the explicit ISO date / datetime passed through."""
    if since in (None, "auto"):
        return runlog.last_success_ts(src)
    if since == "full":
        return None
    return since


def run_daily(sources: list[str] | None = None, *, since=None, full_universe: bool = True,
              shard: int | None = None, n_shards: int = 1, run_id: str | None = None) -> dict:
    """Run one incremental daily ingest+update pass. See module docstring."""
    from ..config import get_settings
    from ..kg import expert, signals, store
    from ..kg import extract as kg_extract
    from ..parsing import parse

    s = get_settings()
    enabled = sources if sources is not None else [
        x.strip() for x in s.daily_enabled_sources.split(",") if x.strip()]
    run_id = run_id or llm.new_batch_run_id("batch")  # batch budget cap applies
    stats: dict = {"run_id": run_id, "sources": {}}
    parent = runlog.start("daily")
    try:
        seed_companies()         # idempotent registry → companies
        store.bootstrap_seed()   # idempotent node/edge/alias backbone
        ids = [c["id"] for c in COMPANIES]
        if full_universe and n_shards > 1 and shard is not None:
            ids = ids[shard::n_shards]   # bounded per-shard slice of the whole universe
        stats["companies"] = len(ids)

        # 1) incremental PULL per source — isolated so one failure can't sink the round
        for src in enabled:
            cur = _cursor(src, since)
            r = runlog.start(src, since_ts=cur)
            try:
                sub = _run_source(src, ids, cur)
                stats["sources"][src] = sub
                runlog.finish(r, "ok", stats=sub)
            except Exception as e:  # noqa: BLE001
                stats["sources"][src] = {"error": str(e)}
                runlog.finish(r, "failed", error=str(e))
                log.warning("source %s failed: %s", src, e)

        # 2) parse + embed any new documents (incremental: chunk-less docs only)
        stats["chunks"] = parse.parse_pending()
        # 3) semantic extraction (incremental; fills causal/stance/narrative fields)
        stats["kg"] = kg_extract.build_kg(limit=s.daily_kg_doc_limit, run_id=run_id)
        stats["expert"] = expert.process(run_id=run_id)
        # 4) structured → ontology signals
        for cid in ids:
            try:
                signals.derive_for_company(cid)
            except Exception as e:  # noqa: BLE001
                log.warning("signals %s: %s", cid, e)

        runlog.finish(parent, "ok", stats=stats)
    except llm.BudgetExceeded as e:
        stats["budget_capped"] = str(e)
        runlog.finish(parent, "ok", stats=stats)  # graceful stop, not a failure
        log.warning("daily run budget-capped: %s", e)
    except Exception as e:  # noqa: BLE001
        runlog.finish(parent, "failed", error=str(e))
        raise
    log.info("daily run done: %s", {k: stats.get(k) for k in ("companies", "chunks", "kg", "expert")})
    return stats
