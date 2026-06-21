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


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the web UI + API."""
    import uvicorn

    uvicorn.run("xar.api.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
