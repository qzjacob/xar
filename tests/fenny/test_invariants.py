"""Economic invariants the engine must respect (plan §5).

These are the cheap, high-signal checks that catch sign errors and broken variants:
correlation monotonicity for worst-of, PV monotonic in the KI barrier, fair coupon
rising with vol, and degenerate-limit equivalences (KI->0, conditional==fixed,
cash==physical).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from fcn.core.rng import RNGSpec
from fcn.marketdata.correlation import Correlation
from fcn.marketdata.provider import ManualProvider, assemble_snapshot
from fcn.marketdata.volsurface import FlatVolSurface
from fcn.pricing.mcengine import MCConfig, MCEngine
from fcn.pricing.solver import solve_coupon
from fcn.product.enums import CouponType, Frequency, KIStyle, Settlement
from fcn.product.presets import build_fcn
from fcn.product.termsheet import CouponSpec, KnockInSpec, TermSheet, Underlying

TRADE = date(2026, 6, 18)
MAT = date(2027, 6, 18)
ENGINE = MCEngine(config=MCConfig(n_paths=60_000, rng=RNGSpec(method="pseudo", antithetic=True)))


def _single(ki=0.65, sigma=0.25, coupon=0.08, style=KIStyle.EUROPEAN, settlement=Settlement.CASH):
    ts = TermSheet(
        notional=100.0, trade_date=TRADE, strike_date=TRADE, maturity=MAT,
        underlyings=[Underlying(ticker="AAA")],
        autocall=None,
        coupon=CouponSpec(type=CouponType.FIXED, rate=coupon, frequency=Frequency.QUARTERLY),
        knock_in=KnockInSpec(barrier=ki, style=style, settlement=settlement),
    )
    provider = ManualProvider(spots={"AAA": 100.0}, surfaces={"AAA": FlatVolSurface(sigma)}, rate=0.03)
    return ts, assemble_snapshot(provider, ts, "2026-06-18")


def test_pv_decreases_as_ki_barrier_rises():
    ts_lo, snap_lo = _single(ki=0.50)
    ts_hi, snap_hi = _single(ki=0.80)
    pv_lo = ENGINE.price(ts_lo, snap_lo).pv
    pv_hi = ENGINE.price(ts_hi, snap_hi).pv
    assert pv_hi < pv_lo  # higher KI barrier -> more downside -> worth less


def test_fair_coupon_rises_with_vol():
    ts_lo, snap_lo = _single(sigma=0.20)
    ts_hi, snap_hi = _single(sigma=0.45)
    c_lo = solve_coupon(ENGINE, ts_lo, snap_lo, 0.977).coupon_rate
    c_hi = solve_coupon(ENGINE, ts_hi, snap_hi, 0.977).coupon_rate
    assert c_hi > c_lo  # short-vol product: higher vol -> higher coupon


def test_worst_of_value_rises_with_correlation():
    def price(rho):
        ts = build_fcn(
            tickers=["AAA", "BBB"], notional=100.0, trade_date=TRADE, strike_date=TRADE,
            maturity=MAT, coupon_rate=0.10, frequency=Frequency.QUARTERLY,
            autocall_barrier=1.0, ki_barrier=0.65, ki_style=KIStyle.EUROPEAN,
        )
        provider = ManualProvider(
            spots={"AAA": 100.0, "BBB": 100.0},
            surfaces={"AAA": FlatVolSurface(0.30), "BBB": FlatVolSurface(0.30)},
            rate=0.03, corr=Correlation.uniform(2, rho),
        )
        return ENGINE.price(ts, assemble_snapshot(provider, ts, "2026-06-18")).pv

    assert price(0.90) > price(0.20)  # higher correlation -> worst-of less extreme -> worth more


def test_ki_to_zero_is_riskfree_annuity_plus_par():
    ts, snap = _single(ki=0.01)
    res = ENGINE.price(ts, snap)
    # No realistic path breaches a 1% barrier -> redemption ~ par discounted.
    df = float(np.exp(-0.03 * 1.0))
    assert abs(res.redemption_pv - 100.0 * df) < 0.05


def test_conditional_with_tiny_barrier_equals_fixed():
    base = dict(
        notional=100.0, trade_date=TRADE, strike_date=TRADE, maturity=MAT,
        underlyings=[Underlying(ticker="AAA")], autocall=None,
        knock_in=KnockInSpec(barrier=0.65, style=KIStyle.EUROPEAN),
    )
    fixed = TermSheet(coupon=CouponSpec(type=CouponType.FIXED, rate=0.08), **base)
    cond = TermSheet(
        coupon=CouponSpec(type=CouponType.CONDITIONAL, rate=0.08, barrier=0.01), **base
    )
    provider = ManualProvider(spots={"AAA": 100.0}, surfaces={"AAA": FlatVolSurface(0.25)}, rate=0.03)
    snap = assemble_snapshot(provider, fixed, "2026-06-18")
    f = ENGINE.price(fixed, snap).coupon_factor
    c = ENGINE.price(cond, snap).coupon_factor
    assert abs(f - c) < 1e-6  # barrier always met -> identical coupons


def test_cash_and_physical_have_equal_pv():
    ts_cash, snap = _single(settlement=Settlement.CASH)
    ts_phys, _ = _single(settlement=Settlement.PHYSICAL)
    assert ENGINE.price(ts_cash, snap).pv == pytest.approx(ENGINE.price(ts_phys, snap).pv, abs=1e-9)
