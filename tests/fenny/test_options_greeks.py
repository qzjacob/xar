"""Black–Scholes Greeks correctness gates.

These are the cheap, high-signal checks: put–call parity, the BS PDE identity
(theta + drift·S·delta + ½σ²S²gamma − rV = 0), IV roundtrip, and numerical
cross-checks of the second-order Greeks (vanna, vomma). All closed-form; no
network, no live data.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from fcn.options.greeks import (
    Greeks,
    bs_greeks,
    bs_price,
    delta_to_strike,
    implied_vol,
)


# --- put–call parity --------------------------------------------------------

def test_put_call_parity_price():
    S, K, T, sig, r, q = 100.0, 100.0, 1.0, 0.30, 0.04, 0.01
    c = float(bs_price(S, K, T, sig, r, q, kind="call"))
    p = float(bs_price(S, K, T, sig, r, q, kind="put"))
    # C − P = S·e^{−qT} − K·e^{−rT}
    expected = S * np.exp(-q * T) - K * np.exp(-r * T)
    assert abs((c - p) - expected) < 1e-9


def test_put_call_parity_delta():
    S, K, T, sig, r, q, b = 100.0, 95.0, 0.5, 0.25, 0.03, 0.005, 0.001
    gc = bs_greeks(S, K, T, sig, r, q, b, kind="call")
    gp = bs_greeks(S, K, T, sig, r, q, b, kind="put")
    # Δ_call − Δ_put = e^{−(q+b)T}
    diff = float(gc.delta) - float(gp.delta)
    assert abs(diff - np.exp(-(q + b) * T)) < 1e-9


# --- BS PDE identity --------------------------------------------------------

@pytest.mark.parametrize("kind", ["call", "put"])
@pytest.mark.parametrize("S,K", [(100, 100), (110, 100), (90, 100)])
def test_bs_pde_identity(kind, S, K):
    """Θ + (r−q−b)·S·Δ + ½σ²S²Γ − r·V = 0  (per year)."""
    T, sig, r, q, b = 0.5, 0.28, 0.04, 0.01, 0.002
    g = bs_greeks(S, K, T, sig, r, q, b, kind=kind)
    theta_yr = float(g.theta) * 365  # back to per-year
    drift = (r - q - b)
    resid = (theta_yr + drift * S * float(g.delta)
             + 0.5 * sig * sig * S * S * float(g.gamma)
             - r * float(g.price))
    assert abs(resid) < 1e-9


# --- second-order Greeks numerically ---------------------------------------

def test_vanna_matches_numerical():
    """Vanna = ∂Δ/∂σ = ∂Vega/∂S."""
    S, K, T, sig, r, q = 100.0, 105.0, 0.5, 0.30, 0.04, 0.005
    h = 1e-4
    g0 = bs_greeks(S, K, T, sig, r, q, kind="call")
    d_lo = bs_greeks(S, K, T, sig - h, r, q, kind="call")
    d_hi = bs_greeks(S, K, T, sig + h, r, q, kind="call")
    num_d_delta = (float(d_hi.delta) - float(d_lo.delta)) / (2 * h)
    v_lo = bs_greeks(S - h, K, T, sig, r, q, kind="call")
    v_hi = bs_greeks(S + h, K, T, sig, r, q, kind="call")
    num_d_vega_ds = (float(v_hi.vega) - float(v_lo.vega)) / (2 * h)
    assert abs(float(g0.vanna) - num_d_delta) < 1e-4
    assert abs(float(g0.vanna) - num_d_vega_ds) < 1e-4


def test_vomma_matches_numerical():
    """Vomma = ∂²V/∂σ²."""
    S, K, T, sig, r = 100.0, 100.0, 1.0, 0.25, 0.03
    h = 1e-3
    g0 = bs_greeks(S, K, T, sig, r, kind="call")
    g_lo = bs_greeks(S, K, T, sig - h, r, kind="call")
    g_hi = bs_greeks(S, K, T, sig + h, r, kind="call")
    num = (float(g_hi.price) - 2 * float(g0.price) + float(g_lo.price)) / (h * h)
    assert abs(float(g0.vomma) - num) < 1e-2


def test_charm_matches_numerical_calendar_convention():
    """Charm (per year, calendar convention) = −∂Δ/∂τ."""
    S, K, T, sig, r = 100.0, 100.0, 1.0, 0.30, 0.04
    h = 1e-4
    g0 = bs_greeks(S, K, T, sig, r, kind="call")
    g_lo = bs_greeks(S, K, T - h, sig, r, kind="call")
    g_hi = bs_greeks(S, K, T + h, sig, r, kind="call")
    num_d_delta_d_tau = (float(g_hi.delta) - float(g_lo.delta)) / (2 * h)
    # Per-year charm in calendar convention: −∂Δ/∂τ.
    assert abs(float(g0.charm) * 365 - (-num_d_delta_d_tau)) < 1e-3


# --- IV roundtrip & boundary cases -----------------------------------------

def test_iv_roundtrip_atm():
    S, K, T, r = 100.0, 100.0, 1.0, 0.04
    for target in (0.10, 0.20, 0.30, 0.50, 0.80):
        px = float(bs_price(S, K, T, target, r, kind="call"))
        iv = implied_vol(px, S, K, T, r, kind="call")
        assert iv is not None
        assert abs(iv - target) < 1e-6


def test_iv_returns_none_below_intrinsic():
    S, K, T, r = 100.0, 90.0, 1.0, 0.04
    # Call intrinsic-forward = S − K·e^{−rT} > 0 here; target below it → None.
    fwd_intr = max(S - K * np.exp(-r * T), 0.0)
    assert implied_vol(fwd_intr * 0.1, S, K, T, r, kind="call") is None


def test_iv_handles_put():
    S, K, T, r, target = 100.0, 110.0, 0.5, 0.04, 0.35
    px = float(bs_price(S, K, T, target, r, kind="put"))
    iv = implied_vol(px, S, K, T, r, kind="put")
    assert iv is not None and abs(iv - target) < 1e-6


# --- delta_to_strike inversion ---------------------------------------------

def test_delta_to_strike_call_and_put():
    S, T, sig, r = 100.0, 0.5, 0.30, 0.04
    for target_d in (0.20, 0.25, 0.40, 0.50):
        K = delta_to_strike(target_d, S, T, sig, r, kind="call")
        g = bs_greeks(S, K, T, sig, r, kind="call")
        assert abs(float(g.delta) - target_d) < 1e-4
    for target_d in (-0.20, -0.25, -0.40):
        K = delta_to_strike(target_d, S, T, sig, r, kind="put")
        g = bs_greeks(S, K, T, sig, r, kind="put")
        assert abs(float(g.delta) - target_d) < 1e-4


# --- vectorisation ----------------------------------------------------------

def test_greeks_vectorised():
    spots = np.array([90.0, 100.0, 110.0])
    g = bs_greeks(spots, 100.0, 1.0, 0.30, 0.04, kind="call")
    assert g.delta.shape == (3,)
    assert g.gamma.shape == (3,)
    # delta must be monotonically increasing in spot for a call.
    assert all(np.diff(g.delta) > 0)


# --- expiry boundary --------------------------------------------------------

def test_greeks_at_expiry_returns_intrinsic():
    g = bs_greeks(110.0, 100.0, 0.0, 0.30, 0.04, kind="call")
    assert math.isclose(float(g.price), 10.0)
    assert math.isclose(float(g.delta), 1.0)
    g_put = bs_greeks(90.0, 100.0, 0.0, 0.30, 0.04, kind="put")
    assert math.isclose(float(g_put.price), 10.0)
    assert math.isclose(float(g_put.delta), -1.0)
