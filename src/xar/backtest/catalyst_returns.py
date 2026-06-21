"""Catalyst -> forward-return backtest. Quantifies signal efficacy: for each
dated catalyst event, measure the company's forward return over N trading days,
then aggregate by event_type x polarity.

Prices via yfinance (INTERNAL/self-use only, prototyping posture). Degrades
gracefully if yfinance/prices are unavailable."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ..logging import get_logger
from ..storage import db

log = get_logger("xar.backtest")


def _prices(ticker: str, start, end):
    try:
        import yfinance as yf

        df = yf.download(ticker, start=str(start), end=str(end), progress=False, auto_adjust=True)
        return df["Close"] if not df.empty else None
    except Exception as e:
        log.warning("price fetch failed %s: %s", ticker, e)
        return None


def _us_ticker(tickers: list[str]) -> str | None:
    return next((t for t in tickers if "." not in t), None)


def backtest(horizons=(5, 20), limit: int = 500) -> dict:
    rows = db.query(
        """SELECT e.event_type, e.polarity, e.event_date, c.tickers
           FROM kg_events e JOIN companies c ON c.id = e.company_id
           WHERE e.event_date IS NOT NULL AND e.invalidated_at IS NULL
           ORDER BY e.event_date DESC LIMIT %s""",
        (limit,),
    )
    agg: dict = defaultdict(lambda: {h: [] for h in horizons})
    n_used = 0
    for r in rows:
        ticker = _us_ticker(r["tickers"] or [])
        if not ticker:
            continue
        d0 = r["event_date"]
        series = _prices(ticker, d0 - timedelta(days=5), d0 + timedelta(days=max(horizons) + 10))
        if series is None or len(series) < max(horizons) + 1:
            continue
        try:
            base = float(series.iloc[0])
            key = (r["event_type"], r["polarity"])
            for h in horizons:
                if len(series) > h:
                    fwd = (float(series.iloc[h]) / base - 1.0) * 100
                    agg[key][h].append(fwd)
            n_used += 1
        except Exception:
            continue

    result = {"events_used": n_used, "by_signal": {}}
    for (etype, pol), hmap in agg.items():
        result["by_signal"][f"{etype}/{pol}"] = {
            f"{h}d_mean_pct": round(sum(v) / len(v), 2) if v else None for h, v in hmap.items()
        } | {"n": len(next(iter(hmap.values())))}
    log.info("backtest: %d events used across %d signals", n_used, len(result["by_signal"]))
    return result
