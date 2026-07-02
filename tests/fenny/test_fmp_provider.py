"""FMP live-data adapter, exercised offline via an injected HTTP getter."""

from __future__ import annotations

import numpy as np
import pytest

from fcn.marketdata.fmp import FMPProvider, FMPUnavailable
from fcn.marketdata.provider import assemble_snapshot
from fcn.marketdata.volsurface import ParametricSkewSurface
from fcn.product.enums import CouponType, Frequency, KIStyle
from fcn.product.termsheet import CouponSpec, KnockInSpec, TermSheet, Underlying
from datetime import date


def _fake_getter(prices: dict[str, list[float]]):
    def getter(path: str, params: dict, api_key: str):
        sym = params.get("symbol")
        if path == "quote":
            return [{"symbol": sym, "price": prices[sym][-1]}]
        if path.startswith("historical-price-eod"):
            # FMP returns most-recent-first.
            return [{"date": f"d{i}", "close": c} for i, c in enumerate(reversed(prices[sym]))]
        if path == "treasury-rates":
            return [{"year1": 4.5}]
        raise AssertionError(path)
    return getter


def test_spot_and_correlation_from_history():
    rng = np.random.default_rng(0)
    base = np.cumprod(1 + rng.normal(0, 0.01, 260))
    prices = {"AAA": (100 * base).tolist(), "BBB": (50 * base * (1 + rng.normal(0, 0.003, 260))).tolist()}
    p = FMPProvider(api_key="x", getter=_fake_getter(prices))
    assert p.spot("AAA") == pytest.approx(prices["AAA"][-1])
    corr = p.correlation(["AAA", "BBB"])
    assert corr.matrix.shape == (2, 2)
    assert corr.matrix[0, 1] > 0.5  # constructed to be highly correlated


def test_vol_surface_is_none_then_falls_back():
    p = FMPProvider(api_key="x", getter=_fake_getter({"AAA": [100.0] * 5}))
    assert p.vol_surface("AAA") is None  # no live IV -> engine uses parametric fallback


def test_treasury_refresh():
    p = FMPProvider(api_key="x", getter=_fake_getter({"AAA": [100.0] * 5}))
    assert p.treasury_rate() == pytest.approx(0.045)


def test_unavailable_without_key():
    p = FMPProvider(api_key=None)
    with pytest.raises(FMPUnavailable):
        p.spot("AAA")


def test_assemble_snapshot_with_fmp_and_parametric_fallback():
    prices = {"AAA": [100.0 + i * 0.1 for i in range(260)]}
    p = FMPProvider(
        api_key="x", getter=_fake_getter(prices),
        user_surfaces={"AAA": ParametricSkewSurface(atm=0.3, slope=-0.5)},
    )
    ts = TermSheet(
        notional=100.0, trade_date=date(2026, 6, 18), strike_date=date(2026, 6, 18),
        maturity=date(2027, 6, 18), underlyings=[Underlying(ticker="AAA")], autocall=None,
        coupon=CouponSpec(type=CouponType.FIXED, rate=0.08, frequency=Frequency.QUARTERLY),
        knock_in=KnockInSpec(barrier=0.65, style=KIStyle.EUROPEAN),
    )
    snap = assemble_snapshot(p, ts, "2026-06-18")
    assert snap.assets[0].spot == pytest.approx(prices["AAA"][-1])
    assert snap.assets[0].surface.atm_vol(1.0) == pytest.approx(0.3)
