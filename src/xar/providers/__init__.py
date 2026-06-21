"""Market-data + alternative-data provider suite.

Each provider is key-gated and degrades to a no-op when unconfigured, so the
platform runs turnkey with zero provider keys and lights up incrementally as keys
are added. Structured numbers from every provider are normalized onto the
canonical metric vocabulary (ontology/standards.py) and coexist by `source`.

    structured fundamentals/estimates/prices : fmp, finnhub, polygon, yahoo, wind
    ratings / insider                        : fmp, finnhub
    prediction markets                       : polymarket (public)
    social                                   : twitter (X), reddit
"""
from __future__ import annotations

from ..ingestion.registry import COMPANIES
from ..logging import get_logger
from . import (aifinmarket, arxiv, finnhub, fmp, journals, polygon, polymarket, reddit,
               twitter, wind, yahoo)

log = get_logger("xar.providers")

# Structured market-data providers in preference order (all that are available run).
_MARKET = [fmp, finnhub, polygon, yahoo, wind, aifinmarket]


def status() -> dict[str, bool]:
    """Which providers are configured/usable right now."""
    return {
        "fmp": fmp.available(), "finnhub": finnhub.available(),
        "polygon": polygon.available(), "yahoo": yahoo.available(),
        "wind": wind.available(), "polymarket": polymarket.available(),
        "twitter": twitter.available(), "reddit": reddit.available(),
        "aifinmarket": aifinmarket.available(), "arxiv": arxiv.available(),
        "journals": journals.available(),
    }


def pull_company(company_id: str, *, with_social: bool = True) -> dict:
    """Pull all available structured + social data for one company, then derive
    KG signal events from it."""
    from ..kg import signals

    out: dict = {}
    for prov in _MARKET:
        if prov.available():
            try:
                out[prov.__name__.split(".")[-1]] = prov.pull(company_id)
            except Exception as e:  # noqa: BLE001
                log.warning("provider %s failed for %s: %s", prov.__name__, company_id, e)
    if with_social and twitter.available():
        out["twitter"] = {"posts": twitter.pull_company(company_id)}
    # structured -> ontology: derive catalyst signals + mirror social into RAG
    out["signals"] = signals.derive_for_company(company_id)
    return out


def pull_basket(company_ids: list[str] | None = None, *, with_social: bool = True) -> dict:
    from ..ingestion import seed_companies

    seed_companies()
    ids = company_ids or [c["id"] for c in COMPANIES]
    result = {cid: pull_company(cid, with_social=with_social) for cid in ids}
    # basket-level alt-data
    result["_prediction_markets"] = polymarket.pull()
    if reddit.available():
        result["_reddit_posts"] = reddit.pull_basket(ids)
    # turn fresh prediction markets + social into signals/RAG
    from ..kg import signals

    result["_market_signals"] = signals.derive_market_signals()
    for cid in ids:
        signals.mirror_social(cid)
    return result
