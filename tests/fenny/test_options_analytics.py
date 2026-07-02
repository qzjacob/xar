"""IV-surface analytics: deterministic checks on a known surface."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from fcn.marketdata.volsurface import FlatVolSurface, ParametricSkewSurface
from fcn.options.analytics import (
    SurfaceAnalytics,
    _term_label,
    _vol_regime,
    analyze_surface,
)


# --- flat surface: skew/RR/BF all ~0 ---------------------------------------

def test_flat_surface_has_zero_risk_reversal():
    a = analyze_surface(FlatVolSurface(0.25), ticker="X", spot=100.0, rate=0.04)
    assert abs(a.risk_reversal_25d_3m) < 1e-9
    assert abs(a.butterfly_25d_3m) < 1e-9
    assert abs(a.skew_90_3m) < 1e-9
    assert a.term_structure == "flat"


def test_flat_surface_term_slope_zero():
    a = analyze_surface(FlatVolSurface(0.25), ticker="X", spot=100.0, rate=0.04)
    assert abs(a.term_slope_1y_1m) < 1e-9


# --- equity skew surface ---------------------------------------------------

def test_equity_skew_negative_risk_reversal():
    """A put-skew surface (negative slope) → negative RR (puts pricier than calls)."""
    surf = ParametricSkewSurface(atm=0.30, slope=-0.4, curv=0.3)
    a = analyze_surface(surf, ticker="X", spot=100.0, rate=0.04)
    assert a.risk_reversal_25d_3m < 0
    assert a.risk_reversal_10d_3m < a.risk_reversal_25d_3m  # deeper wing → bigger
    assert a.skew_90_3m > 0                   # 90% strike > ATM vol


def test_atm_term_structure_at_standard_tenors():
    surf = ParametricSkewSurface(atm=0.30, slope=-0.4, curv=0.3)
    a = analyze_surface(surf, ticker="X", spot=100.0, rate=0.04)
    assert len(a.atm_term) == 6
    # Parametric surface has flat ATM term.
    for _, vol in a.atm_term:
        assert abs(vol - 0.30) < 1e-9


def test_term_slope_label():
    assert _term_label(0.02) == "contango"
    assert _term_label(-0.02) == "backwardated"
    assert _term_label(0.0) == "flat"


# --- vol regime mapping ----------------------------------------------------

@pytest.mark.parametrize("pctile, expected", [
    (5, "depressed"), (20, "low"), (50, "normal"), (80, "high"), (95, "extreme"),
])
def test_vol_regime_from_percentile(pctile, expected):
    assert _vol_regime(0.30, pctile) == expected


@pytest.mark.parametrize("iv, expected", [
    (0.10, "depressed"), (0.18, "low"), (0.28, "normal"), (0.45, "high"), (0.65, "extreme"),
])
def test_vol_regime_from_absolute(iv, expected):
    assert _vol_regime(iv, None) == expected


# --- realized vol from history ---------------------------------------------

def test_realized_vol_from_history():
    rng = np.random.default_rng(42)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.012, 252)))  # ~19% annualised
    a = analyze_surface(FlatVolSurface(0.25), ticker="X", spot=float(closes[-1]),
                        rate=0.04, history=closes)
    assert a.realized_21d is not None
    assert 0.10 < a.realized_21d < 0.30
    assert a.iv_rv_gap is not None
    # IV (0.25) > RV (~0.19) → positive gap.
    assert a.iv_rv_gap > 0


def test_short_history_returns_none_for_rv():
    a = analyze_surface(FlatVolSurface(0.25), ticker="X", spot=100.0, rate=0.04,
                        history=np.array([100.0, 101.0, 99.0]))
    assert a.realized_21d is None
    assert a.iv_rv_gap is None


def test_vol_percentile_in_0_100():
    rng = np.random.default_rng(7)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, 300)))
    a = analyze_surface(FlatVolSurface(0.30), ticker="X", spot=float(closes[-1]),
                        rate=0.04, history=closes)
    assert a.vol_1y_percentile is not None
    assert 0.0 <= a.vol_1y_percentile <= 100.0


# --- serialization ---------------------------------------------------------

def test_to_dict_roundtrips():
    a = analyze_surface(FlatVolSurface(0.25), ticker="X", spot=100.0, rate=0.04)
    d = a.to_dict()
    assert d["ticker"] == "X"
    assert "wing_marks" in d and "ATM" in d["wing_marks"]
    assert d["vol_regime"] in ("depressed", "low", "normal", "high", "extreme")
