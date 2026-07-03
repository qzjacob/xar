"""Yahoo Finance connector via yfinance (no API key; optional dependency).

Covers global tickers — including the CN/TW/JP/KR/EU names (300308.SZ, 2330.TW,
6758.T ...) the keyed US providers can't reach — so it's the universal deep-pull:

    prices        daily OHLCV
    fundamentals  .info TTM snapshot + short interest / float (CORE-pack keys)
    analyst       .recommendations / .analyst_price_targets -> analyst_ratings;
                  .earnings_estimate / .revenue_estimate    -> estimates
    calendar      .actions (dividends/splits) + get_earnings_dates() -> event_calendar
    statements    quarterly income/balance/cashflow -> fundamentals with real period_end

Install with: pip install '.[market]'. Every section is independently non-fatal
(log + continue) so a basket run over ~1000 names never dies on one ticker.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ..ingestion.registry import company_by_id
from ..ontology.standards import YAHOO_INFO_MAP, FinMetric
from ..storage import structured
from .base import log

# .info short-interest / float fields -> (canonical CORE-pack key, unit).
_SHORT_INFO_MAP: dict[str, tuple[str, str]] = {
    "sharesShort": ("short_interest_shares", "count"),
    "shortRatio": ("short_ratio", "days"),
    "shortPercentOfFloat": ("short_pct_float", "ratio"),
    "floatShares": ("float_shares", "count"),
}

# Quarterly statement frames -> (canonical key, yfinance line-item candidates).
# Each canonical key is sourced from exactly one frame so values never double-write.
_QUARTERLY_STATEMENTS: tuple[tuple[str, tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    ("quarterly_income_stmt", (
        (FinMetric.REVENUE.value, ("Total Revenue", "Operating Revenue")),
        (FinMetric.GROSS_PROFIT.value, ("Gross Profit",)),
        (FinMetric.OPERATING_INCOME.value, ("Operating Income",)),
        (FinMetric.NET_INCOME.value, ("Net Income",)),
    )),
    ("quarterly_balance_sheet", (
        (FinMetric.CASH.value, ("Cash And Cash Equivalents",
                                "Cash Cash Equivalents And Short Term Investments")),
        (FinMetric.TOTAL_DEBT.value, ("Total Debt",)),
    )),
    ("quarterly_cashflow", (
        (FinMetric.CAPEX.value, ("Capital Expenditure",)),
        (FinMetric.FREE_CASH_FLOW.value, ("Free Cash Flow",)),
    )),
)


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


def _handle(company_id: str, tk=None):
    """(symbol, yf.Ticker) — reuses a caller-supplied ticker so batch pulls hit
    yfinance's per-Ticker cache instead of re-fetching per section."""
    yf = _yf()
    sym = _ticker(company_id)
    if not yf or not sym:
        return None, None
    return sym, (tk if tk is not None else yf.Ticker(sym))


def _num(v) -> float | None:
    """float(v) with NaN/None/garbage scrubbed to None (JSON- and SQL-safe)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _int(v) -> int | None:
    f = _num(v)
    return None if f is None else int(f)


def _as_date(v) -> date | None:
    """date from a pandas Timestamp / datetime / ISO string; None for NaT/garbage."""
    try:
        if v is None or v != v:  # NaN / NaT
            return None
        return v.date() if hasattr(v, "date") else date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _epoch_iso(ts) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _month_anchor(months_back: int, today: date) -> date:
    m = today.year * 12 + today.month - 1 - months_back
    return date(m // 12, m % 12 + 1, 1)


# --- prices ------------------------------------------------------------------
def pull_prices(company_id: str, period: str = "2y", *, tk=None) -> int:
    sym, tk = _handle(company_id, tk)
    if not tk:
        return 0
    try:
        df = tk.history(period=period, auto_adjust=True)
    except Exception as e:  # noqa: BLE001
        log.warning("yahoo history %s: %s", sym, e)
        return 0
    bars = [{"d": idx.date(), "open": r.get("Open"), "high": r.get("High"),
             "low": r.get("Low"), "close": r.get("Close"), "volume": r.get("Volume")}
            for idx, r in df.iterrows()]
    return structured.upsert_prices(company_id, sym, bars, source="yahoo")


# --- fundamentals: .info TTM snapshot + short interest / float ----------------
def pull_fundamentals(company_id: str, *, tk=None) -> int:
    sym, tk = _handle(company_id, tk)
    if not tk:
        return 0
    try:
        info = tk.info or {}
    except Exception as e:  # noqa: BLE001
        log.warning("yahoo info %s: %s", sym, e)
        return 0
    n = 0
    for field, canon in YAHOO_INFO_MAP.items():
        val = info.get(field)
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        unit = "ratio" if "Margin" in field or field in (
            "returnOnEquity", "trailingPE", "priceToSalesTrailing12Months",
            "revenueGrowth", "earningsGrowth") else "USD"
        structured.upsert_fundamental(company_id, canon, float(val), period="TTM",
                                      freq="ttm", unit=unit, source="yahoo")
        n += 1
    # short interest + float: point-in-time snapshot (canonical keys live in the
    # CORE metric pack); prior-month shares ride along in meta for the delta.
    short_meta = {"date_short_interest": _epoch_iso(info.get("dateShortInterest")),
                  "prior_month_shares_short": _num(info.get("sharesShortPriorMonth"))}
    for field, (canon, unit) in _SHORT_INFO_MAP.items():
        v = _num(info.get(field))
        if v is None:
            continue
        meta = short_meta if canon == "short_interest_shares" else None
        structured.upsert_fundamental(company_id, canon, v, period="latest",
                                      freq="snapshot", unit=unit, source="yahoo",
                                      meta=meta)
        n += 1
    return n


# --- analyst layer: ratings + price targets + forward estimates ---------------
def pull_analyst(company_id: str, *, tk=None) -> int:
    """.recommendations / .analyst_price_targets -> analyst_ratings; the current
    month merges counts with price targets at as_of=today (snapshot, like finnhub);
    prior months anchor to the 1st of their month so re-pulls dedup.
    .earnings_estimate / .revenue_estimate -> estimates (relative periods 0q/+1q/0y/+1y)."""
    sym, tk = _handle(company_id, tk)
    if not tk:
        return 0
    today = date.today()
    n = 0
    pt: dict = {}
    try:
        pt = {k: _num(v) for k, v in dict(tk.analyst_price_targets or {}).items()}
    except Exception as e:  # noqa: BLE001
        log.warning("yahoo price targets %s: %s", sym, e)
    rows: list[dict] = []
    try:
        rec = tk.recommendations
        if rec is not None and not rec.empty:
            rows = rec.to_dict("records")
    except Exception as e:  # noqa: BLE001
        log.warning("yahoo recommendations %s: %s", sym, e)
    pt_meta = {"pt_current": pt.get("current"), "pt_median": pt.get("median")}
    wrote_current = False
    for row in rows[:6]:
        try:
            off = abs(int(str(row.get("period", "")).replace("m", "")))
        except ValueError:
            continue
        counts = {k: _int(row.get(raw)) for k, raw in
                  (("strong_buy", "strongBuy"), ("buy", "buy"), ("hold", "hold"),
                   ("sell", "sell"), ("strong_sell", "strongSell"))}
        if off == 0:
            structured.upsert_rating(company_id, today, **counts, pt_mean=pt.get("mean"),
                                     pt_high=pt.get("high"), pt_low=pt.get("low"),
                                     source="yahoo", meta=pt_meta)
            wrote_current = True
        elif any(v is not None for v in counts.values()):
            structured.upsert_rating(company_id, _month_anchor(off, today), **counts,
                                     source="yahoo")
        else:
            continue
        n += 1
    if not wrote_current and any(pt.get(k) is not None for k in ("mean", "high", "low")):
        structured.upsert_rating(company_id, today, pt_mean=pt.get("mean"),
                                 pt_high=pt.get("high"), pt_low=pt.get("low"),
                                 source="yahoo", meta=pt_meta)
        n += 1
    for prop, metric, unit in (("earnings_estimate", FinMetric.EPS_DILUTED.value, "ratio"),
                               ("revenue_estimate", FinMetric.REVENUE.value, "USD")):
        try:
            df = getattr(tk, prop)
        except Exception as e:  # noqa: BLE001
            log.warning("yahoo %s %s: %s", prop, sym, e)
            continue
        if df is None or getattr(df, "empty", True):
            continue
        for idx, row in df.iterrows():
            v = _num(row.get("avg"))
            if v is None:
                continue
            structured.upsert_estimate(
                company_id, metric, v, today, period=str(idx),
                high=_num(row.get("high")), low=_num(row.get("low")),
                n_analysts=_int(row.get("numberOfAnalysts")), unit=unit, source="yahoo",
                meta={"growth": _num(row.get("growth")), "relative_period": True})
            n += 1
    return n


# --- corporate actions + earnings dates -> event_calendar ---------------------
def pull_calendar(company_id: str, *, tk=None, lookback_days: int = 5 * 365,
                  earnings_limit: int = 12) -> int:
    """Dividends/splits (.actions) and earnings dates -> event_calendar rows,
    deduped on company|type|date|title (titles are per-type stable). Past events
    land as status='occurred', future ones as 'scheduled'."""
    sym, tk = _handle(company_id, tk)
    if not tk:
        return 0
    today = date.today()
    n = 0
    try:
        actions = tk.actions
    except Exception as e:  # noqa: BLE001
        log.warning("yahoo actions %s: %s", sym, e)
        actions = None
    if actions is not None and not getattr(actions, "empty", True):
        cutoff = today - timedelta(days=lookback_days)
        for idx, row in actions.iterrows():
            d = _as_date(idx)
            if d is None or d < cutoff:
                continue
            status = "occurred" if d <= today else "scheduled"
            div = _num(row.get("Dividends"))
            if div:
                n += int(structured.upsert_calendar(
                    company_id, "dividend", d, title=f"{sym} dividend", status=status,
                    importance=1, source="yahoo", meta={"amount": div}))
            split = _num(row.get("Stock Splits"))
            if split:
                n += int(structured.upsert_calendar(
                    company_id, "split", d, title=f"{sym} split", status=status,
                    importance=2, source="yahoo", meta={"ratio": split}))
    try:
        ed = tk.get_earnings_dates(limit=earnings_limit)
    except Exception as e:  # noqa: BLE001
        log.warning("yahoo earnings_dates %s: %s", sym, e)
        ed = None
    if ed is not None and not getattr(ed, "empty", True):
        for idx, row in ed.iterrows():
            d = _as_date(idx)
            if d is None:
                continue
            status = "occurred" if d < today else "scheduled"
            meta = {"eps_estimate": _num(row.get("EPS Estimate")),
                    "reported_eps": _num(row.get("Reported EPS")),
                    "surprise_pct": _num(row.get("Surprise(%)"))}
            n += int(structured.upsert_calendar(
                company_id, "earnings", d, title=f"{sym} earnings", status=status,
                importance=3, source="yahoo", meta=meta))
    return n


# --- quarterly statements -> fundamentals time series with real period_end ----
def pull_statements(company_id: str, *, tk=None) -> int:
    """Quarterly income/balance/cashflow -> fundamentals rows with real period_end
    for the compact canonical set (revenue, net_income, gross_profit,
    operating_income, capex, free_cash_flow, cash, total_debt). Capex keeps
    yfinance's sign convention (negative = cash outflow, same as FMP)."""
    sym, tk = _handle(company_id, tk)
    if not tk:
        return 0
    n = 0
    for attr, lines in _QUARTERLY_STATEMENTS:
        try:
            df = getattr(tk, attr)
        except Exception as e:  # noqa: BLE001
            log.warning("yahoo %s %s: %s", attr, sym, e)
            continue
        if df is None or getattr(df, "empty", True):
            continue
        for canon, candidates in lines:
            hit = next((c for c in candidates if c in df.index), None)
            if hit is None:
                continue
            for col, val in df.loc[hit].items():
                v, pend = _num(val), _as_date(col)
                if v is None or pend is None:
                    continue
                label = f"Q{(pend.month - 1) // 3 + 1}-{pend.year}"
                structured.upsert_fundamental(
                    company_id, canon, v, period=label, period_end=pend,
                    freq="quarter", unit="USD", source="yahoo",
                    meta={"line_item": hit})
                n += 1
    return n


# --- orchestration -------------------------------------------------------------
def pull(company_id: str) -> dict:
    """Deep pull for one company on a single shared yf.Ticker. Every section is
    independently non-fatal so a 947-name basket run survives per-ticker failures."""
    sym, tk = _handle(company_id)
    if not tk:
        return {}
    out: dict = {}
    for name, fn in (("prices", pull_prices), ("fundamentals", pull_fundamentals),
                     ("analyst", pull_analyst), ("calendar", pull_calendar),
                     ("statements", pull_statements)):
        try:
            out[name] = fn(company_id, tk=tk)
        except Exception as e:  # noqa: BLE001
            log.warning("yahoo %s %s: %s", name, sym, e)
            out[name] = 0
    log.info("yahoo %s: %s", company_id, out)
    return out
