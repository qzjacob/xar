"""Unit tests for core utilities and market-data primitives."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from fcn.core.calendar import periodic_schedule, roll
from fcn.core.daycount import DayCount, year_fraction
from fcn.core.rng import RNGSpec, standard_normals
from fcn.marketdata.correlation import Correlation
from fcn.marketdata.curve import DiscountCurve, ForwardCurve
from fcn.marketdata.volsurface import ParametricSkewSurface


def test_year_fraction_act365():
    assert year_fraction(date(2026, 1, 1), date(2027, 1, 1), DayCount.ACT_365F) == pytest.approx(
        365 / 365
    )


def test_roll_weekend_forward():
    # 2026-06-20 is a Saturday -> rolls to Monday 2026-06-22.
    assert roll(date(2026, 6, 20)) == date(2026, 6, 22)


def test_periodic_schedule_quarterly_endpoint():
    sched = periodic_schedule(date(2026, 6, 18), date(2027, 6, 18), 3)
    assert len(sched) == 4
    assert sched[-1] == date(2027, 6, 18)


def test_rng_crn_identical():
    spec = RNGSpec(seed=7, method="pseudo", antithetic=True)
    a = standard_normals(spec, 1000, 5, 2)
    b = standard_normals(spec, 1000, 5, 2)
    assert np.array_equal(a, b)


def test_rng_antithetic_zero_mean():
    spec = RNGSpec(seed=7, method="pseudo", antithetic=True)
    z = standard_normals(spec, 2000, 4, 1)
    assert abs(z.mean()) < 1e-12  # antithetic pairs cancel exactly


def test_correlation_psd_repair():
    bad = np.array([[1.0, 0.95, -0.9], [0.95, 1.0, 0.95], [-0.9, 0.95, 1.0]])
    corr = Correlation(bad)
    eigvals = np.linalg.eigvalsh(corr.matrix)
    assert (eigvals > -1e-10).all()
    np.testing.assert_allclose(np.diag(corr.matrix), 1.0)


def test_forward_curve_growth():
    fwd = ForwardCurve(spot=100.0, rate=0.05, div_yield=0.02, borrow=0.0)
    assert fwd.forward(1.0) == pytest.approx(100.0 * np.exp(0.03))
    assert fwd.log_drift(0.0, 1.0) == pytest.approx(0.03)


def test_parametric_skew_put_wing_higher():
    surf = ParametricSkewSurface(atm=0.25, slope=-0.4)
    put_wing = surf.implied_vol(np.array([-0.2]), 1.0)[0]  # K < F
    call_wing = surf.implied_vol(np.array([0.2]), 1.0)[0]  # K > F
    assert put_wing > call_wing  # equity put skew


def test_discount_curve():
    disc = DiscountCurve(rate=0.04)
    assert float(disc.df(2.0)) == pytest.approx(np.exp(-0.08))
