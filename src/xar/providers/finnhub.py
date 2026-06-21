"""Finnhub connector: basic financials (margins/ratios), analyst recommendation
trends, forward EPS/revenue estimates, and insider transactions. Free-tier
endpoints; premium ones simply return nothing and are skipped."""
from __future__ import annotations

import re
from datetime import date

from ..config import get_settings
from ..ontology.standards import FINNHUB_METRIC_MAP, FinMetric
from ..storage import structured
from .base import get_json, log, us_ticker

_BASE = "https://finnhub.io/api/v1"
_HOST = "finnhub.io"
_SUFFIX = re.compile(r"(TTM|Annual|Quarterly|5Y|3Y|10Y|PerShare)+$")


def available() -> bool:
    return bool(get_settings().finnhub_api_key)


def _tok() -> str:
    return get_settings().finnhub_api_key


def pull_fundamentals(company_id: str) -> int:
    sym = us_ticker(company_id)
    if not sym:
        return 0
    js = get_json(f"{_BASE}/stock/metric", params={"symbol": sym, "metric": "all", "token": _tok()},
                  host=_HOST)
    metric = (js or {}).get("metric") or {}
    n = 0
    for raw, val in metric.items():
        if not isinstance(val, (int, float)):
            continue
        base_key = _SUFFIX.sub("", raw)
        canon = FINNHUB_METRIC_MAP.get(base_key)
        if not canon:
            continue
        unit = "ratio" if "Margin" in raw or "Ratio" in raw else "x"
        structured.upsert_fundamental(company_id, canon, float(val), period="TTM",
                                      freq="ttm", unit=unit, source="finnhub",
                                      meta={"raw_key": raw})
        n += 1
    return n


def pull_estimates(company_id: str) -> int:
    sym = us_ticker(company_id)
    if not sym:
        return 0
    today = date.today()
    n = 0
    for ep, metric, avg_key in (("eps-estimate", FinMetric.EPS_DILUTED.value, "epsAvg"),
                                ("revenue-estimate", FinMetric.REVENUE.value, "revenueAvg")):
        js = get_json(f"{_BASE}/stock/{ep}", params={"symbol": sym, "freq": "quarterly",
                      "token": _tok()}, host=_HOST)
        for row in (js or {}).get("data", [])[:8]:
            structured.upsert_estimate(
                company_id, metric, row.get(avg_key), today, period=row.get("period"),
                high=row.get(avg_key.replace("Avg", "High")),
                low=row.get(avg_key.replace("Avg", "Low")),
                n_analysts=row.get("numberAnalysts"),
                unit="ratio" if metric == FinMetric.EPS_DILUTED.value else "USD",
                source="finnhub")
            n += 1
    return n


def pull_ratings(company_id: str) -> int:
    sym = us_ticker(company_id)
    if not sym:
        return 0
    js = get_json(f"{_BASE}/stock/recommendation", params={"symbol": sym, "token": _tok()},
                  host=_HOST)
    n = 0
    for row in (js or [])[:6]:
        structured.upsert_rating(
            company_id, row.get("period"), strong_buy=row.get("strongBuy"),
            buy=row.get("buy"), hold=row.get("hold"), sell=row.get("sell"),
            strong_sell=row.get("strongSell"), source="finnhub")
        n += 1
    return n


def pull_insider(company_id: str) -> int:
    sym = us_ticker(company_id)
    if not sym:
        return 0
    js = get_json(f"{_BASE}/stock/insider-transactions", params={"symbol": sym, "token": _tok()},
                  host=_HOST)
    n = 0
    for row in (js or {}).get("data", [])[:60]:
        code = (row.get("transactionCode") or "").upper()
        txn = "buy" if code in ("P", "A") else "sell" if code in ("S", "D") else "other"
        shares = row.get("share") or row.get("change")
        price = row.get("transactionPrice")
        added = structured.upsert_insider(
            company_id, insider=row.get("name"), txn_date=row.get("transactionDate"),
            txn_type=txn, shares=shares, price=price,
            value=(shares or 0) * (price or 0) if shares and price else None,
            source="finnhub", meta={"code": code})
        n += int(added)
    return n


def pull(company_id: str) -> dict:
    if not available():
        return {}
    out = {"fundamentals": pull_fundamentals(company_id),
           "estimates": pull_estimates(company_id),
           "ratings": pull_ratings(company_id),
           "insider": pull_insider(company_id)}
    log.info("finnhub %s: %s", company_id, out)
    return out
