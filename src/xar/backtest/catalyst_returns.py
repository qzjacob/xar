"""Catalyst -> forward-return backtest. Quantifies signal efficacy: for each
dated catalyst event, measure the company's forward return over N trading days
from the EVENT DAY, then aggregate by event_type x polarity.

Methodology notes (review §3.2):
  - Base price is the close ON/AFTER the event date (event-study t0), NOT a price
    days before the event — so the figure is a genuine forward return, not pre-event
    drift.
  - Prices come from the local `prices` table first (the schema already stores them),
    falling back to yfinance only when absent — and ALL listings are included, not just
    US tickers, so CN/HK/JP/KR events are no longer silently dropped.
  - Entry uses `event_date`, the public-information timestamp for filings/announcements.
  - KNOWN LIMITATIONS (surfaced in the result, not silently hidden): no benchmark /
    risk adjustment, no transaction costs, no multiple-comparison correction, and the
    basket is the current registry (survivorship bias). These are illustrative
    self-use diagnostics, not investable backtests.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import timedelta

from ..logging import get_logger
from ..storage import db

log = get_logger("xar.backtest")

_DISCLAIMER = ("Illustrative self-use diagnostic. No benchmark/risk adjustment, no "
               "transaction costs, no multiple-comparison correction; basket is the "
               "current registry (survivorship bias). Not an investable backtest.")


def _local_series(ticker: str, start, end) -> list[tuple]:
    """(date, close) ascending from the local prices table; one source per (ticker,d)."""
    rows = db.query(
        "SELECT d, close FROM prices WHERE ticker=%s AND d BETWEEN %s AND %s "
        "AND close IS NOT NULL ORDER BY d, source LIMIT 1000",
        (ticker, start, end),
    )
    # collapse multiple sources for the same day (keep first)
    seen, out = set(), []
    for r in rows:
        if r["d"] in seen:
            continue
        seen.add(r["d"])
        out.append((r["d"], float(r["close"])))
    return out


def _yf_series(ticker: str, start, end) -> list[tuple]:
    try:
        import yfinance as yf

        df = yf.download(ticker, start=str(start), end=str(end), progress=False, auto_adjust=True)
        if df.empty:
            return []
        return [(idx.date(), float(v)) for idx, v in df["Close"].items() if v == v]
    except Exception as e:  # noqa: BLE001
        log.warning("price fetch failed %s: %s", ticker, e)
        return []


def _series(ticker: str, start, end, need: int = 2) -> list[tuple]:
    """Local prices first; fall back to yfinance when local has FEWER than `need`
    rows. A partially-covered local window (2..need-1 rows) must NOT block the
    fallback — otherwise the caller's length check rejects it and the event is
    silently dropped without yfinance ever being tried. Returns the longer source."""
    s = _local_series(ticker, start, end)
    if len(s) >= need:
        return s
    yf = _yf_series(ticker, start, end)
    return yf if len(yf) > len(s) else s


def backtest(horizons=(5, 20), limit: int = 500) -> dict:
    # Drive off the unified semantic-fact stream (catalyst events + expert stance/
    # narrative layer) so the backtest answers "does the semantic/sentiment layer
    # predict forward returns" — broken out by kind + time_orientation (esp. the
    # forward_looking subset). `as_of` is the public-information timestamp (expert
    # rows default to the source doc's published_at). companies JOIN supplies tickers
    # (the view carries none).
    # Entry = GREATEST(as_of, observed_at): a fact is only actionable once it is BOTH public
    # (as_of/event_date) AND known to us (observed_at), so enter at the LATER of the two —
    # never before we knew (no look-ahead, even for backfilled/late-ingested facts) and on a
    # single consistent basis (no valid/tx-time mixing within an aggregation bucket). as_of
    # NULL falls back to observed_at (which is NOT NULL).
    # Gate: leave room for the forward window — the newest facts have no complete N-day
    # forward price series yet, so require entry to sit ~(max_horizon trading days → calendar
    # + slack) before the latest price date. When prices is empty the gate is skipped (rather
    # than silently returning zero rows) and _series falls back to its per-ticker source.
    maxp = db.query("SELECT max(d) AS m FROM prices")[0]["m"]
    gate, params = "", []
    if maxp is not None:
        gate = "WHERE entry <= %s"
        params.append(maxp - timedelta(days=int(max(horizons) * 1.7) + 5))
    rows = db.query(
        f"""SELECT category, polarity, kind, time_orientation, entry, tickers FROM (
              SELECT s.category, s.polarity, s.kind, s.time_orientation,
                     GREATEST(COALESCE(s.as_of, s.observed_at::date), s.observed_at::date) AS entry,
                     c.tickers
                FROM semantic_facts s JOIN companies c ON c.id = s.company_id
            ) q
            {gate}
            ORDER BY entry DESC LIMIT %s""",
        (*params, limit),
    )
    agg: dict = defaultdict(lambda: {h: [] for h in horizons})
    n_used = 0
    for r in rows:
        tickers = r["tickers"] or []
        if not tickers:
            continue
        d0 = r["entry"]
        # widen the window slightly on each side to land trading days
        series = None
        need = max(horizons) + 1
        for tk in tickers:  # try each listing until one resolves to prices
            s = _series(tk, d0 - timedelta(days=3), d0 + timedelta(days=max(horizons) + 12), need=need)
            if len(s) >= need:
                series = s
                break
        if not series:
            continue
        # base = first close ON/AFTER the event date (event-study t0)
        base_idx = next((i for i, (dt, _) in enumerate(series) if dt >= d0), None)
        if base_idx is None:
            continue
        base = series[base_idx][1]
        if not base:
            continue
        key = (r["category"], r["polarity"], r["kind"], r["time_orientation"])
        used = False
        for h in horizons:
            fwd_idx = base_idx + h
            if fwd_idx < len(series):
                agg[key][h].append((series[fwd_idx][1] / base - 1.0) * 100)
                used = True
        n_used += int(used)

    result = {"events_used": n_used, "by_signal": {}, "disclaimer": _DISCLAIMER}
    for (cat, pol, kind, orient), hmap in agg.items():
        entry: dict = {}
        for h, v in hmap.items():
            entry[f"{h}d_mean_pct"] = round(sum(v) / len(v), 2) if v else None
            entry[f"{h}d_std_pct"] = round(statistics.pstdev(v), 2) if len(v) > 1 else None
            entry[f"{h}d_n"] = len(v)  # per-horizon n (samples differ across horizons)
        result["by_signal"][f"{cat}/{pol}/{kind}/{orient or 'na'}"] = entry
    log.info("backtest: %d events used across %d signals", n_used, len(result["by_signal"]))
    return result
