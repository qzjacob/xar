"""Financial Modeling Prep connector: statement line items (income/balance/cash
flow) normalized onto the canonical metric vocabulary, forward analyst estimates,
price-target consensus, and daily OHLCV. Degrades to nothing without a key."""
from __future__ import annotations

from datetime import date

from ..config import get_settings
from ..ingestion.base import Doc, save
from ..ontology.standards import FMP_MAP, RATIO_METRICS, FinMetric
from ..storage import structured
from .base import get_json, log, us_ticker

_BASE = "https://financialmodelingprep.com/api"
_HOST = "financialmodelingprep.com"
_STATEMENTS = ("income-statement", "balance-sheet-statement", "cash-flow-statement")
_NEWS_LICENSE = "fmp-news-extracted-facts-self-use"


def available() -> bool:
    return bool(get_settings().fmp_api_key)


def _key() -> str:
    return get_settings().fmp_api_key


def _period_label(row: dict) -> str:
    p = row.get("period") or ""
    yr = row.get("calendarYear") or (str(row.get("date", ""))[:4])
    return f"{p}-{yr}" if p and p != "FY" else f"FY{yr}"


def pull_fundamentals(company_id: str, limit: int = 8) -> int:
    sym = us_ticker(company_id)
    if not sym:
        return 0
    n = 0
    for stmt in _STATEMENTS:
        js = get_json(f"{_BASE}/v3/{stmt}/{sym}",
                      params={"period": "quarter", "limit": limit, "apikey": _key()}, host=_HOST)
        for row in js or []:
            label, pend = _period_label(row), row.get("date")
            for field, canon in FMP_MAP.items():
                if field not in row or row[field] is None:
                    continue
                unit = "ratio" if canon in RATIO_METRICS else "USD"
                structured.upsert_fundamental(company_id, canon, row[field], period=label,
                                              period_end=pend, freq="quarter", unit=unit,
                                              source="fmp")
                n += 1
    return n


def pull_estimates(company_id: str, limit: int = 8) -> int:
    sym = us_ticker(company_id)
    if not sym:
        return 0
    js = get_json(f"{_BASE}/v3/analyst-estimates/{sym}",
                  params={"period": "quarter", "limit": limit, "apikey": _key()}, host=_HOST)
    today = date.today()
    n = 0
    for row in js or []:
        label, pend = f"Q-{str(row.get('date',''))[:7]}", row.get("date")
        for metric, avg, hi, lo in (
            (FinMetric.REVENUE.value, "estimatedRevenueAvg", "estimatedRevenueHigh", "estimatedRevenueLow"),
            (FinMetric.EPS_DILUTED.value, "estimatedEpsAvg", "estimatedEpsHigh", "estimatedEpsLow"),
            (FinMetric.EBITDA.value, "estimatedEbitdaAvg", "estimatedEbitdaHigh", "estimatedEbitdaLow"),
        ):
            structured.upsert_estimate(
                company_id, metric, row.get(avg), today, period=label, period_end=pend,
                high=row.get(hi), low=row.get(lo), n_analysts=row.get("numberAnalystEstimatedRevenue"),
                unit="ratio" if metric == FinMetric.EPS_DILUTED.value else "USD", source="fmp")
            n += 1
    return n


def pull_ratings(company_id: str) -> int:
    sym = us_ticker(company_id)
    if not sym:
        return 0
    js = get_json(f"{_BASE}/v4/price-target-consensus", params={"symbol": sym, "apikey": _key()},
                  host=_HOST)
    row = (js or [{}])[0] if isinstance(js, list) else (js or {})
    if not row.get("targetConsensus"):
        return 0
    structured.upsert_rating(company_id, date.today(), pt_mean=row.get("targetConsensus"),
                             pt_high=row.get("targetHigh"), pt_low=row.get("targetLow"),
                             source="fmp")
    return 1


def pull_prices(company_id: str, days: int = 400) -> int:
    sym = us_ticker(company_id)
    if not sym:
        return 0
    js = get_json(f"{_BASE}/v3/historical-price-full/{sym}",
                  params={"timeseries": days, "apikey": _key()}, host=_HOST)
    hist = (js or {}).get("historical", [])
    bars = [{"d": h.get("date"), "open": h.get("open"), "high": h.get("high"),
             "low": h.get("low"), "close": h.get("close"), "volume": h.get("volume")}
            for h in hist]
    return structured.upsert_prices(company_id, sym, bars, source="fmp")


def pull_calendar(company_id: str, limit: int = 12) -> int:
    """Forward earnings dates -> event_calendar (the 'what's coming' dimension)."""
    sym = us_ticker(company_id)
    if not sym:
        return 0
    js = get_json(f"{_BASE}/v3/historical/earning_calendar/{sym}",
                  params={"apikey": _key()}, host=_HOST)
    today = date.today()
    n = 0
    for row in (js or []):
        d = row.get("date")
        if not d:
            continue
        try:
            dd = date.fromisoformat(str(d)[:10])
        except ValueError:
            continue
        if dd < today:
            continue
        meta = {"epsEstimated": row.get("epsEstimated"), "revenueEstimated": row.get("revenueEstimated")}
        if structured.upsert_calendar(company_id, "earnings", dd, title=f"{sym} earnings",
                                      importance=3, source="fmp", meta=meta):
            n += 1
        if n >= limit:
            break
    return n


def pull_news(company_id: str, *, limit: int = 20) -> int:
    """Company news -> documents (grey, extracted-facts self-use). Per-company only
    (the registry is the relevance filter). Idempotent via the content-hash Doc.id."""
    sym = us_ticker(company_id)
    if not sym or not available():
        return 0
    js = get_json(f"{_BASE}/v3/stock_news",
                  params={"tickers": sym, "limit": limit, "apikey": _key()}, host=_HOST)
    n = 0
    for row in (js or [])[:limit]:
        title = (row.get("title") or "").strip()
        body = (row.get("text") or "").strip() or title
        if len(body) < 24:
            continue
        save(Doc(company_id=company_id, source="fmp", doc_type="news",
                 title=title or body[:80], text=body[:120_000], url=row.get("url"),
                 published_at=row.get("publishedDate"), permission="grey",
                 license_tag=_NEWS_LICENSE,
                 meta={"news_source": row.get("site"), "symbol": row.get("symbol")}))
        n += 1
    log.info("fmp news %s: %d docs", company_id, n)
    return n


def pull(company_id: str) -> dict:
    if not available():
        return {}
    out = {"fundamentals": pull_fundamentals(company_id),
           "estimates": pull_estimates(company_id),
           "ratings": pull_ratings(company_id),
           "prices": pull_prices(company_id),
           "calendar": pull_calendar(company_id),
           "news": pull_news(company_id)}
    log.info("fmp %s: %s", company_id, out)
    return out
