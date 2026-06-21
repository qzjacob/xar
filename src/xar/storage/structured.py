"""Upsert + read helpers for the structured-data layer (fundamentals, estimates,
ratings, prices, insider trades, prediction markets, social posts).

All writes are idempotent (provider-scoped UNIQUE keys) so re-pulls never
duplicate. Every row records its `source` and an `as_of`/observation time so the
same fact from multiple providers — and the evolution of consensus — coexist.
"""
from __future__ import annotations

import hashlib
import json

from ..logging import get_logger
from . import db

log = get_logger("xar.structured")


def _json(d) -> str:
    return json.dumps(d or {}, ensure_ascii=False, default=str)


# --- fundamentals ----------------------------------------------------------
def upsert_fundamental(company_id, metric, value, *, period=None, period_end=None,
                       freq=None, unit="USD", source="", meta=None) -> None:
    if value is None:
        return
    db.execute(
        """INSERT INTO fundamentals
             (company_id,metric,period,period_end,freq,value,unit,source,meta)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (company_id,metric,period,source) DO UPDATE SET
             value=EXCLUDED.value, period_end=EXCLUDED.period_end,
             freq=EXCLUDED.freq, unit=EXCLUDED.unit, as_of=now(), meta=EXCLUDED.meta""",
        (company_id, metric, period, period_end, freq, float(value), unit, source, _json(meta)),
    )


# --- estimates -------------------------------------------------------------
def upsert_estimate(company_id, metric, value, as_of, *, period=None, period_end=None,
                    high=None, low=None, n_analysts=None, unit="USD", source="",
                    meta=None) -> None:
    if value is None:
        return
    db.execute(
        """INSERT INTO estimates
             (company_id,metric,period,period_end,value,high,low,n_analysts,unit,source,as_of,meta)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (company_id,metric,period,source,as_of) DO UPDATE SET
             value=EXCLUDED.value, high=EXCLUDED.high, low=EXCLUDED.low,
             n_analysts=EXCLUDED.n_analysts, meta=EXCLUDED.meta""",
        (company_id, metric, period, period_end, float(value),
         _f(high), _f(low), n_analysts, unit, source, as_of, _json(meta)),
    )


# --- analyst ratings -------------------------------------------------------
def upsert_rating(company_id, as_of, *, strong_buy=None, buy=None, hold=None,
                  sell=None, strong_sell=None, pt_mean=None, pt_high=None,
                  pt_low=None, source="", meta=None) -> None:
    db.execute(
        """INSERT INTO analyst_ratings
             (company_id,as_of,strong_buy,buy,hold,sell,strong_sell,pt_mean,pt_high,pt_low,source,meta)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (company_id,as_of,source) DO UPDATE SET
             strong_buy=EXCLUDED.strong_buy, buy=EXCLUDED.buy, hold=EXCLUDED.hold,
             sell=EXCLUDED.sell, strong_sell=EXCLUDED.strong_sell,
             pt_mean=EXCLUDED.pt_mean, pt_high=EXCLUDED.pt_high, pt_low=EXCLUDED.pt_low,
             meta=EXCLUDED.meta""",
        (company_id, as_of, strong_buy, buy, hold, sell, strong_sell,
         _f(pt_mean), _f(pt_high), _f(pt_low), source, _json(meta)),
    )


# --- prices ----------------------------------------------------------------
def upsert_prices(company_id, ticker, bars, *, source="") -> int:
    """bars: iterable of dicts with d/open/high/low/close/volume."""
    rows = [
        (company_id, ticker, b["d"], _f(b.get("open")), _f(b.get("high")),
         _f(b.get("low")), _f(b.get("close")), _f(b.get("volume")), source)
        for b in bars if b.get("d") is not None
    ]
    if not rows:
        return 0
    db.executemany(
        """INSERT INTO prices(company_id,ticker,d,open,high,low,close,volume,source)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (ticker,d,source) DO UPDATE SET close=EXCLUDED.close,
             open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
             volume=EXCLUDED.volume""",
        rows,
    )
    return len(rows)


# --- insider trades --------------------------------------------------------
def upsert_insider(company_id, *, insider=None, role=None, txn_date=None,
                   txn_type=None, shares=None, price=None, value=None,
                   source="", meta=None) -> bool:
    dedup = hashlib.sha256(
        f"{company_id}|{insider}|{txn_date}|{txn_type}|{shares}|{price}".encode()
    ).hexdigest()[:32]
    if db.query("SELECT 1 FROM insider_trades WHERE dedup_key=%s", (dedup,)):
        return False
    db.execute(
        """INSERT INTO insider_trades
             (company_id,insider,role,txn_date,txn_type,shares,price,value,source,dedup_key,meta)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (dedup_key) DO NOTHING""",
        (company_id, insider, role, txn_date, txn_type, _f(shares), _f(price),
         _f(value), source, dedup, _json(meta)),
    )
    return True


# --- prediction markets ----------------------------------------------------
def upsert_prediction_market(market_id, *, question=None, outcome=None,
                             probability=None, volume=None, close_date=None,
                             tags=None, company_id=None, tech_route_tag=None,
                             source="polymarket", meta=None) -> None:
    db.execute(
        """INSERT INTO prediction_markets
             (market_id,question,outcome,probability,volume,close_date,tags,
              company_id,tech_route_tag,source,meta)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (market_id,outcome,as_of) DO NOTHING""",
        (str(market_id), question, outcome, _f(probability), _f(volume), close_date,
         tags or [], company_id, tech_route_tag, source, _json(meta)),
    )


# --- social posts ----------------------------------------------------------
def upsert_social(post_id, platform, *, company_id=None, author=None, url=None,
                  posted_at=None, text=None, metrics=None, sentiment=None,
                  permission="grey", meta=None) -> None:
    db.execute(
        """INSERT INTO social_posts
             (id,platform,company_id,author,url,posted_at,text,metrics,sentiment,permission,meta)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (id) DO UPDATE SET
             metrics=EXCLUDED.metrics, sentiment=EXCLUDED.sentiment, text=EXCLUDED.text""",
        (f"{platform}:{post_id}", platform, company_id, author, url, posted_at,
         text, _json(metrics), _f(sentiment), permission, _json(meta)),
    )


# --- reads (used by API + signals + report context) ------------------------
def latest_fundamentals(company_id, limit=40) -> list[dict]:
    return db.query(
        "SELECT metric,period,period_end,freq,value,unit,source FROM fundamentals "
        "WHERE company_id=%s ORDER BY period_end DESC NULLS LAST, metric LIMIT %s",
        (company_id, limit),
    )


def estimate_series(company_id, metric, period) -> list[dict]:
    return db.query(
        "SELECT as_of,value,high,low,n_analysts,source FROM estimates "
        "WHERE company_id=%s AND metric=%s AND period=%s ORDER BY as_of",
        (company_id, metric, period),
    )


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
