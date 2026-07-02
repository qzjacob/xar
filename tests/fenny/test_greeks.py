"""Greeks: sign/shape sanity and CRN stability."""

from __future__ import annotations

from datetime import date

from fcn.core.rng import RNGSpec
from fcn.marketdata.correlation import Correlation
from fcn.marketdata.provider import ManualProvider, assemble_snapshot
from fcn.marketdata.volsurface import ParametricSkewSurface
from fcn.pricing.greeks import GreeksEngine
from fcn.pricing.mcengine import MCConfig, MCEngine
from fcn.product.enums import CouponType, Frequency, KIStyle
from fcn.product.termsheet import CouponSpec, KnockInSpec, TermSheet, Underlying

ENGINE = MCEngine(config=MCConfig(n_paths=40_000, rng=RNGSpec(seed=11, method="pseudo")))


def _single_fcn(coupon=0.10):
    ts = TermSheet(
        notional=100.0, trade_date=date(2026, 6, 18), strike_date=date(2026, 6, 18),
        maturity=date(2027, 6, 18), underlyings=[Underlying(ticker="AAA")], autocall=None,
        coupon=CouponSpec(type=CouponType.FIXED, rate=coupon, frequency=Frequency.QUARTERLY),
        knock_in=KnockInSpec(barrier=0.65, style=KIStyle.EUROPEAN),
    )
    provider = ManualProvider(
        spots={"AAA": 100.0}, surfaces={"AAA": ParametricSkewSurface(atm=0.30, slope=-0.5)}, rate=0.03
    )
    return ts, assemble_snapshot(provider, ts, "2026-06-18")


def test_greeks_signs():
    ts, snap = _single_fcn()
    g = GreeksEngine(ENGINE).compute(ts, snap, coupon_rate=0.10)
    assert g.delta[0] > 0  # long the underlying via the short put -> positive delta
    assert g.vega[0] < 0  # short vol product -> negative vega
    assert g.rho != 0.0


def test_greeks_crn_stable():
    """With CRN, the bumped-minus-base difference has tiny SE relative to the value."""
    ts, snap = _single_fcn()
    g = GreeksEngine(ENGINE).compute(ts, snap, coupon_rate=0.10)
    delta_se = g.se["delta"][0]
    assert delta_se < 0.05 * abs(g.delta[0])  # CRN keeps the estimator sharp


def test_correlation_greek_positive_for_worst_of():
    ts = TermSheet(
        notional=100.0, trade_date=date(2026, 6, 18), strike_date=date(2026, 6, 18),
        maturity=date(2027, 6, 18),
        underlyings=[Underlying(ticker="AAA"), Underlying(ticker="BBB")], autocall=None,
        coupon=CouponSpec(type=CouponType.FIXED, rate=0.12, frequency=Frequency.QUARTERLY),
        knock_in=KnockInSpec(barrier=0.65, style=KIStyle.EUROPEAN),
    )
    provider = ManualProvider(
        spots={"AAA": 100.0, "BBB": 100.0},
        surfaces={t: ParametricSkewSurface(atm=0.30, slope=-0.5) for t in ["AAA", "BBB"]},
        rate=0.03, corr=Correlation.uniform(2, 0.5),
    )
    snap = assemble_snapshot(provider, ts, "2026-06-18")
    g = GreeksEngine(ENGINE).compute(ts, snap, coupon_rate=0.12)
    assert g.corr_sens > 0  # worst-of note value rises with correlation
