"""Yahoo Finance connector via yfinance (no API key; optional dependency).
Covers global tickers — including the CN A-share names (300308.SZ ...) the other
providers can't reach — so it's the universal fallback for prices + a snapshot of
fundamentals. Install with: pip install '.[market]'."""
from __future__ import annotations

from ..ingestion.registry import company_by_id
from ..ontology.standards import YAHOO_INFO_MAP
from ..storage import structured
from .base import log


def _yf():
    try:
        import yfinance as yf

        return yf
    except Exception:
        return None


def available() -> bool:
    return _yf() is not None


def _ticker(company_id: str) -> str | None:
    c = company_by_id(company_id)
    if not c or not c.get("tickers"):
        return None
    # prefer US ticker; else the first (global) listing yfinance understands
    return next((t for t in c["tickers"] if "." not in t), c["tickers"][0])


def pull_prices(company_id: str, period: str = "2y") -> int:
    yf = _yf()
    sym = _ticker(company_id)
    if not yf or not sym:
        return 0
    try:
        df = yf.Ticker(sym).history(period=period, auto_adjust=True)
    except Exception as e:  # noqa: BLE001
        log.warning("yahoo history %s: %s", sym, e)
        return 0
    bars = [{"d": idx.date(), "open": r.get("Open"), "high": r.get("High"),
             "low": r.get("Low"), "close": r.get("Close"), "volume": r.get("Volume")}
            for idx, r in df.iterrows()]
    return structured.upsert_prices(company_id, sym, bars, source="yahoo")


def pull_fundamentals(company_id: str) -> int:
    yf = _yf()
    sym = _ticker(company_id)
    if not yf or not sym:
        return 0
    try:
        info = yf.Ticker(sym).info or {}
    except Exception as e:  # noqa: BLE001
        log.warning("yahoo info %s: %s", sym, e)
        return 0
    n = 0
    for field, canon in YAHOO_INFO_MAP.items():
        val = info.get(field)
        if not isinstance(val, (int, float)):
            continue
        unit = "ratio" if "Margin" in field or field in (
            "returnOnEquity", "trailingPE", "priceToSalesTrailing12Months",
            "revenueGrowth", "earningsGrowth") else "USD"
        structured.upsert_fundamental(company_id, canon, float(val), period="TTM",
                                      freq="ttm", unit=unit, source="yahoo")
        n += 1
    return n


def pull(company_id: str) -> dict:
    if not available():
        return {}
    out = {"prices": pull_prices(company_id), "fundamentals": pull_fundamentals(company_id)}
    log.info("yahoo %s: %s", company_id, out)
    return out
