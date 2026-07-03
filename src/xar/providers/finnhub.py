"""Finnhub connector: basic financials (margins/ratios), analyst recommendation
trends, forward EPS/revenue estimates, insider transactions, company news, and the
forward earnings calendar (event_calendar). Free-tier endpoints; premium ones simply
return nothing and are skipped.

Basket-wide sweeps are rate-limit aware: every HTTP call goes through `_paced_get`,
which enforces a finnhub-local minimum interval (~1 req/s, well under the free tier's
60 req/min) on top of the shared per-host polite() delay. Non-US names never issue a
call at all (`us_ticker` gate), so a full-universe loop skips them instantly."""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta, timezone

from ..config import get_settings
from ..ingestion.base import Doc, save
from ..ingestion.registry import COMPANIES
from ..ontology.standards import FINNHUB_METRIC_MAP, FinMetric
from ..storage import structured
from .base import get_json, log, us_ticker

_BASE = "https://finnhub.io/api/v1"
_HOST = "finnhub.io"
_SUFFIX = re.compile(r"(TTM|Annual|Quarterly|5Y|3Y|10Y|PerShare)+$")
_NEWS_LICENSE = "finnhub-news-extracted-facts-self-use"

# Free tier: 60 requests/min. polite() already spaces same-host calls by
# crawl_delay_seconds (default 2s); this local floor keeps a basket-wide sweep
# under the cap even if that global delay is tuned down.
_RATE_MIN_INTERVAL = 1.1
_last_req = 0.0


def _paced_get(path: str, params: dict):
    """get_json against the Finnhub API with a 60/min-safe minimum call interval."""
    global _last_req
    wait = _RATE_MIN_INTERVAL - (time.monotonic() - _last_req)
    if wait > 0:
        time.sleep(wait)
    _last_req = time.monotonic()
    return get_json(f"{_BASE}{path}", params=params, host=_HOST)


def _as_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


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
    js = _paced_get("/stock/metric", {"symbol": sym, "metric": "all", "token": _tok()})
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
        js = _paced_get(f"/stock/{ep}", {"symbol": sym, "freq": "quarterly", "token": _tok()})
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
    js = _paced_get("/stock/recommendation", {"symbol": sym, "token": _tok()})
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
    js = _paced_get("/stock/insider-transactions", {"symbol": sym, "token": _tok()})
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
    Overlapping windows dedup on the content-hash Doc.id, so re-runs are idempotent.
    Non-US names (no US ticker) return 0 without an HTTP call."""
    sym = us_ticker(company_id)
    if not sym or not available():
        return 0
    today = date.today()
    frm = _as_date(since) or (today - timedelta(days=get_settings().daily_news_lookback_days))
    to = _as_date(until) or today
    js = _paced_get("/company-news", {"symbol": sym, "from": frm.isoformat(),
                                      "to": to.isoformat(), "token": _tok()})
    n = _save_news(js, company_id, limit)
    log.info("finnhub news %s: %d docs", company_id, n)
    return n


def pull_news_basket(company_ids: list[str] | None = None, *, since=None) -> dict:
    """Company news for EVERY US-tickered company in the basket (default: the whole
    registry universe). Non-US names skip instantly; each US call is spaced by the
    60/min-safe pacer, so a full sweep is slow-but-safe on the free tier."""
    if not available():
        return {}
    ids = company_ids or [c["id"] for c in COMPANIES]
    stats = {"companies": 0, "docs": 0, "skipped_non_us": 0}
    for cid in ids:
        if not us_ticker(cid):
            stats["skipped_non_us"] += 1
            continue
        try:
            stats["docs"] += pull_news(cid, since=since)
            stats["companies"] += 1
        except Exception as e:  # noqa: BLE001 — one bad name must not sink the sweep
            log.warning("finnhub news basket %s: %s", cid, e)
        if stats["companies"] and stats["companies"] % 25 == 0:
            log.info("finnhub news basket: %(companies)d companies, %(docs)d docs", stats)
    log.info("finnhub news basket done: %s", stats)
    return stats


def pull_general_news(category: str = "technology", limit: int = 50) -> int:
    """Market-level news scan (not company-scoped) -> documents for expert processing."""
    if not available():
        return 0
    js = _paced_get("/news", {"category": category, "token": _tok()})
    return _save_news(js, None, limit)


def pull_calendar(company_id: str, *, days_ahead: int = 180, limit: int = 12) -> int:
    """Forward earnings dates via GET /calendar/earnings (free tier) -> event_calendar.
    Dedup/update handled by structured.upsert_calendar (company|type|date|title key).
    Non-US names return 0 without an HTTP call. Returns the number of NEW events."""
    sym = us_ticker(company_id)
    if not sym or not available():
        return 0
    today = date.today()
    js = _paced_get("/calendar/earnings",
                    {"symbol": sym, "from": today.isoformat(),
                     "to": (today + timedelta(days=days_ahead)).isoformat(), "token": _tok()})
    n = 0
    for row in (js or {}).get("earningsCalendar") or []:
        dd = _as_date(row.get("date"))
        if not dd or dd < today:
            continue
        meta = {k: row.get(k)
                for k in ("epsEstimate", "revenueEstimate", "hour", "quarter", "year")}
        if structured.upsert_calendar(company_id, "earnings", dd, title=f"{sym} earnings",
                                      importance=3, source="finnhub", meta=meta):
            n += 1
        if n >= limit:
            break
    return n


def pull_calendar_basket(company_ids: list[str] | None = None, *, days_ahead: int = 180) -> dict:
    """Forward earnings calendar for every US-tickered company in the basket."""
    if not available():
        return {}
    ids = company_ids or [c["id"] for c in COMPANIES]
    stats = {"companies": 0, "events": 0, "skipped_non_us": 0}
    for cid in ids:
        if not us_ticker(cid):
            stats["skipped_non_us"] += 1
            continue
        try:
            stats["events"] += pull_calendar(cid, days_ahead=days_ahead)
            stats["companies"] += 1
        except Exception as e:  # noqa: BLE001
            log.warning("finnhub calendar basket %s: %s", cid, e)
        if stats["companies"] and stats["companies"] % 50 == 0:
            log.info("finnhub calendar basket: %(companies)d companies, %(events)d events", stats)
    log.info("finnhub calendar basket done: %s", stats)
    return stats


def pull(company_id: str) -> dict:
    if not available():
        return {}
    if not us_ticker(company_id):  # non-US: every endpoint would no-op — skip fast
        return {}
    out = {"fundamentals": pull_fundamentals(company_id),
           "estimates": pull_estimates(company_id),
           "ratings": pull_ratings(company_id),
           "insider": pull_insider(company_id),
           "calendar": pull_calendar(company_id),
           "news": pull_news(company_id)}
    log.info("finnhub %s: %s", company_id, out)
    return out
