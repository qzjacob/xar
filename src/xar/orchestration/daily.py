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


def _run_source(src: str, ids: list[str], since, *, shard: int | None = None) -> dict:
    """Pull one source incrementally for the given company shard. Returns a small
    stats dict. Per-company failures are logged, not raised (a bad ticker must not
    sink the whole source); a source-wide failure propagates to the caller, which
    records it as a failed `ingest_runs` row. `shard` gates global (non-company)
    sub-tasks to run once — see the wechat discovery branch."""
    from .. import providers
    from ..ingestion import cninfo, edgar, ingest_wechat, wechat, wechat_discover
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
            pulled += len(ingest_wechat())            # 订阅号轮询(现有)
        # 全网发现是全局查询(不分 company),且外部搜索靠按天轮转限流 → 每日只跑一次(shard 0),
        # 否则 N 个分片 N× 重复搜索,打爆反爬。脆弱环节 try/except 隔离:发现/晋升/止损失败
        # 不拖垮已成功的订阅轮询、不误标整轮 wechat 失败。文章级/账号级各自 available() 自门控。
        if shard in (None, 0):
            try:
                # WCDA = 唯一全网搜索引擎;werss discover_accounts 已弃用(werss 仅订阅+轮询)。
                if wechat_discover.wcda_available():        # 文章级(wechat-download-api,主用)
                    pulled += len(wechat_discover.discover_via_wcda())
                if wechat_discover.available():            # 文章级(通用 /api/search 备用后端)
                    pulled += len(wechat_discover.discover())
                from ..mining.wechat_promote import promote_candidates, prune_accounts
                promote_candidates()                      # WCDA 高产号混合晋升(自动/HITL)
                prune_accounts()                          # 发现订阅低信噪号止损(停用+退订)
            except Exception as e:  # noqa: BLE001
                log.warning("wechat discover/promote/prune failed (isolated, 订阅轮询不受影响): %s", e)
    elif src == "aifinmarket":
        for cid in ids:
            try:
                providers.aifinmarket.pull(cid)
            except Exception as e:  # noqa: BLE001
                log.warning("aifinmarket %s: %s", cid, e)
    elif src == "futu":
        # Futu OpenD (富途): snapshot valuation + 资讯 news + 板块 plates for HK/CN/US
        # names. OFF unless enable_futu + OpenD reachable (available() gates it).
        if providers.futu.available():
            for cid in ids:
                try:
                    r = providers.futu.pull(cid)
                    pulled += int(r.get("news", 0))
                except Exception as e:  # noqa: BLE001
                    log.warning("futu %s: %s", cid, e)
    elif src == "polymarket":
        providers.polymarket.pull()
        signals.derive_market_signals()
    elif src == "rss":
        # curated industry-news feeds (theme-level) — ignores the company shard
        from ..providers import rss

        pulled += rss.pull(since=since)
    elif src == "macro":
        # Andy (slx) macro module — opt-in (add 'macro' to XAR_DAILY_ENABLED_SOURCES).
        # Ignores the company shard: connectors → identification → overclaim verdicts.
        from datetime import date

        from slx.engine import overclaim
        from slx.ingestion.discovery import discover_connectors
        from slx.ingestion.identification_panels import run_identification

        from ..cli import _bridge_slx_env

        _bridge_slx_env()
        for source_id, (cls, is_primary) in sorted(discover_connectors().items()):
            if not is_primary or source_id == "seed":
                continue
            try:
                cls().run()          # returns an audit_log run uuid; rows land in slx.observation
                pulled += 1          # count sources swept (per-source row counts: audit_log)
            except Exception as e:  # noqa: BLE001
                log.warning("macro connector %s: %s", source_id, e)
        run_identification(date.today())
        overclaim.run(date.today())
        from ..ingestion import macro_bridge

        macro_bridge.sync(date.today())   # 勾稽：印字+判定跃迁 → semantic_facts
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
              shard: int | None = None, n_shards: int = 1, run_id: str | None = None,
              stages: tuple[str, ...] = ("pull", "extract")) -> dict:
    """Run one incremental daily ingest+update pass. See module docstring.

    `stages` selects the phases:
      - 'pull'    — per-source incremental fetch (sources → documents). This is the part
                    that shards safely by company (each shard a disjoint company slice).
      - 'extract' — parse → build_kg → expert → signals. These read the GLOBAL pending
                    queue, so they must run EXACTLY ONCE per night (one batch budget), not
                    once per shard — running them per shard would N× the LLM spend and race
                    on the same docs. The Dagster sidecar runs 'pull' across N shard
                    partitions and 'extract' once. The CLI default runs both (unsharded)."""
    from ..config import get_settings
    from ..kg import expert, resolve_claims, signals, store
    from ..kg import extract as kg_extract
    from ..parsing import parse

    s = get_settings()
    enabled = sources if sources is not None else [
        x.strip() for x in s.daily_enabled_sources.split(",") if x.strip()]
    run_id = run_id or llm.new_batch_run_id("batch")  # batch budget cap applies
    do_pull, do_extract = "pull" in stages, "extract" in stages
    stats: dict = {"run_id": run_id, "stages": list(stages), "sources": {}}
    parent = runlog.start("daily" if do_pull and do_extract else "daily:" + ",".join(stages))
    try:
        seed_companies()         # idempotent registry → companies (FK base for doc saves)
        store.bootstrap_seed()   # idempotent node/edge/alias backbone
        all_ids = [c["id"] for c in COMPANIES]
        ids = all_ids
        if do_pull and full_universe and n_shards > 1 and shard is not None:
            ids = all_ids[shard::n_shards]   # bounded per-shard slice — PULL only
        stats["companies"] = len(ids)

        if do_pull:
            # incremental PULL per source — isolated so one failure can't sink the round
            for src in enabled:
                cur = _cursor(src, since)
                r = runlog.start(src, since_ts=cur)
                try:
                    sub = _run_source(src, ids, cur, shard=shard)
                    stats["sources"][src] = sub
                    runlog.finish(r, "ok", stats=sub)
                except Exception as e:  # noqa: BLE001
                    stats["sources"][src] = {"error": str(e)}
                    runlog.finish(r, "failed", error=str(e))
                    log.warning("source %s failed: %s", src, e)

        if do_extract:
            # GLOBAL stages (NOT sharded) — run once over the whole pending queue.
            stats["chunks"] = parse.parse_pending()
            # The LLM stages may trip the batch budget; catch it HERE so the cheap DB-only
            # stages below (signals, resolve) still run — they cost no tokens and a capped
            # extract night must not also skip signal derivation and claim resolution.
            try:
                stats["kg"] = kg_extract.build_kg(limit=s.daily_kg_doc_limit, run_id=run_id)
                stats["expert"] = expert.process(run_id=run_id)
            except llm.BudgetExceeded as e:
                stats["budget_capped"] = str(e)
                log.warning("extract LLM stages budget-capped: %s", e)
            for cid in all_ids:   # structured → ontology signals, whole universe
                try:
                    signals.derive_for_company(cid)
                except Exception as e:  # noqa: BLE001
                    log.warning("signals %s: %s", cid, e)
            # close the forward-claim loop: did past forward_looking expectations realize?
            stats["resolved"] = resolve_claims.resolve_forward_claims()

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
