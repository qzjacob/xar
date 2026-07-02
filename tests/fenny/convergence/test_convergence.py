"""MC convergence and reproducibility (plan §5)."""

from __future__ import annotations

from datetime import date

import numpy as np

from fcn.core.rng import RNGSpec
from fcn.marketdata.provider import ManualProvider, assemble_snapshot
from fcn.marketdata.volsurface import FlatVolSurface
from fcn.pricing.mcengine import MCConfig, MCEngine
from fcn.product.enums import CouponType, Frequency, KIStyle
from fcn.product.termsheet import CouponSpec, KnockInSpec, TermSheet, Underlying


def _setup():
    ts = TermSheet(
        notional=100.0, trade_date=date(2026, 6, 18), strike_date=date(2026, 6, 18),
        maturity=date(2027, 6, 18), underlyings=[Underlying(ticker="AAA")], autocall=None,
        coupon=CouponSpec(type=CouponType.FIXED, rate=0.08, frequency=Frequency.QUARTERLY),
        knock_in=KnockInSpec(barrier=0.65, style=KIStyle.EUROPEAN),
    )
    provider = ManualProvider(spots={"AAA": 100.0}, surfaces={"AAA": FlatVolSurface(0.25)}, rate=0.03)
    return ts, assemble_snapshot(provider, ts, "2026-06-18")


def test_standard_error_shrinks_with_sqrt_n():
    ts, snap = _setup()
    ns = [10_000, 40_000, 160_000]
    ses = []
    for n in ns:
        eng = MCEngine(config=MCConfig(n_paths=n, rng=RNGSpec(method="pseudo", antithetic=False)))
        ses.append(eng.price(ts, snap).pv_se)
    # Each 4x in N should roughly halve the SE.
    assert ses[1] < ses[0]
    assert ses[2] < ses[1]
    ratio = ses[0] / ses[2]
    assert 2.5 < ratio < 6.0  # expected ~4x


def test_crn_reproducibility():
    ts, snap = _setup()
    eng = MCEngine(config=MCConfig(n_paths=20_000, rng=RNGSpec(seed=42, method="pseudo")))
    a = eng.price(ts, snap).pv
    b = eng.price(ts, snap).pv
    assert a == b  # bit-identical under common random numbers
