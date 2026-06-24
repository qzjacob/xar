"""Finnhub connector: basic financials (margins/ratios), analyst recommendation
trends, forward EPS/revenue estimates, and insider transactions. Free-tier
endpoints; premium ones simply return nothing and are skipped."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from ..config import get_settings
from ..ingestion.base import Doc, save
from ..ontology.standards import FINNHUB_METRIC_MAP, FinMetric
from ..storage import structured
from .base import get_json, log, us_ticker

_BASE = "https://finnhub.io/api/v1"
_HOST = "finnhub.io"
_SUFFIX = re.compile(r"(TTM|Annual|Quarterly|5Y|3Y|10Y|PerShare)+$")
_NEWS_LICENSE = "finnhub-news-extracted-facts-self-use"


def _as_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _save_news(rows: list, company_id: str | None, limit: int) -> int:
    n = 0
    for row in (rows or [])[:limit]:
        headline = (row.get("headline") or "").strip()
        summary = (row.get("summary") or "").strip()
        text = summary or headline
        if len(text) < 24:
            continue
        ts = row.get("datetime")
        published = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
        save(Doc(company_id=company_id, source="finnhub", doc_type="news",
                 title=headline or text[:80], text=text[:120_000], url=row.get("url"),
                 published_at=published, permission="grey", license_tag=_NEWS_LICENSE,
                 meta={"finnhub_id": row.get("id"), "category": row.get("category"),
                       "news_source": row.get("source")}))
        n += 1
    return n


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


def pull_news(company_id: str, *, since=None, until=None, limit: int = 200) -> int:
    """Company news -> documents (grey, extracted-facts self-use). Finnhub requires
    a from/to window (<=1yr); defaults to the last `daily_news_lookback_days`.
    Overlapping windows dedup on the content-hash Doc.id, so re-runs are idempotent."""
    sym = us_ticker(company_id)
    if not sym or not available():
        return 0
    today = date.today()
    frm = _as_date(since) or (today - timedelta(days=get_settings().daily_news_lookback_days))
    to = _as_date(until) or today
    js = get_json(f"{_BASE}/company-news",
                  params={"symbol": sym, "from": frm.isoformat(), "to": to.isoformat(),
                          "token": _tok()}, host=_HOST)
    n = _save_news(js, company_id, limit)
    log.info("finnhub news %s: %d docs", company_id, n)
    return n


def pull_general_news(category: str = "technology", limit: int = 50) -> int:
    """Market-level news scan (not company-scoped) -> documents for expert processing."""
    if not available():
        return 0
    js = get_json(f"{_BASE}/news", params={"category": category, "token": _tok()}, host=_HOST)
    return _save_news(js, None, limit)


def pull(company_id: str) -> dict:
    if not available():
        return {}
    out = {"fundamentals": pull_fundamentals(company_id),
           "estimates": pull_estimates(company_id),
           "ratings": pull_ratings(company_id),
           "insider": pull_insider(company_id),
           "news": pull_news(company_id)}
    log.info("finnhub %s: %s", company_id, out)
    return out
