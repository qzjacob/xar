"""Go/No-Go gate: the MC engine must reproduce the closed-form single-name,
European-KI, fixed-coupon, no-autocall note within a few standard errors."""

from __future__ import annotations

from datetime import date

import numpy as np

from fcn.analytics.closed_form import (
    black_scholes,
    down_and_in_put_european,
    reiner_rubinstein_down_in_put,
    single_name_european_note,
)
from fcn.core.rng import RNGSpec
from fcn.marketdata.provider import ManualProvider, assemble_snapshot
from fcn.marketdata.volsurface import FlatVolSurface
from fcn.pricing.grid import build_grid
from fcn.pricing.mcengine import MCConfig, MCEngine
from fcn.product.enums import CouponType, Frequency, KIStyle, Settlement
from fcn.product.termsheet import CouponSpec, KnockInSpec, TermSheet, Underlying

SPOT = 100.0
SIGMA = 0.25
RATE = 0.03
KI = 0.65
STRIKE = 1.0
COUPON = 0.08


def _note() -> TermSheet:
    return TermSheet(
        notional=100.0,
        trade_date=date(2026, 6, 18),
        strike_date=date(2026, 6, 18),
        maturity=date(2027, 6, 18),
        underlyings=[Underlying(ticker="AAA", strike=STRIKE)],
        autocall=None,
        coupon=CouponSpec(type=CouponType.FIXED, rate=COUPON, frequency=Frequency.QUARTERLY),
        knock_in=KnockInSpec(barrier=KI, style=KIStyle.EUROPEAN, settlement=Settlement.CASH),
    )


def _provider() -> ManualProvider:
    return ManualProvider(
        spots={"AAA": SPOT}, surfaces={"AAA": FlatVolSurface(SIGMA)}, rate=RATE
    )


def test_mc_matches_closed_form_note():
    ts = _note()
    provider = _provider()
    snap = assemble_snapshot(provider, ts, "2026-06-18")
    grid = build_grid(ts)
    coupon_times = grid.times[grid.coupon_idx].tolist()
    coupon_taus = grid.coupon_tau.tolist()
    maturity = float(grid.times[grid.maturity_idx])

    cf = single_name_european_note(
        spot=SPOT, initial_fixing=SPOT, ki_fraction=KI, strike_fraction=STRIKE,
        sigma=SIGMA, r=RATE, q=0.0, borrow=0.0, funding=RATE, coupon_rate=COUPON,
        coupon_times=coupon_times, coupon_taus=coupon_taus, maturity=maturity, notional=100.0,
    )

    engine = MCEngine(config=MCConfig(n_paths=300_000, rng=RNGSpec(method="pseudo", antithetic=True)))
    res = engine.price(ts, snap)

    diff = abs(res.pv - cf.pv)
    assert diff < 4.0 * res.pv_se + 1e-3, (
        f"MC {res.pv:.4f} vs CF {cf.pv:.4f} (diff {diff:.4f}, 4*SE {4*res.pv_se:.4f})"
    )
    # Relative tolerance sanity (should be well under 0.2% of par).
    assert diff / 100.0 < 2e-3


def test_reiner_rubinstein_invariants():
    """Continuous down-and-in put: non-negative, below vanilla, and DI+DO == vanilla."""
    for (s, k, b, sig, r, q) in [(100, 100, 70, 0.25, 0.03, 0.03),
                                 (100, 100, 80, 0.30, 0.02, 0.0),
                                 (95, 100, 75, 0.35, 0.04, 0.01)]:
        di = reiner_rubinstein_down_in_put(s, k, b, 1.0, sig, r, q)
        van = black_scholes(s, k, 1.0, sig, r, q, call=False)
        assert di >= 0.0
        assert di <= van + 1e-9  # a down-and-IN put is worth no more than the vanilla put
    # Known value (Haug): S=K=100, B=70, T=1, sigma=25%, r=q=3% -> ~5.34
    assert abs(reiner_rubinstein_down_in_put(100, 100, 70, 1.0, 0.25, 0.03, 0.03) - 5.336) < 0.02


def test_mc_american_ki_matches_continuous_dip():
    """Go/No-Go for the American (continuous) KI + Brownian-bridge correction:
    a single-name American-KI note must match par + coupons − (N/K)·continuous-DIP."""
    ki, strike, sigma, rate, coupon = 0.70, 1.0, 0.25, 0.03, 0.08
    ts = TermSheet(
        notional=100.0, trade_date=date(2026, 6, 18), strike_date=date(2026, 6, 18),
        maturity=date(2027, 6, 18), underlyings=[Underlying(ticker="AAA", strike=strike)],
        autocall=None,
        coupon=CouponSpec(type=CouponType.FIXED, rate=coupon, frequency=Frequency.QUARTERLY),
        knock_in=KnockInSpec(barrier=ki, style=KIStyle.AMERICAN, settlement=Settlement.CASH),
    )
    provider = ManualProvider(spots={"AAA": SPOT}, surfaces={"AAA": FlatVolSurface(sigma)}, rate=rate)
    snap = assemble_snapshot(provider, ts, "2026-06-18")
    grid = build_grid(ts)
    maturity = float(grid.times[grid.maturity_idx])
    coupon_pv = sum(
        100.0 * coupon * tau * np.exp(-rate * t)
        for t, tau in zip(grid.times[grid.coupon_idx], grid.coupon_tau, strict=True)
    )
    dip = reiner_rubinstein_down_in_put(SPOT, strike * SPOT, ki * SPOT, maturity, sigma, rate, 0.0)
    cf = coupon_pv + 100.0 * np.exp(-rate * maturity) - (100.0 / (strike * SPOT)) * dip

    engine = MCEngine(config=MCConfig(n_paths=200_000, rng=RNGSpec(method="pseudo", antithetic=True)))
    res = engine.price(ts, snap)
    # daily discrete + Brownian-bridge vs continuous closed form: a few SE + small residual bias
    assert abs(res.pv - cf) < 4 * res.pv_se + 0.20, f"MC {res.pv:.3f} vs continuous-DIP CF {cf:.3f}"


def test_european_dip_decomposition_consistency():
    """Closed-form note redemption == par - (N/K) * European DIP."""
    maturity = 1.0
    dip = down_and_in_put_european(
        spot=SPOT, strike=STRIKE * SPOT, barrier=KI * SPOT, t=maturity,
        sigma=SIGMA, r=RATE, q=0.0, borrow=0.0,
    )
    cf = single_name_european_note(
        spot=SPOT, initial_fixing=SPOT, ki_fraction=KI, strike_fraction=STRIKE,
        sigma=SIGMA, r=RATE, q=0.0, borrow=0.0, funding=RATE, coupon_rate=0.0,
        coupon_times=[], coupon_taus=[], maturity=maturity, notional=100.0,
    )
    expected = 100.0 * np.exp(-RATE * maturity) - (100.0 / (STRIKE * SPOT)) * dip
    assert abs(cf.pv_redemption - expected) < 1e-8
