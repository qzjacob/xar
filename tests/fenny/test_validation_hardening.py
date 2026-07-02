"""Validation hardening: golden regression snapshot + multi-asset BB residual bias."""

from __future__ import annotations

from datetime import date

from fcn.core.rng import RNGSpec
from fcn.marketdata.correlation import Correlation
from fcn.marketdata.provider import ManualProvider, assemble_snapshot
from fcn.marketdata.volsurface import FlatVolSurface, ParametricSkewSurface
from fcn.pricing.mcengine import MCConfig, MCEngine
from fcn.pricing.solver import solve_coupon
from fcn.product.enums import CouponType, Frequency, KIStyle
from fcn.product.presets import build_fcn
from fcn.product.termsheet import CouponSpec, KnockInSpec, TermSheet, Underlying

TRADE = date(2026, 6, 20)
MAT = date(2027, 6, 18)


def test_golden_fcn_regression():
    """Lock the PV of a fixed single-name FCN — guards against silent engine drift."""
    ts = TermSheet(
        notional=100.0, trade_date=TRADE, strike_date=TRADE, maturity=MAT,
        underlyings=[Underlying(ticker="X")], autocall=None,
        coupon=CouponSpec(type=CouponType.FIXED, rate=0.08, frequency=Frequency.QUARTERLY),
        knock_in=KnockInSpec(barrier=0.65, style=KIStyle.EUROPEAN),
    )
    prov = ManualProvider(spots={"X": 100.0}, surfaces={"X": FlatVolSurface(0.25)}, rate=0.03)
    snap = assemble_snapshot(prov, ts, "2026-06-20")
    eng = MCEngine(config=MCConfig(n_paths=50_000, rng=RNGSpec(seed=20260620, method="pseudo")))
    pv = eng.price(ts, snap).pv
    assert abs(pv - 103.164042) < 0.05, f"golden PV drifted: {pv}"


def test_multi_asset_bb_residual_bias_is_small():
    """P0-3: per-asset Brownian-bridge ignores cross-asset crossing dependence. Quantify
    the residual by comparing daily vs 2x-finer grids at high correlation — it must stay
    small (the bridge already removes most discretization bias)."""
    def coupon(steps_per_year):
        ts = build_fcn(tickers=["A", "B"], notional=100.0, trade_date=TRADE, strike_date=TRADE,
                       maturity=MAT, coupon_rate=None, frequency=Frequency.QUARTERLY,
                       autocall_barrier=1.0, ki_barrier=0.65, ki_style=KIStyle.AMERICAN)
        prov = ManualProvider(
            spots={"A": 100.0, "B": 100.0},
            surfaces={t: ParametricSkewSurface(atm=0.30, slope=-0.5) for t in ["A", "B"]},
            rate=0.03, corr=Correlation.uniform(2, 0.7),
        )
        eng = MCEngine(config=MCConfig(n_paths=20_000, steps_per_year=steps_per_year,
                                       rng=RNGSpec(seed=7, method="pseudo")))
        return solve_coupon(eng, ts, assemble_snapshot(prov, ts, "2026-06-20"), 0.977).coupon_rate

    assert abs(coupon(252) - coupon(504)) < 0.005  # < 50bp coupon residual
