"""Tests for the supplementary correctness work (review response groups A/B)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from fcn.core.rng import RNGSpec
from fcn.marketdata.correlation import Correlation
from fcn.marketdata.curve import DiscountCurve, DiscreteDividend, ForwardCurve
from fcn.marketdata.provider import ManualProvider, assemble_snapshot
from fcn.marketdata.snapshot import AssetMarket, MarketSnapshot
from fcn.marketdata.volsurface import FlatVolSurface, ParametricSkewSurface, dupire_local_vol
from fcn.pricing.grid import build_grid
from fcn.pricing.mcengine import MCConfig, MCEngine
from fcn.pricing.pathgen import GBMPathGenerator
from fcn.product.enums import CouponType, Frequency, KIStyle
from fcn.product.termsheet import CouponSpec, KnockInSpec, TermSheet, Underlying

TRADE = date(2026, 6, 20)
MAT = date(2027, 6, 18)


# ---- B: Dupire local vol ----
def test_dupire_flat_surface_returns_sigma():
    lv = dupire_local_vol(FlatVolSurface(0.25), np.array([-0.3, 0.0, 0.2]), 1.0)
    assert np.allclose(lv, 0.25, atol=1e-3)


def test_dupire_never_nan_on_steep_skew():
    surf = ParametricSkewSurface(atm=0.3, slope=-0.8, curv=2.0)
    lv = dupire_local_vol(surf, np.linspace(-0.6, 0.4, 25), 0.5)
    assert np.all(np.isfinite(lv)) and np.all(lv > 0)


# ---- A: discrete-dividend ex-date jump + forward parity ----
def _ki_note(style=KIStyle.AMERICAN):
    return TermSheet(
        notional=100.0, trade_date=TRADE, strike_date=TRADE, maturity=MAT,
        underlyings=[Underlying(ticker="X")], autocall=None,
        coupon=CouponSpec(type=CouponType.FIXED, rate=0.0, frequency=Frequency.QUARTERLY),
        knock_in=KnockInSpec(barrier=0.6, style=style),
    )


def test_discrete_dividend_forward_parity():
    ts = _ki_note()
    grid = build_grid(ts)
    fwd = ForwardCurve(spot=100.0, rate=0.05, div_yield=0.0, borrow=0.0,
                       dividends=(DiscreteDividend(0.5, 3.0),))
    asset = AssetMarket("X", spot=100.0, initial_fixing=100.0, forward=fwd, surface=FlatVolSurface(0.2))
    snap = MarketSnapshot("2026-06-20", (asset,), DiscountCurve(0.05), Correlation.uniform(1, 0.0))
    bundle = GBMPathGenerator(local_vol=False).generate(snap, grid, RNGSpec(seed=3), 200_000)
    sT = bundle.S[:, grid.maturity_idx, 0].mean()
    fT = float(np.atleast_1d(fwd.forward(grid.times[grid.maturity_idx]))[0])
    assert abs(sT - fT) / fT < 5e-3  # martingale to the discrete-dividend forward


# ---- A: strike conversion uses the right strike (monotonic in strike) ----
def test_pv_decreases_with_strike():
    eng = MCEngine(config=MCConfig(n_paths=40_000, rng=RNGSpec(method="pseudo")))
    def pv(strike):
        ts = TermSheet(
            notional=100.0, trade_date=TRADE, strike_date=TRADE, maturity=MAT,
            underlyings=[Underlying(ticker="X", strike=strike)], autocall=None,
            coupon=CouponSpec(type=CouponType.FIXED, rate=0.08, frequency=Frequency.QUARTERLY),
            knock_in=KnockInSpec(barrier=0.65, style=KIStyle.EUROPEAN),
        )
        prov = ManualProvider(spots={"X": 100.0}, surfaces={"X": FlatVolSurface(0.3)}, rate=0.03)
        return eng.price(ts, assemble_snapshot(prov, ts, "2026-06-20")).pv
    assert pv(1.10) < pv(0.90)  # higher conversion strike -> worse for investor


def test_worst_performer_strike_used_for_two_assets():
    """Two assets, different strikes: loss converts at the worst performer's strike."""
    eng = MCEngine(config=MCConfig(n_paths=30_000, rng=RNGSpec(method="pseudo")))
    ts = TermSheet(
        notional=100.0, trade_date=TRADE, strike_date=TRADE, maturity=MAT,
        underlyings=[Underlying(ticker="A", strike=1.0), Underlying(ticker="B", strike=1.2)],
        autocall=None,
        coupon=CouponSpec(type=CouponType.FIXED, rate=0.10, frequency=Frequency.QUARTERLY),
        knock_in=KnockInSpec(barrier=0.65, style=KIStyle.EUROPEAN),
    )
    prov = ManualProvider(
        spots={"A": 100.0, "B": 100.0},
        surfaces={"A": FlatVolSurface(0.3), "B": FlatVolSurface(0.3)},
        rate=0.03, corr=Correlation.uniform(2, 0.5),
    )
    res = eng.price(ts, assemble_snapshot(prov, ts, "2026-06-20"))
    assert res.pv > 0 and np.isfinite(res.pv)  # runs without error on non-uniform strikes
    assert res.redemption_pv < res.notional  # downside (short put) reduces redemption below par
    assert res.prob_knock_in > 0


# ---- C: risk-management upgrades ----
def _single_fcn_snap(coupon=0.10):
    ts = TermSheet(
        notional=100.0, trade_date=TRADE, strike_date=TRADE, maturity=MAT,
        underlyings=[Underlying(ticker="X")], autocall=None,
        coupon=CouponSpec(type=CouponType.FIXED, rate=coupon, frequency=Frequency.QUARTERLY),
        knock_in=KnockInSpec(barrier=0.65, style=KIStyle.EUROPEAN),
    )
    prov = ManualProvider(spots={"X": 100.0}, surfaces={"X": ParametricSkewSurface(atm=0.3, slope=-0.5)}, rate=0.03)
    return ts, assemble_snapshot(prov, ts, "2026-06-20")


def test_rho_and_carry_have_opposite_signs():
    from fcn.pricing.greeks import GreeksEngine
    eng = MCEngine(config=MCConfig(n_paths=50_000, rng=RNGSpec(seed=5, method="pseudo")))
    ts, snap = _single_fcn_snap()
    g = GreeksEngine(eng).compute(ts, snap, 0.10)
    assert g.rho < 0 < g.carry  # discount up -> PV down; growth up -> KI less likely -> PV up


def test_bucketed_and_skew_vega():
    from fcn.pricing.greeks import GreeksEngine
    eng = MCEngine(config=MCConfig(n_paths=50_000, rng=RNGSpec(seed=5, method="pseudo")))
    ts, snap = _single_fcn_snap()
    g = GreeksEngine(eng).compute(ts, snap, 0.10)
    assert set(g.bucketed_vega.keys()) == {"-0.30", "-0.20", "-0.10", "+0.00", "+0.10"}
    assert g.bucketed_vega["-0.20"] < 0  # short the put-wing vol
    assert np.isfinite(g.skew_vega)


def test_snowball_accrual_decreases_with_ki_barrier():
    eng = MCEngine(config=MCConfig(n_paths=40_000, rng=RNGSpec(method="pseudo")))
    from fcn.product.presets import build_snowball
    def factor(ki):
        ts = build_snowball(tickers=["X"], notional=100.0, trade_date=TRADE, strike_date=TRADE,
                            maturity=MAT, coupon_rate=None, ki_barrier=ki)
        prov = ManualProvider(spots={"X": 100.0}, surfaces={"X": ParametricSkewSurface(atm=0.35, slope=-0.5)}, rate=0.03)
        return eng.price(ts, assemble_snapshot(prov, ts, "2026-06-20")).coupon_factor
    assert factor(0.80) < factor(0.50)  # higher KI -> earlier first-passage -> less accrual


def test_issuer_spread_lowers_pv():
    eng = MCEngine(config=MCConfig(n_paths=30_000, rng=RNGSpec(seed=9, method="pseudo")))
    ts, snap = _single_fcn_snap()
    pv0 = eng.price(ts, snap, 0.10).pv
    pv1 = eng.price(ts, snap.shock_discount_rate(0.02), 0.10).pv  # +200bp issuer spread
    assert pv1 < pv0
