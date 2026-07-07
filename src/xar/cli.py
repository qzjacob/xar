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


@app.command()
def gangtise(
    company: str = typer.Argument(None, help="company id; omit for a rotating CN slice"),
    limit: int = typer.Option(10, help="companies to pull when no id is given"),
) -> None:
    """Pull Gangtise 投研 (financials/valuation/一致预期 → canonical metrics + 投研文本 →
    documents) for a CN A-share name, or a slice of the CN basket. Needs GTS keys +
    XAR_ENABLE_GANGTISE."""
    from .ingestion.registry import COMPANIES
    from .providers import gangtise as gts

    if not gts.available():
        print("[yellow]gangtise unavailable[/yellow] — set GTS_ACCESS_KEY/GTS_SECRET_KEY + "
              "XAR_ENABLE_GANGTISE=true")
        raise typer.Exit(1)
    if company:
        ids = [company]
    else:
        ids = [c["id"] for c in COMPANIES
               if any(str(t).endswith((".SS", ".SH", ".SZ")) for t in (c.get("tickers") or []))][:limit]
    out = {cid: gts.pull(cid) for cid in ids}
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
def futu(
    company: str = typer.Argument(None, help="company id; omit for status + gap report"),
    gaps: bool = typer.Option(False, "--gaps", help="show Futu-plate ontology gaps"),
) -> None:
    """富途 (Futu/OpenD): pull one company's snapshot+news+plates, or show status/gaps.
    Needs XAR_ENABLE_FUTU=true + a running OpenD gateway."""
    from .providers import futu as ft

    if not ft.available():
        print("[yellow]Futu OpenD unreachable[/yellow] — set XAR_ENABLE_FUTU=true and run OpenD "
              "(127.0.0.1:11111).")
        raise typer.Exit(0)
    if company:
        print(json.dumps(ft.pull(company), indent=2, default=str))
    if gaps or not company:
        rows = ft.plate_theme_gaps(limit=30)
        t = Table("company", "futu implies", "currently curated")
        for r in rows:
            t.add_row(r["company_id"], ",".join(r["futu_implied"]), ",".join(r["curated"] or []))
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


# ── GLM 常驻抽取工人(订阅额度机制利用)─────────────────────────────────────────
glm_app = typer.Typer(add_completion=False,
                      help="GLM 订阅额度感知的常驻抽取工人(耗尽自动等待,恢复自动重启)")
app.add_typer(glm_app, name="glm-worker")


@glm_app.command("run")
def glm_worker_run(
    once: bool = typer.Option(False, "--once", help="run a single cycle and exit"),
) -> None:
    """常驻循环(默认)或单轮:语义源拉取 + 10 年历史回填 + 钉扎 GLM 的语义抽取。"""
    from .orchestration import glm_worker

    if once:
        print(json.dumps(glm_worker.run_once(), ensure_ascii=False, indent=2, default=str))
    else:
        glm_worker.run_daemon()


@glm_app.command("status")
def glm_worker_status() -> None:
    """额度状态 / 计数器 / 回填游标 / 抽取积压。"""
    from .orchestration import glm_worker

    print(json.dumps(glm_worker.status(), ensure_ascii=False, indent=2, default=str))


@glm_app.command("probe")
def glm_worker_probe() -> None:
    """手动探针:GLM 订阅池当前是否可用。"""
    from .orchestration import glm_worker

    ok = glm_worker.probe()
    print("[green]GLM quota OK[/green]" if ok else "[yellow]GLM quota exhausted[/yellow]")
    raise typer.Exit(0 if ok else 1)


# ── 投资论点(CompanyThesis)────────────────────────────────────────────────────
# ── 微信多层级挖掘(mining/)────────────────────────────────────────────────────
wechat_app = typer.Typer(add_completion=False, help="微信策展账号名册 + 挖掘目标")
app.add_typer(wechat_app, name="wechat-account")


@wechat_app.command("add")
def wechat_account_add(
    feed_id: str, name: str = typer.Option(""), theme: str = typer.Option(None),
    company: str = typer.Option(None, help="绑定公司 id"), tier: int = typer.Option(2),
) -> None:
    """登记一个策展公众号 feed_id(先在 we-mp-rss UI 订阅该号)。"""
    from .mining import roster

    roster.register(feed_id, name=name, theme=theme, company_id=company, tier=tier)
    print(f"[green]registered[/green] {feed_id}")


@wechat_app.command("list")
def wechat_account_list() -> None:
    """列出策展名册。"""
    from .mining import roster

    t = Table("feed_id", "name", "theme", "company", "tier")
    for r in roster.active_feeds():
        t.add_row(r["feed_id"], r.get("name") or "", r.get("theme") or "",
                  r.get("company_id") or "", str(r.get("tier")))
    print(t)
    print(json.dumps(roster.status(), ensure_ascii=False))


@wechat_app.command("rm")
def wechat_account_rm(feed_id: str) -> None:
    from .mining import roster

    roster.deactivate(feed_id)
    print(f"[yellow]deactivated[/yellow] {feed_id}")


@app.command("wechat-targets")
def wechat_targets(limit: int = typer.Option(20)) -> None:
    """当前挖掘目标(被挑战论点优先)+ 中文猎词。"""
    from .mining import targeting

    targets = targeting.build_targets(limit)
    for t in targets:
        flag = "🔴" if t.priority >= 1.0 else "  "
        print(f"{flag} {t.company_id:16} {t.name[:24]:24} watch={list(t.watch_event_types)[:3]} "
              f"猎词={list(t.hunt_terms_zh)[:4]}")


@app.command("wechat-mine")
def wechat_mine(
    once: bool = typer.Option(False, "--once", help="triage 一批待处理微信文档并打印统计"),
    limit: int = typer.Option(40, help="本批 triage 的文档数"),
    stats_only: bool = typer.Option(False, "--stats", help="只打印 triage 库总览"),
) -> None:
    """微信 SNR triage:给待抽取的微信文档打 triage_score(GLM 钉扎、订阅计费)。
    高分文档才进深度抽取队列;--stats 看保留率(对比旧的 3.75%)。"""
    from .mining import triage
    from .models import llm
    from .orchestration.glm_worker import GLM_PIN

    if stats_only:
        print(json.dumps(triage.stats(), ensure_ascii=False, indent=2, default=str))
        return
    with llm.pinned(GLM_PIN):
        out = triage.triage_pending(limit=limit)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(json.dumps(triage.stats(), ensure_ascii=False, indent=2, default=str))


# ── 另类数据追踪(alt-data)────────────────────────────────────────────────────
alt_app = typer.Typer(add_completion=False,
                      help="另类数据:追踪器拉取 / 阈值信号→事件 / 论点信号快照")
app.add_typer(alt_app, name="alt")


@alt_app.command("pull")
def alt_pull(
    source: str = typer.Argument(None, help="single source id (twse_revenue/github_metrics/…); omit for all"),
    limit: int = typer.Option(None, help="cap companies per source (wiki/github pacing)"),
) -> None:
    """跑另类数据追踪器,写入 alt_signals(缺失的 provider 优雅跳过)。"""
    from .ingestion import alt

    stats = alt.pull_all([source] if source else None, limit=limit)
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))


@alt_app.command("sync-events")
def alt_sync() -> None:
    """|z|>=2 的新期另类信号 → kg_events(alt_signal) → semantic_facts(幂等)。"""
    from .ingestion import alt

    print(json.dumps(alt.sync_events(), ensure_ascii=False, indent=2))


@alt_app.command("snapshot")
def alt_snapshot(company: str) -> None:
    """一家公司的另类信号统计快照 + 支柱信号分。"""
    from .research import thesis_signals

    snap = thesis_signals.signal_snapshot(company)
    scores = thesis_signals.pillar_signal_scores(company)
    print(json.dumps({"signals": snap, "pillar_scores": scores},
                     ensure_ascii=False, indent=2, default=str))


@alt_app.command("challenged")
def alt_challenged(limit: int = typer.Option(10)) -> None:
    """信号面挑战最重的既有论点(glm_worker 据此排队重建)。"""
    from .research import thesis_signals

    for cid in thesis_signals.challenged_companies(limit=limit):
        print(cid)


@alt_app.command("status")
def alt_status() -> None:
    """alt_signals 库总览:每信号行数/覆盖公司数 + 绑定覆盖。"""
    from .ontology import altdata
    from .storage import db

    t = Table("signal_key", "rows", "companies", "themes", "latest")
    for r in db.query("SELECT signal_key, count(*) n, count(DISTINCT company_id) c, "
                      "count(DISTINCT theme) th, max(period_end) mx FROM alt_signals GROUP BY 1 ORDER BY 1"):
        t.add_row(r["signal_key"], str(r["n"]), str(r["c"]), str(r["th"]), str(r["mx"]))
    print(t)
    print(json.dumps(altdata.coverage_summary(), ensure_ascii=False, indent=2))


thesis_app = typer.Typer(add_completion=False,
                         help="投资论点:生成/刷新/健康度(research/thesis.py)")
app.add_typer(thesis_app, name="thesis")


@thesis_app.command("build")
def thesis_build_cmd(
    company: str = typer.Argument(None, help="company id; omit with --theme/--all for batch"),
    theme: str = typer.Option(None, help="batch: one theme's roster (coverage-ranked)"),
    all_companies: bool = typer.Option(False, "--all", help="batch: whole universe"),
    limit: int = typer.Option(None, help="batch cap"),
    force: bool = typer.Option(False, help="rebuild even without new facts"),
    quality: bool = typer.Option(False, help="EDITOR-tier quality pass (token-billed)"),
) -> None:
    """生成/刷新论点。单司同步返回;批量按覆盖度从高到低走查(订阅池,幂等)。"""
    from .research import thesis

    if company:
        print(json.dumps(thesis.build(company, force=force, quality_tier=quality),
                         ensure_ascii=False, indent=2, default=str))
        return
    if not (theme or all_companies):
        print("[red]give a company id, --theme, or --all[/red]")
        raise typer.Exit(1)
    print(json.dumps(thesis.build_batch(theme=theme, limit=limit, force=force),
                     ensure_ascii=False, indent=2))


@thesis_app.command("show")
def thesis_show(company: str) -> None:
    """打印最新论点(支柱/证据锚/风险/健康度)。"""
    from .research import thesis

    row = thesis.latest(company)
    if row is None:
        print("[yellow]no thesis yet[/yellow] — run: xar thesis build " + company)
        raise typer.Exit(0)
    c = row["content"]
    print(f"[bold]{company}[/bold] v{row['version']} {row['stance']} "
          f"conviction={row['conviction']} as_of={row['as_of']}")
    print(f"[cyan]{c['one_liner_zh']}[/cyan]\n{c['narrative_zh']}")
    for pl in c["pillars"]:
        print(f"  [{pl['kind']}] {pl['title_zh']} w={pl['weight']} score={pl['score']} "
              f"证据={len(pl['evidence'])}")
    h = thesis.health(company)
    if h:
        print(f"健康度: {h['overall']} " +
              str([(x['key'], x['status']) for x in h['pillars']]))


@thesis_app.command("link")
def thesis_link(
    company: str = typer.Argument(None, help="company id;省略=批量(按待链接事实数排序)"),
    limit: int = typer.Option(15, help="批量:处理的公司数"),
) -> None:
    """把新到事实做相对主张分类,写入 thesis_fact_links(LLM 语义道 + 零 LLM 数值道)。"""
    from .research import evidence_link, thesis

    if company:
        row = thesis.latest(company)
        if row is None:
            print("[yellow]no thesis[/yellow] — run: xar thesis build " + company)
            raise typer.Exit(0)
        links = evidence_link.link_company(company, row)
        vps = evidence_link.check_verification_points(company, row)
        print(json.dumps({"links": links, "vp_checks": vps}, ensure_ascii=False, indent=2, default=str))
        return
    out = {"links": evidence_link.link_pending(limit), "vp_checks": evidence_link.check_pending(limit)}
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


@thesis_app.command("health")
def thesis_health_cmd(company: str) -> None:
    """争论感知健康度(health_v3):争论天平 lean / VP 读数 vs 阈值 / top facts。"""
    from .research import thesis_health

    h = thesis_health.health_v3(company)
    if h is None:
        print("[yellow]no thesis[/yellow] — run: xar thesis build " + company)
        raise typer.Exit(0)
    print(f"[bold]{company}[/bold] overall={h['overall']} "
          f"(debate_challenged={h.get('debate_challenged')})")
    pt = Table("pillar", "status", "signal_score")
    for p in h["pillars"]:
        pt.add_row(p["key"], p["status"], str(p.get("signal_score")))
    print(pt)
    if h.get("debates"):
        dt_ = Table("debate", "status", "lean_now", "authored", "weight", "n")
        for d in h["debates"]:
            dt_.add_row(d["key"], d["status"], f"{d['lean_now']:+.2f}",
                        f"{d['lean_authored']:+.2f}", f"{d['weight']:.2f}", str(d["n_facts"]))
        print(dt_)
        for d in h["debates"]:
            for vp in d.get("vp_readings", []):
                print(f"  · {d['key']} VP {vp['metric']}: {vp['verdict']} — {vp['note']}")


@thesis_app.command("theme-debates")
def thesis_theme_debates(theme: str) -> None:
    """主题级核心争论健康度(成员旗舰 lean 聚合 + 翻转清单)。"""
    from .research import thesis_health

    out = thesis_health.theme_debate_health(theme)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


@thesis_app.command("links")
def thesis_links(company: str, limit: int = typer.Option(30)) -> None:
    """打印一家公司最近的证据裁决(人工抽查:rationale 是否真'相对主张')。"""
    from .storage import db

    t = Table("date", "kind", "target", "verdict", "str", "origin", "rationale")
    for r in db.query(
            "SELECT as_of, fact_kind, target_kind, target_key, verdict, strength, origin, rationale_zh "
            "FROM thesis_fact_links WHERE company_id=%s AND target_kind<>'none' "
            "ORDER BY created_at DESC LIMIT %s", (company, limit)):
        t.add_row(str(r["as_of"]), r["fact_kind"], f"{r['target_kind']}:{r['target_key']}",
                  r["verdict"], f"{r['strength']:.2f}" if r["strength"] is not None else "",
                  r["origin"], (r["rationale_zh"] or "")[:50])
    print(t)


@thesis_app.command("status")
def thesis_status() -> None:
    """论点库总览:版本数/立场分布/最近生成。"""
    from .storage import db

    t = Table("metric", "value")
    for r in db.query(
            "SELECT count(DISTINCT company_id) AS companies, count(*) AS versions, "
            "max(created_at) AS latest FROM company_thesis"):
        t.add_row("companies covered", str(r["companies"]))
        t.add_row("total versions", str(r["versions"]))
        t.add_row("latest build", str(r["latest"]))
    for r in db.query("SELECT stance, count(*) FROM ("
                      "SELECT DISTINCT ON (company_id) company_id, stance FROM company_thesis "
                      "ORDER BY company_id, version DESC) x GROUP BY stance"):
        t.add_row(f"stance {r['stance']}", str(r["count"]))
    print(t)


research_app = typer.Typer(add_completion=False,
                           help="CN 非标语义抓取:券商研报/纪要/MD&A 抓取 + 回填 + 独立审计")
app.add_typer(research_app, name="research")


@research_app.command("crawl")
def research_crawl() -> None:
    """跑一次每日增量抓取(clue 雷达 + 研报/纪要日期窗扫 + 核心 MD&A + 评级第二遍)。"""
    from .providers.gangtise import planner

    print(json.dumps(planner.fresh_sweep(), ensure_ascii=False, indent=2, default=str))


@research_app.command("backfill")
def research_backfill(
    units: int = typer.Option(2, help="本次回填的 (doc_type,月窗) 单元数"),
    reset: bool = typer.Option(False, "--reset", help="清游标/exhausted 戳,从最新月重新向深挖"),
) -> None:
    """历史回填一步(月窗最新先行,自适应空窗停机;MD&A 走季度序)。"""
    from .providers.gangtise import planner

    if reset:
        planner.reset_backfill()
    print(json.dumps({"step": planner.backfill_step(units), "status": planner.backfill_status()},
                     ensure_ascii=False, indent=2, default=str))


@research_app.command("audit")
def research_audit_cmd(no_llm: bool = typer.Option(False, "--no-llm", help="只跑零 LLM 完整性对账")) -> None:
    """独立审计:完整性对账 + (可选)强 token 模型抽样复核 → 失败重排队。"""
    from .orchestration import research_audit

    print(json.dumps(research_audit.run_audit(no_llm=no_llm), ensure_ascii=False, indent=2, default=str))


@research_app.command("status")
def research_status() -> None:
    """非标语义抓取库总览:各 doc_type 计数 + 回填态 + EDB 新鲜度。"""
    from .orchestration import research_audit
    from .providers.gangtise import planner

    print(json.dumps({"integrity": research_audit.integrity_report(),
                      "backfill": planner.backfill_status()},
                     ensure_ascii=False, indent=2, default=str))


indicators_app = typer.Typer(add_completion=False,
                             help="衍生追踪指标:从 fundamentals 计算同比/增速二阶导/趋势(零 LLM)")
app.add_typer(indicators_app, name="indicators")


@indicators_app.command("compute")
def indicators_compute(
    company: str = typer.Argument(None, help="company id;省略=全库有数据的公司"),
    limit: int = typer.Option(None, help="批量上限"),
) -> None:
    """计算衍生指标写回 fundamentals(source='derived',幂等)。"""
    from .research import indicators

    if company:
        out = indicators.compute_company(company)
    else:
        out = indicators.compute_all(limit=limit)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


@indicators_app.command("status")
def indicators_status() -> None:
    """衍生指标库总览:每指标行数/覆盖公司数/最新期。"""
    from .storage import db

    t = Table("indicator", "rows", "companies", "latest")
    for r in db.query(
            "SELECT metric, count(*) n, count(DISTINCT company_id) c, max(period_end) mx "
            "FROM fundamentals WHERE source='derived' GROUP BY 1 ORDER BY 1"):
        t.add_row(r["metric"], str(r["n"]), str(r["c"]), str(r["mx"]))
    print(t)


@app.command()
def reembed(
    model: str = typer.Option("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                              help="fastembed 模型名(默认多语 MiniLM 384d,快;中英混合"
                                   "最高质量用 intfloat/multilingual-e5-large --dim 1024,CPU 上慢)"),
    dim: int = typer.Option(384, help="该模型维度(必须与 --model 一致)"),
    batch: int = typer.Option(256, help="每批 chunk 数"),
    max_seconds: int = typer.Option(0, help="到时干净退出并提交进度(0=跑到完;配合续跑分片推进)"),
) -> None:
    """全库重嵌入到新模型(中文检索升级)。ALTER chunks 维度 → 分批重嵌 → 重建索引。
    完成后请把 XAR_EMBED_MODEL/XAR_EMBED_DIM 写入 .env 并重启,使查询侧用同一模型。
    可中断续跑(维度或模型变更 → 全表清空重嵌;否则续跑 WHERE NULL;--max-seconds 分片)。
    注意:维度/模型变更会 DROP+ADD embedding 列,重嵌期间(可能数十分钟~数小时)向量检索返回空
    —— 建议先停 app/glmworker 或择低峰,跑完设 .env 再一并重启。"""
    import os

    from .config import get_settings
    from .models import embeddings
    from .storage import db

    # 强制本进程用新模型(含 e5 前缀逻辑),与查询侧一致
    os.environ["XAR_EMBED_MODEL"] = model
    os.environ["XAR_EMBED_DIM"] = str(dim)
    get_settings.cache_clear()
    print(f"[cyan]loading[/cyan] {model} (dim={dim})…")
    _ = embeddings  # noqa: F401 — 保证 env 覆盖前模块已导入

    from fastembed import TextEmbedding

    # 单进程 + ONNX 全线程(threads=0=全部核):模型常驻,forward 多线程,
    # 避免 parallel= 每次 embed() 重建进程池/重载模型的巨大开销。
    _emb = TextEmbedding(model_name=model, threads=0)
    _is_e5 = "e5" in model.lower()

    def _vecs(texts: list[str]) -> list[list[float]]:
        payload = [f"passage: {t}" for t in texts] if _is_e5 else texts
        return [list(map(float, v)) for v in _emb.embed(payload, batch_size=32)]

    from .storage.kvstate import get_state, save_state

    total = db.query("SELECT count(*) n FROM chunks")[0]["n"]
    cur = db.query("SELECT atttypmod AS m FROM pg_attribute "
                   "WHERE attrelid='chunks'::regclass AND attname='embedding'")
    cur_dim = cur[0]["m"] if cur else -1  # pgvector: atttypmod == dim (-1 若无列)
    stored_model = get_state("embed").get("model")
    # 清空重嵌的触发:维度变更 **或** 同维换模型(否则 resume 只填 NULL=0 行,
    # 库里留旧模型向量,查询侧却用新模型 → 两个不兼容向量空间静默混用)。
    needs_full = cur_dim != dim or (stored_model not in (None, model))
    if needs_full:
        print(f"[cyan]migrating[/cyan] chunks.embedding {cur_dim}→{dim} model "
              f"{stored_model}→{model}; {total} rows (清空重嵌)")
        with db.tx() as conn:
            conn.execute("DROP INDEX IF EXISTS idx_chunks_vec")
            conn.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS embedding")
            conn.execute(f"ALTER TABLE chunks ADD COLUMN embedding vector({dim})")
    else:
        remaining = db.query("SELECT count(*) n FROM chunks WHERE embedding IS NULL "
                             "AND text IS NOT NULL")[0]["n"]
        print(f"[cyan]resuming[/cyan] {model} vector({dim}); {remaining}/{total} left")
    save_state("embed", {"model": model, "dim": dim})  # 供下次 resume/换模型判定
    import time as _time

    started = _time.monotonic()
    done = 0
    hit_deadline = False
    while True:
        rows = db.query("SELECT id, text FROM chunks WHERE embedding IS NULL "
                        "AND text IS NOT NULL ORDER BY id LIMIT %s", (batch,))
        if not rows:
            break
        vecs = _vecs([r["text"] for r in rows])
        with db.tx() as conn:
            for r, v in zip(rows, vecs):
                conn.execute("UPDATE chunks SET embedding=%s::vector WHERE id=%s",
                             (str(v), r["id"]))
        done += len(rows)
        if done % (batch * 4) == 0:
            print(f"  re-embedded {done}/{total}", flush=True)
        if max_seconds and _time.monotonic() - started >= max_seconds:
            hit_deadline = True
            break
    from .storage import db as _db

    if hit_deadline:
        left = db.query("SELECT count(*) n FROM chunks WHERE embedding IS NULL "
                        "AND text IS NOT NULL")[0]["n"]
        if left == 0:                       # 恰好在本片补齐 → 补建索引(dim-change 已 DROP)
            _db.ensure_vector_index()
            print("[green]✓ reembed done[/green](末片)re-embedded 全部;索引已重建。")
        else:
            print(f"[yellow]时间片到[/yellow] 本片 {done} 块;剩 {left} 块,再次运行同命令续跑"
                  f"(全部完成后自动重建 ANN 索引)。")
        raise typer.Exit(0)
    print(f"[green]re-embedded {done} chunks[/green]; rebuilding ANN index…")
    _db.ensure_vector_index()
    print(f"[green]✓ reembed done[/green]  →  在 .env 设 XAR_EMBED_MODEL={model} "
          f"XAR_EMBED_DIM={dim} 并重启应用/worker")


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the web UI + API."""
    import uvicorn

    uvicorn.run("xar.api.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
