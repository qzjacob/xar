"""Ontology bridge: turn STRUCTURED + ALT data into the knowledge graph.

This is how Finnhub/FMP/Polygon estimates, insider filings, and Polymarket odds
"enter the ontology": each is distilled into the same bitemporal `kg_events`
catalyst stream the LLM extracts from filings — so retrieval, the agent debate,
and the backtest treat a consensus revision or a prediction-market shift exactly
like a filing-sourced catalyst. Social/research text is mirrored into `documents`
so it flows through RAG + LLM extraction too.

Mapping stays inside the 10-type catalyst taxonomy; the precise signal sub-class
is recorded in the event summary (see ontology.standards.SIGNAL_TO_CATALYST)."""
from __future__ import annotations

from ..logging import get_logger
from ..ontology.standards import SIGNAL_TO_CATALYST, FinMetric
from ..storage import db
from . import store

log = get_logger("xar.kg.signals")

# Thresholds (conservative — a signal must be material to enter the graph).
_REVISION_PCT = {FinMetric.REVENUE.value: 2.0, FinMetric.EPS_DILUTED.value: 3.0,
                 FinMetric.EBITDA.value: 3.0}
_INSIDER_MIN_BUYERS = 3
_INSIDER_MIN_NET_USD = 250_000
_PM_MIN_PROB = 0.60


# --- company-level structured signals --------------------------------------
def derive_for_company(company_id: str) -> dict:
    return {"estimate_revisions": _estimate_revisions(company_id),
            "insider": _insider_cluster(company_id)}


def _estimate_revisions(company_id: str) -> int:
    pairs = db.query(
        "SELECT DISTINCT metric, period FROM estimates WHERE company_id=%s "
        "AND metric=ANY(%s)", (company_id, list(_REVISION_PCT.keys())))
    n = 0
    for row in pairs:
        metric, period = row["metric"], row["period"]
        snaps = db.query(
            "SELECT as_of, value FROM estimates WHERE company_id=%s AND metric=%s "
            "AND period=%s AND value IS NOT NULL ORDER BY as_of DESC LIMIT 2",
            (company_id, metric, period))
        if len(snaps) < 2 or not snaps[1]["value"]:
            continue
        new, old = snaps[0]["value"], snaps[1]["value"]
        pct = (new / old - 1.0) * 100 if old else 0.0
        if abs(pct) < _REVISION_PCT[metric]:
            continue
        up = pct > 0
        etype = SIGNAL_TO_CATALYST["estimate_revision_up" if up else "estimate_revision_down"]
        added = store.add_event(
            company_id, company_id, etype, event_date=snaps[0]["as_of"],
            magnitude=f"{pct:+.1f}% {metric} {period}", polarity="positive" if up else "negative",
            summary=f"Consensus {metric} for {period} revised {pct:+.1f}% (signal: estimate_revision)",
            confidence=0.6, license_tag="signal")
        n += int(added)
    return n


def _insider_cluster(company_id: str) -> int:
    rows = db.query(
        """SELECT txn_type, COUNT(DISTINCT insider) AS buyers,
                  COALESCE(SUM(value),0) AS net
           FROM insider_trades
           WHERE company_id=%s AND txn_date >= (CURRENT_DATE - INTERVAL '90 days')
           GROUP BY txn_type""", (company_id,))
    buys = next((r for r in rows if r["txn_type"] == "buy"), None)
    if not buys or (buys["buyers"] < _INSIDER_MIN_BUYERS and buys["net"] < _INSIDER_MIN_NET_USD):
        return 0
    etype = SIGNAL_TO_CATALYST["insider_cluster_buy"]
    added = store.add_event(
        company_id, company_id, etype, magnitude=f"{buys['buyers']} buyers / ${buys['net']:,.0f}",
        polarity="positive",
        summary=f"Insider cluster buying: {buys['buyers']} insiders, ${buys['net']:,.0f} (signal: insider_cluster_buy)",
        confidence=0.55, license_tag="signal")
    return int(added)


# --- market-wide alt-data signals ------------------------------------------
def derive_market_signals() -> int:
    """Prediction-market probabilities -> forward capex / launch catalysts for the
    watched names they reference."""
    rows = db.query(
        """SELECT DISTINCT ON (market_id) market_id, question, outcome, probability,
                  company_id, tech_route_tag
           FROM prediction_markets
           WHERE company_id IS NOT NULL AND probability IS NOT NULL
           ORDER BY market_id, probability DESC""")
    n = 0
    for r in rows:
        prob = r["probability"] or 0
        if prob < _PM_MIN_PROB:
            continue
        is_launch = any(k in (r["question"] or "").lower()
                        for k in ("launch", "release", "ship", "rubin", "blackwell"))
        key = "prediction_market_launch" if is_launch else "prediction_market_capex"
        etype = SIGNAL_TO_CATALYST[key]
        added = store.add_event(
            r["company_id"], r["company_id"], etype,
            magnitude=f"P={prob:.0%}", polarity="positive", tech_route_tag=r["tech_route_tag"],
            summary=f"Prediction market: \"{(r['question'] or '')[:120]}\" → {r['outcome']} {prob:.0%} "
                    f"(signal: {key})",
            confidence=0.5, license_tag="signal")
        n += int(added)
    log.info("derived %d prediction-market signals", n)
    return n


# --- unstructured social -> RAG/ontology -----------------------------------
def mirror_social(company_id: str, limit: int = 25) -> int:
    """Mirror high-signal social posts into `documents` (permission=grey) so the
    RAG + LLM-extraction path embeds them into the ontology like any article."""
    from ..ingestion.base import Doc, save

    rows = db.query(
        """SELECT id, platform, author, url, posted_at, text, sentiment, metrics
           FROM social_posts
           WHERE company_id=%s AND text IS NOT NULL
             AND (abs(COALESCE(sentiment,0)) >= 0.5
                  OR COALESCE((metrics->>'score')::int, 0) >= 50)
           ORDER BY posted_at DESC NULLS LAST LIMIT %s""", (company_id, limit))
    n = 0
    for r in rows:
        doc = Doc(company_id=company_id, source="social", doc_type=f"{r['platform']}_post",
                  title=f"{r['platform']} @{r['author'] or '?'}", text=r["text"], url=r["url"],
                  published_at=r["posted_at"], permission="grey",
                  license_tag="social-extracted-facts-self-use",
                  meta={"sentiment": r["sentiment"], "social_id": r["id"]})
        save(doc)
        n += 1
    return n
