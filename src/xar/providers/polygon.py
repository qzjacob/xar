"""Polygon.io connector: daily aggregates (OHLCV, the deep history Polygon is
known for) and the vX reference-financials feed normalized onto canonical
metrics. No key -> skipped."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ..config import get_settings
from ..ontology.standards import FinMetric
from ..storage import structured
from .base import get_json, log, us_ticker

_BASE = "https://api.polygon.io"
_HOST = "api.polygon.io"

# Polygon vX financial concept -> canonical metric (statement-agnostic lookup).
_CONCEPTS = {
    "revenues": FinMetric.REVENUE.value,
    "cost_of_revenue": FinMetric.COST_OF_REVENUE.value,
    "gross_profit": FinMetric.GROSS_PROFIT.value,
    "operating_income_loss": FinMetric.OPERATING_INCOME.value,
    "net_income_loss": FinMetric.NET_INCOME.value,
    "diluted_earnings_per_share": FinMetric.EPS_DILUTED.value,
    "research_and_development": FinMetric.RD_EXPENSE.value,
    "assets": FinMetric.TOTAL_ASSETS.value,
    "liabilities": FinMetric.TOTAL_LIABILITIES.value,
    "equity": FinMetric.TOTAL_EQUITY.value,
    "net_cash_flow_from_operating_activities": FinMetric.OPERATING_CASH_FLOW.value,
}


def available() -> bool:
    return bool(get_settings().polygon_api_key)


def _key() -> str:
    return get_settings().polygon_api_key


def pull_prices(company_id: str, days: int = 400) -> int:
    sym = us_ticker(company_id)
    if not sym:
        return 0
    end = date.today()
    start = end - timedelta(days=days)
    js = get_json(f"{_BASE}/v2/aggs/ticker/{sym}/range/1/day/{start}/{end}",
                  params={"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": _key()},
                  host=_HOST)
    bars = []
    for r in (js or {}).get("results", []):
        d = datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc).date()
        bars.append({"d": d, "open": r.get("o"), "high": r.get("h"), "low": r.get("l"),
                     "close": r.get("c"), "volume": r.get("v")})
    return structured.upsert_prices(company_id, sym, bars, source="polygon")


def pull_fundamentals(company_id: str, limit: int = 8) -> int:
    sym = us_ticker(company_id)
    if not sym:
        return 0
    js = get_json(f"{_BASE}/vX/reference/financials",
                  params={"ticker": sym, "timeframe": "quarterly", "limit": limit,
                          "apiKey": _key()}, host=_HOST)
    n = 0
    for res in (js or {}).get("results", []):
        period = f"{res.get('fiscal_period','')}-{res.get('fiscal_year','')}"
        pend = res.get("end_date")
        for _stmt, items in (res.get("financials") or {}).items():
            if not isinstance(items, dict):
                continue
            for concept, payload in items.items():
                canon = _CONCEPTS.get(concept)
                if not canon or not isinstance(payload, dict):
                    continue
                val = payload.get("value")
                if val is None:
                    continue
                unit = "ratio" if canon == FinMetric.EPS_DILUTED.value else "USD"
                structured.upsert_fundamental(company_id, canon, val, period=period,
                                              period_end=pend, freq="quarter", unit=unit,
                                              source="polygon")
                n += 1
    return n


def pull(company_id: str) -> dict:
    if not available():
        return {}
    out = {"prices": pull_prices(company_id), "fundamentals": pull_fundamentals(company_id)}
    log.info("polygon %s: %s", company_id, out)
    return out
