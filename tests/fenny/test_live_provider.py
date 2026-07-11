"""FMPLiveProvider — real-data adapter for Market Read / Finder / Options auto mode,
exercised offline via an injected FMP getter. Plus the market-read monthly trend +
timing view built on top of it."""

from __future__ import annotations

import numpy as np
import pytest

from fcn.marketdata.fmp import FMPProvider
from fcn.service.live_provider import FMPLiveProvider
from fcn.service.market_read import build_market_read, monthly_trend, timing_view


def _mk_rows(closes: list[float], start="2026-01-02"):
    """EOD rows most-recent-first, ~21 trading days per month, ISO dates."""
    import datetime as dt

    d0 = dt.date.fromisoformat(start)
    rows = []
    day = d0
    for c in closes:  # oldest→newest input
        while day.weekday() >= 5:
            day += dt.timedelta(days=1)
        rows.append({"date": day.isoformat(), "price": round(c, 4)})
        day += dt.timedelta(days=1)
    return rows[::-1]  # FMP returns most-recent-first


def _fake_getter(prices: dict[str, list[float]], treasury=4.2):
    def getter(path: str, params: dict, api_key: str):
        sym = params.get("symbol")
        if path == "quote":
            if sym not in prices:
                raise AssertionError(f"no quote {sym}")
            return [{"symbol": sym, "price": prices[sym][-1]}]
        if path.startswith("historical-price-eod"):
            return _mk_rows(prices[sym])
        if path == "treasury-rates":
            return [{"year1": treasury}]
        raise AssertionError(path)
    return getter


def _trending_series(n=180, vol_daily=0.012, drift=0.0006, seed=7):
    rng = np.random.default_rng(seed)
    return (100 * np.cumprod(1 + rng.normal(drift, vol_daily, n))).tolist()


def _provider(prices, rate=None):
    fmp = FMPProvider(api_key="x", getter=_fake_getter(prices))
    return FMPLiveProvider(rate=rate, fmp=fmp)


def test_term_structured_realized_surface_and_treasury_rate():
    p = _provider({"SPY": _trending_series()})
    assert p.risk_free_rate() == pytest.approx(0.042)   # live treasury, not the 4.5% default
    surf = p.vol_surface("SPY")
    assert surf is not None
    # realized windows differ → a genuine term structure (1M vs 1Y anchors exist)
    v1m, v1y = surf.atm_vol(1 / 12), surf.atm_vol(1.0)
    assert 0.05 < v1m < 1.5 and 0.05 < v1y < 1.5
    # put skew: vol at 90% strike above ATM (desk parametric skew applied)
    lo = float(surf.implied_vol(np.array([-0.10]), 0.25)[0])
    assert lo > surf.atm_vol(0.25)


def test_monthly_samples_and_trend_and_timing():
    p = _provider({"SPY": _trending_series(), "QQQ": _trending_series(seed=11)})
    samples = p.monthly_samples("SPY")
    assert len(samples) >= 3
    assert all(set(s) == {"month", "spot", "rv21"} for s in samples)
    assert samples == sorted(samples, key=lambda s: s["month"])  # oldest→newest

    trend = monthly_trend(p, ("SPY", "QQQ"))
    assert trend is not None and len(trend["per_index"]) == 2
    assert "vol_mom" in trend and "px_3m" in trend

    metrics = {"vol_level": 0.30}
    t = timing_view(metrics, trend, lang="zh")
    for fam in ("FCN", "Phoenix", "Snowball", "SharkFin", "Booster"):
        assert t[fam]["stance"] in ("enter_now", "wait", "neutral")
        assert t[fam]["label"] and t[fam]["drivers"]


def test_build_market_read_auto_includes_trend_and_timing():
    p = _provider({"SPY": _trending_series(), "QQQ": _trending_series(seed=11)})
    r = build_market_read(p, indices=("SPY", "QQQ"), lang="zh", llm_caller=lambda *a, **k: None)
    assert r["metrics"]["vol_basis"] == "realized"
    assert r["trend"] is not None and r["timing"] is not None
    assert r["narrative_source"] == "template"   # no LLM injected → fallback still works


def test_manual_provider_read_has_no_trend_but_timing_present():
    # providers without monthly_samples (Manual/Massive) keep the old payload shape + timing
    from fcn.marketdata.provider import ManualProvider
    from fcn.marketdata.volsurface import ParametricSkewSurface

    prov = ManualProvider(
        spots={"SPY": 540}, surfaces={"SPY": ParametricSkewSurface(atm=0.2, slope=-0.5)}, rate=0.04,
    )
    r = build_market_read(prov, indices=("SPY",), llm_caller=lambda *a, **k: None)
    assert r["trend"] is None
    assert r["timing"]["FCN"]["stance"] in ("enter_now", "wait", "neutral")


def test_alias_and_screen_passthrough():
    prices = {"TSLA": _trending_series(), "GOOGL": _trending_series(seed=3)}

    def getter(path, params, api_key):
        sym = params.get("symbol")
        if path == "quote":
            if sym == "GOOG":
                raise RuntimeError("HTTP 402")
            return [{"symbol": sym, "price": prices[sym][-1]}]
        if path.startswith("historical-price-eod"):
            return _mk_rows(prices[sym])
        if path == "treasury-rates":
            return [{"year1": 4.0}]
        if path == "company-screener":
            return [{"symbol": "AAA", "companyName": "A", "marketCap": 5e11,
                     "sector": "Tech", "exchangeShortName": "NASDAQ", "isEtf": False}]
        raise AssertionError(path)

    p = FMPLiveProvider(fmp=FMPProvider(api_key="x", getter=getter))
    assert p.spot("GOOG") == pytest.approx(prices["GOOGL"][-1])   # 402 → GOOGL alias
    assert p.vol_surface("GOOG") is not None
    uni = p.screen_universe(min_market_cap=1e10)
    assert uni and uni[0]["ticker"] == "AAA"
