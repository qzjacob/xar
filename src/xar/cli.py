"""XAR command-line interface.

Turnkey flow:
    cp .env.example .env   # set ANTHROPIC_API_KEY
    xar init               # schema + seed companies + KG backbone
    xar ingest             # filings -> parse -> KG (the whole basket)
    xar report nvidia      # generate a deep report
    xar serve              # web UI at http://localhost:8000
"""
from __future__ import annotations

import json

import typer
from rich import print
from rich.table import Table

from .logging import get_logger

app = typer.Typer(add_completion=False, help="XAR industry-chain investment research platform")
log = get_logger("xar.cli")


@app.command()
def init() -> None:
    """Initialize DB schema, seed the company basket, and bootstrap the KG seed."""
    from .ingestion import seed_companies
    from .kg import store
    from .storage import db

    db.init_schema()
    seed_companies()
    store.bootstrap_seed()
    try:  # Andy (slx) macro module: schema + theory/metric/overclaim registry, idempotent
        _andy_init_impl()
        print("[green]✓ andy (slx)[/green] schema + registry loaded")
    except Exception as e:  # noqa: BLE001 — a broken macro module must not block core init
        print(f"[yellow]andy (slx) init skipped:[/yellow] {e}")
    print("[green]✓ initialized[/green] (schema + companies + seed graph)")


@app.command()
def ingest(
    company: str = typer.Argument(None, help="company id; omit for the whole basket"),
    edgar_limit: int = 6,
    cn_limit: int = 15,
    parse_now: bool = typer.Option(True, help="parse+embed after ingest"),
    build_kg: bool = typer.Option(True, help="extract KG after parse"),
) -> None:
    """Ingest filings (and optionally parse + build the KG)."""
    from . import ingestion
    from .kg import extract as kg_extract
    from .parsing import parse
    from .ingestion.registry import COMPANIES

    ids = [company] if company else [c["id"] for c in COMPANIES]
    for cid in ids:
        n = len(ingestion.ingest_company(cid, edgar_limit=edgar_limit, cn_limit=cn_limit))
        print(f"  {cid}: {n} docs")
    if parse_now:
        print(f"[cyan]parsing…[/cyan] {parse.parse_pending()} chunks")
    if build_kg:
        print(f"[cyan]building KG…[/cyan] {kg_extract.build_kg()}")


@app.command("ingest-wechat")
def ingest_wechat(
    parse_now: bool = typer.Option(True, help="parse+embed after ingest"),
    build_kg: bool = typer.Option(True, help="extract KG after parse"),
) -> None:
    """Ingest WeChat Official Account (微信公众号) articles via a we-mp-rss service
    (set WERSS_BASE_URL), then parse + extract into the ontology."""
    from . import ingestion
    from .kg import extract as kg_extract
    from .parsing import parse as p

    if not ingestion.wechat.available():
        print("[yellow]WERSS_BASE_URL not set — WeChat connector skipped.[/yellow] "
              "Run we-mp-rss (docker, port 8001) and set WERSS_BASE_URL.")
        raise typer.Exit(0)
    ids = ingestion.ingest_wechat()
    print(f"[green]wechat:[/green] {len(ids)} articles ingested")
    if parse_now:
        print(f"[cyan]parsing…[/cyan] {p.parse_pending()} chunks")
    if build_kg:
        print(f"[cyan]building KG…[/cyan] {kg_extract.build_kg()}")


@app.command()
def parse() -> None:
    """Parse + embed any unparsed documents."""
    from .parsing import parse as p

    print(f"parsed {p.parse_pending()} chunks")


@app.command("build-kg")
def build_kg(limit: int = typer.Option(None)) -> None:
    """Extract the knowledge graph from ingested documents."""
    from .kg import extract

    print(json.dumps(extract.build_kg(limit=limit), indent=2))


@app.command()
def report(
    company_id: str,
    kind: str = typer.Option("deep_report", help="deep_report|tracking_summary|takeaways"),
    since: str = typer.Option(None),
    auto_approve: bool = typer.Option(True, help="publish without human approval (CLI)"),
    out: str = typer.Option(None, help="write markdown to this path"),
) -> None:
    """Generate a report through the multi-agent pipeline."""
    from .agents import run_report

    r = run_report({"kind": kind, "company_id": company_id, "since": since},
                   auto_approve=auto_approve)
    if r.get("error"):
        print(f"[red]{r['error']}[/red]")
        raise typer.Exit(1)
    m = r["metrics"]
    print(f"[green]run {r['run_id']}[/green] status={r['status']} "
          f"coverage={m['evidence_coverage']} risk={m['hallucination_risk']} cites={m['citation_count']}")
    if out:
        open(out, "w").write(r["content_md"])
        print(f"written -> {out}")
    else:
        print(r["content_md"])


@app.command()
def pull(
    company: str = typer.Argument(None, help="company id; omit for the whole basket"),
    social: bool = typer.Option(True, help="also pull X/Reddit social signal"),
) -> None:
    """Pull structured + alternative data (fundamentals/estimates/prices/insider/
    prediction-markets/social) from all configured providers, normalize onto the
    canonical metric vocabulary, and derive KG signal events."""
    from . import providers

    st = providers.status()
    active = [k for k, v in st.items() if v]
    print(f"[cyan]providers active:[/cyan] {', '.join(active) or '(none configured)'}")
    if company:
        out = providers.pull_company(company, with_social=social)
    else:
        out = providers.pull_basket(with_social=social)
    print(json.dumps(out, indent=2, default=str))


@app.command("pull-rss")
def pull_rss(
    feed: str = typer.Argument(None, help="feed id (see --list); omit for all curated feeds"),
    since: str = typer.Option(None, help="ISO datetime lower bound; omit for the full window"),
    limit: int = typer.Option(50, help="max entries per feed"),
    list_feeds: bool = typer.Option(False, "--list", help="show the curated feed registry"),
) -> None:
    """Pull curated industry-news RSS/Atom feeds (8 themes, 丰富资讯来源) into
    theme-tagged documents. Idempotent (content-hash dedup); also runs nightly
    when 'rss' is in XAR_DAILY_ENABLED_SOURCES."""
    from .ingestion.feeds import FEEDS
    from .providers import rss

    if list_feeds:
        t = Table("id", "name", "themes", "lang", "url")
        for f in FEEDS:
            t.add_row(f["id"], f["name"], ",".join(f["themes"]), f["lang"], f["url"])
        print(t)
        raise typer.Exit(0)
    n = rss.pull(feed, since=since, limit=limit)
    print(f"[green]rss:[/green] {n} docs saved")


@app.command()
def daily(
    sources: str = typer.Option(None, help="CSV of sources; omit for XAR_DAILY_ENABLED_SOURCES"),
    since: str = typer.Option("auto", help="auto (incremental cursor) | full | ISO date"),
    shard: int = typer.Option(None, help="shard index k (with --n-shards) for a universe slice"),
    n_shards: int = typer.Option(1, help="split the universe into N nightly shards"),
) -> None:
    """Run one daily incremental ingest+update pass across all sources: pull → parse
    → semantic extract → expert → signals. Idempotent/resumable; logs to ingest_runs."""
    from .orchestration.daily import run_daily

    srcs = [x.strip() for x in sources.split(",") if x.strip()] if sources else None
    stats = run_daily(srcs, since=since, shard=shard, n_shards=n_shards)
    print(json.dumps(stats, indent=2, default=str))


@app.command("resolve-claims")
def resolve_claims_cmd(
    window_days: int = typer.Option(120, help="realization window (days) for a forward claim"),
    grace_days: int = typer.Option(21, help="min age (days) before a claim is first evaluated"),
) -> None:
    """Close the forward-claim loop: mark forward_looking catalysts hit/miss/stale once a
    later realized event appears (or the window lapses). Idempotent; also runs nightly."""
    from .kg.resolve_claims import resolve_forward_claims

    print(json.dumps(resolve_forward_claims(window_days=window_days, grace_days=grace_days), indent=2))


@app.command()
def providers_status() -> None:
    """Show which market-data / alt-data providers are configured."""
    from . import providers

    t = Table("provider", "configured")
    for name, ok in providers.status().items():
        t.add_row(name, "[green]yes[/green]" if ok else "—")
    print(t)


@app.command()
def backtest() -> None:
    """Catalyst -> forward-return signal efficacy."""
    from .backtest import backtest as bt

    print(json.dumps(bt(), indent=2))


@app.command("eval")
def evaluate() -> None:
    """Offline retrieval hit-rate over the gold set."""
    from .eval import eval_retrieval

    print(json.dumps(eval_retrieval(), indent=2, ensure_ascii=False))


@app.command()
def status() -> None:
    """Show row counts across the platform."""
    from .storage import db

    t = Table("table", "rows")
    for tbl in ["companies", "documents", "chunks", "kg_nodes", "kg_edges", "kg_events",
                "fundamentals", "estimates", "analyst_ratings", "prices", "insider_trades",
                "prediction_markets", "social_posts", "reports"]:
        try:
            n = db.query(f"SELECT count(*) AS n FROM {tbl}")[0]["n"]
        except Exception:
            n = "—"
        t.add_row(tbl, str(n))
    print(t)


@app.command()
def explore(
    domain: str = typer.Argument(None, help="domain id (ai, physics, math, …); omit for all"),
    days: int = typer.Option(None, help="arXiv lookback window (days)"),
    voices: bool = typer.Option(True, help="also pull X expert voices"),
    synthesize: bool = typer.Option(True, help="synthesize research fronts after ingest"),
) -> None:
    """Exploration: ingest frontier preprints (+ expert voices) and synthesize the
    forward-looking research fronts per domain (AI is the first section)."""
    from .exploration import ingest, synthesis
    from .exploration.domains import DOMAINS

    ids = [domain] if domain else [d["id"] for d in DOMAINS]
    for did in ids:
        ing = ingest.ingest_domain(did, days=days, voices=voices)
        print(f"[cyan]{did}[/cyan]: ingested {ing}")
        if synthesize:
            print(f"[green]{did}[/green]: {synthesis.synthesize(did)}")


# ── XAR Andy (siliconomics macro module, vendored src/slx) ─────────────────────
andy_app = typer.Typer(add_completion=False,
                       help="Andy — 宏观指标模块（siliconomics）：registry / ingest / 判定")
app.add_typer(andy_app, name="andy")


def _bridge_slx_env() -> None:
    """Bridge XAR settings → the env keys the vendored slx connectors read (CLI path;
    the API path does the same in xar.api.andy_mount)."""
    import os

    from .config import get_settings

    s = get_settings()
    for key, val in (("SLX_DATABASE_URL", s.database_url),
                     ("SEC_EDGAR_USER_AGENT", s.edgar_identity),
                     ("FRED_API_KEY", s.fred_api_key), ("BEA_API_KEY", s.bea_api_key),
                     ("EIA_API_KEY", s.eia_api_key), ("EMBER_API_KEY", s.ember_api_key),
                     ("ACLED_API_KEY", s.acled_api_key), ("ACLED_EMAIL", s.acled_email),
                     ("TICKETMASTER_API_KEY", s.ticketmaster_api_key),
                     ("SLACK_WEBHOOK_URL", s.slx_slack_webhook)):
        if val:
            os.environ.setdefault(key, val)


def _andy_init_impl() -> None:
    from slx.db import init_schema
    from slx.tools.load_registry import main as load_registry

    _bridge_slx_env()
    init_schema()
    load_registry()


def _parse_as_of(as_of: str | None):
    from datetime import date

    return date.fromisoformat(as_of) if as_of else date.today()


@andy_app.command("init")
def andy_init() -> None:
    """Create the `slx` schema and load the theory/metric/overclaim registry (idempotent)."""
    _andy_init_impl()
    print("[green]✓ andy initialized[/green] (slx schema + registry)")


@andy_app.command("ingest")
def andy_ingest(
    seed: bool = typer.Option(True, help="run the deterministic no-network SeedConnector"),
    connector: str = typer.Option(None, help="run ONE real connector by source_id (e.g. sec_edgar)"),
    all_real: bool = typer.Option(False, "--all-real", help="run every discovered primary connector (network)"),
) -> None:
    """Ingest macro observations. Default = seed only (offline, deterministic);
    real connectors are opt-in (keyless set: sec_edgar epoch_ai fhfa lbnl indeed_hiring_lab bls stooq)."""
    from slx.ingestion.discovery import discover_connectors, resolve_connector

    _bridge_slx_env()
    if seed and not connector:
        from slx.ingestion.seed import SeedConnector

        SeedConnector().run()
        print("[green]seed:[/green] deterministic observations written")
    if connector:
        conn, is_primary = resolve_connector(connector)
        if conn is None:
            print(f"[red]unknown connector:[/red] {connector}")
            raise typer.Exit(1)
        if not is_primary:
            print(f"[yellow]{connector} is a secondary source[/yellow] (covered by {conn.source_id})")
        conn.run()
        print(f"[green]{conn.source_id}:[/green] run complete (audit_log has the outcome)")
    if all_real:
        for src, (cls, is_primary) in sorted(discover_connectors().items()):
            if not is_primary or src == "seed":
                continue
            try:
                cls().run()
                print(f"[green]{src}[/green] ok")
            except Exception as e:  # noqa: BLE001 — one flaky source must not sink the sweep
                print(f"[yellow]{src}[/yellow] {e}")


@andy_app.command("identify")
def andy_identify(as_of: str = typer.Option(None, help="ISO date; default today")) -> None:
    """Run the identification engine (DID / within-FE) and write derived estimates PIT."""
    from slx.ingestion.identification_panels import run_identification

    _bridge_slx_env()
    run_identification(_parse_as_of(as_of))
    print("[green]✓ identification[/green] derived estimates written")


@andy_app.command("evaluate")
def andy_evaluate(as_of: str = typer.Option(None, help="ISO date; default today"),
                  sync: bool = typer.Option(True, help="then emit 勾稽 events into kg_events")) -> None:
    """Evaluate the 9 overclaim-registry claims at as_of (writes eval log + status),
    then sync macro prints + verdict transitions into the semantic stream."""
    from slx.engine import overclaim

    _bridge_slx_env()
    for claim_key, verdict in overclaim.run(_parse_as_of(as_of)):
        print(f"  {claim_key}: {verdict}")
    if sync:
        from .ingestion import macro_bridge

        print(json.dumps(macro_bridge.sync(_parse_as_of(as_of)), ensure_ascii=False))


@andy_app.command("sync-events")
def andy_sync_events(as_of: str = typer.Option(None, help="ISO date; default today")) -> None:
    """勾稽数据层同步：宏观印字 + 登记簿判定跃迁 → kg_events(macro_print) → semantic_facts。
    Idempotent (dedup_key)."""
    from .ingestion import macro_bridge

    _bridge_slx_env()
    print(json.dumps(macro_bridge.sync(_parse_as_of(as_of)), ensure_ascii=False, indent=2))


@andy_app.command("status")
def andy_status() -> None:
    """Row counts + claim statuses of the macro module."""
    from slx.db import connect

    _bridge_slx_env()
    t = Table("slx table", "rows")
    with connect() as c:
        for tbl in ["theory_anchor", "metric_registry", "metric_source", "observation",
                    "panel_observation", "overclaim_registry", "overclaim_eval_log", "audit_log"]:
            try:
                t.add_row(tbl, str(c.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0]))
            except Exception:  # noqa: BLE001
                t.add_row(tbl, "—")
        print(t)
        try:
            rows = c.execute("SELECT claim_key, status FROM overclaim_registry ORDER BY claim_key").fetchall()
            s = Table("claim", "status")
            for k, v in rows:
                s.add_row(k, v)
            print(s)
        except Exception:  # noqa: BLE001
            pass


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the web UI + API."""
    import uvicorn

    uvicorn.run("xar.api.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
