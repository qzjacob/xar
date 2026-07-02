"""Black–Scholes option Greeks — vectorised, with second-order Greeks.

Sits next to :func:`fcn.analytics.closed_form.black_scholes` (price-only) and
adds the full Greek set the options desk needs: delta, gamma, vega, theta, rho,
vanna (∂Δ/∂σ), vomma (∂V/∂σ²), charm (∂Δ/∂t). All are closed-form under
Black–Scholes with continuous dividend yield ``q`` and borrow ``b``; the
risk-neutral drift is ``mu = r − q − b``.

Conventions:
  * ``t`` is ACT/365 years (positive; t=0 returns intrinsic to keep callers branch-free)
  * ``theta`` is per *calendar day* (divided by 365); UI shows it as "per day"
  * ``vega`` is ∂V/∂σ for σ in absolute units (1.00 = 100%); UI divides by 100
    to display per-vol-point
  * signs follow the long-call convention; shorts flip via the leg ``quantity``

Correctness gates (``tests/test_options_greeks.py``):
  * put–call parity for Δ and price
  * Black–Scholes PDE: ``0.5 σ² S² Γ + μ S Δ − r V = −θ_per_year`` (residual < 1e-10)
  * vanna = ∂Δ/∂σ = ∂Vega/∂S, vomma = ∂Vega/∂σ (numerically re-derived)
  * numerical-difference cross-check at non-degenerate points
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy.stats import norm

Kind = Literal["call", "put"]


@dataclass(frozen=True)
class Greeks:
    """Full Black–Scholes Greek set for one option (or vectorised array)."""

    price: np.ndarray
    delta: np.ndarray
    gamma: np.ndarray
    vega: np.ndarray
    theta: np.ndarray          # per calendar day
    rho: np.ndarray            # per 1.00 (absolute) rate unit
    vanna: np.ndarray
    vomma: np.ndarray
    charm: np.ndarray          # per calendar day

    def to_dict(self) -> dict:
        return {
            "price": _clean(self.price), "delta": _clean(self.delta),
            "gamma": _clean(self.gamma), "vega": _clean(self.vega),
            "theta": _clean(self.theta), "rho": _clean(self.rho),
            "vanna": _clean(self.vanna), "vomma": _clean(self.vomma),
            "charm": _clean(self.charm),
        }


def _clean(x: np.ndarray) -> float | list:
    """Return python float for scalar arrays, else a rounded list (UI-friendly)."""
    arr = np.asarray(x)
    if arr.ndim == 0:
        return float(arr)
    return arr.tolist()


def _d1_d2(spot, strike, t, sigma, mu):
    """Standard ``d1``, ``d2`` with cost-of-carry ``mu = r − q − b``."""
    spot = np.asarray(spot, dtype=float)
    strike = np.asarray(strike, dtype=float)
    t = np.asarray(t, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    sqt = sigma * np.sqrt(t)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(spot / strike) + (mu + 0.5 * sigma**2) * t) / sqt
    d2 = d1 - sqt
    return d1, d2


def bs_price(
    spot: float | np.ndarray, strike: float | np.ndarray, t: float | np.ndarray,
    sigma: float | np.ndarray, r: float, q: float = 0.0, borrow: float = 0.0,
    kind: Kind = "call",
) -> np.ndarray:
    """Black–Scholes price (vectorised, mirrors
    :func:`fcn.analytics.closed_form.black_scholes` with a separate ``borrow``)."""
    mu = r - q - borrow
    spot, strike, t, sigma = (np.asarray(x, dtype=float) for x in (spot, strike, t, sigma))
    is_call = kind == "call"
    intrinsic = np.where(is_call, np.maximum(spot - strike, 0.0), np.maximum(strike - spot, 0.0))
    safe = t > 1e-9
    if not np.any(safe):
        return intrinsic
    d1, d2 = _d1_d2(spot, strike, t, sigma, mu)
    df_r = np.exp(-r * t)
    df_q = np.exp(-(q + borrow) * t)
    call = spot * df_q * norm.cdf(d1) - strike * df_r * norm.cdf(d2)
    put = strike * df_r * norm.cdf(-d2) - spot * df_q * norm.cdf(-d1)
    out = call if is_call else put
    return np.where(safe, out, intrinsic)


def bs_greeks(
    spot: float | np.ndarray, strike: float | np.ndarray, t: float | np.ndarray,
    sigma: float | np.ndarray, r: float, q: float = 0.0, borrow: float = 0.0,
    kind: Kind = "call",
) -> Greeks:
    """Full Black–Scholes Greeks for a call or put.

    ``t`` is in years (ACT/365); returned ``theta``/``charm`` are *per calendar
    day* (÷365); ``vega``/``vanna``/``vomma``/``rho`` are per absolute unit (1.00).
    For a *short* position, multiply price and every Greek by ``−1``.
    """
    mu = r - q - borrow
    spot = np.asarray(spot, dtype=float)
    strike = np.asarray(strike, dtype=float)
    t_arr = np.asarray(t, dtype=float)
    sigma = np.asarray(sigma, dtype=float)

    # t -> 0 intrinsic branch (keeps arrays finite at expiry for payoff scans).
    intrinsic = np.where(
        kind == "call",
        np.maximum(spot - strike, 0.0),
        np.maximum(strike - spot, 0.0),
    )
    safe = t_arr > 1e-9
    t_safe = np.where(safe, t_arr, 1.0)  # avoid 0/0 in d1/d2; result is overwritten

    d1, d2 = _d1_d2(spot, strike, t_safe, sigma, mu)
    pdf = norm.pdf(d1)
    df_r = np.exp(-r * t_safe)
    df_q = np.exp(-(q + borrow) * t_safe)
    sqt = sigma * np.sqrt(t_safe)

    # 1st order
    if kind == "call":
        price = spot * df_q * norm.cdf(d1) - strike * df_r * norm.cdf(d2)
        delta = df_q * norm.cdf(d1)
        rho = np.where(safe, strike * t_safe * df_r * norm.cdf(d2), 0.0)
    else:
        price = strike * df_r * norm.cdf(-d2) - spot * df_q * norm.cdf(-d1)
        delta = -df_q * norm.cdf(-d1)
        rho = np.where(safe, -strike * t_safe * df_r * norm.cdf(-d2), 0.0)

    # 2nd order (same for call/put)
    gamma = df_q * pdf / (spot * sqt) / np.where(safe, 1.0, np.inf)
    gamma = np.where(safe, gamma, 0.0)
    vega = spot * df_q * pdf * np.sqrt(t_safe)
    vega = np.where(safe, vega, 0.0)
    vanna = -df_q * pdf * d2 / sigma
    vanna = np.where(safe, vanna, 0.0)
    vomma = vega * d1 * d2 / sigma
    vomma = np.where(safe, vomma, 0.0)

    # Theta (per calendar day). Sign convention: long-option theta ≤ 0.
    if kind == "call":
        theta_yr = (
            -(spot * df_q * pdf * sigma) / (2.0 * np.sqrt(t_safe))
            - r * strike * df_r * norm.cdf(d2)
            + (q + borrow) * spot * df_q * norm.cdf(d1)
        )
    else:
        theta_yr = (
            -(spot * df_q * pdf * sigma) / (2.0 * np.sqrt(t_safe))
            + r * strike * df_r * norm.cdf(-d2)
            - (q + borrow) * spot * df_q * norm.cdf(-d1)
        )
    theta_day = np.where(safe, theta_yr, 0.0) / 365.0

    # Charm (∂Δ/∂t per day).
    if kind == "call":
        charm_yr = -df_q * (
            pdf * (2 * (r - q - borrow) * t_safe - d2 * sqt) / (2.0 * t_safe * sqt)
            - (q + borrow) * norm.cdf(d1)
        )
    else:
        charm_yr = -df_q * (
            pdf * (2 * (r - q - borrow) * t_safe - d2 * sqt) / (2.0 * t_safe * sqt)
            + (q + borrow) * norm.cdf(-d1)
        )
    charm_day = np.where(safe, charm_yr, 0.0) / 365.0

    price = np.where(safe, price, intrinsic)
    delta = np.where(safe, delta, np.where(kind == "call", (spot > strike).astype(float), -(spot < strike).astype(float)))

    return Greeks(
        price=price, delta=delta, gamma=gamma, vega=vega,
        theta=theta_day, rho=rho, vanna=vanna, vomma=vomma, charm=charm_day,
    )


def implied_vol(
    target: float, spot: float, strike: float, t: float, r: float,
    q: float = 0.0, borrow: float = 0.0, kind: Kind = "call",
    tol: float = 1e-6, max_iter: int = 100,
) -> float | None:
    """Black–Scholes implied vol via safeguarded Newton (bisection fallback).

    Price is strictly increasing in σ between intrinsic and the forward, so we
    bracket the root on ``[lo, hi]`` and take a Newton step only when it stays
    inside the bracket, otherwise bisect. This converges for *every* strike,
    including deep-OTM wings where the plain Brenner-Subrahmanyam Newton seed
    diverged. Returns ``None`` only when the target is genuinely unreachable
    (below intrinsic or above the σ≈5 cap) — the caller decides the fallback.
    """
    if t <= 0 or target <= 0:
        return None
    df_r = np.exp(-r * t)
    df_q = np.exp(-(q + borrow) * t)
    intrinsic = max(spot * df_q - strike * df_r, 0.0) if kind == "call" \
        else max(strike * df_r - spot * df_q, 0.0)
    if target < intrinsic - 1e-9:
        return None

    def f(sigma: float) -> float:
        return float(np.asarray(bs_price(spot, strike, t, sigma, r, q, borrow, kind)).item()) - target

    lo, hi = 1e-4, 5.0
    f_lo, f_hi = f(lo), f(hi)
    if f_lo > 0:           # target below the σ→0 (intrinsic) floor
        return None
    if f_hi < 0:           # target above what 500% vol can produce
        return None
    # Seed near ATM but clamped into the bracket.
    sigma = min(max(np.sqrt(2 * np.pi / t) * target / spot, 0.10), 4.0)
    if not (lo < sigma < hi):
        sigma = 0.5 * (lo + hi)
    for _ in range(max_iter):
        fv = f(sigma)
        if abs(fv) < tol:
            return float(sigma)
        if fv > 0:
            hi = sigma
        else:
            lo = sigma
        vega_val = float(np.asarray(
            bs_greeks(spot, strike, t, sigma, r, q, borrow, kind).vega).item())
        if vega_val > 1e-10:
            step = sigma - fv / vega_val
        else:
            step = 0.5 * (lo + hi)
        # Keep the iterate strictly inside the bracket; bisect if Newton escapes.
        if not (lo < step < hi):
            step = 0.5 * (lo + hi)
        sigma = float(step)
    return float(sigma) if abs(f(sigma)) < 1e-3 else None


def delta_to_strike(
    delta: float, spot: float, t: float, sigma: float, r: float,
    q: float = 0.0, borrow: float = 0.0, kind: Kind = "call",
    tol: float = 1e-6, max_iter: int = 50,
) -> float:
    """Invert the Black–Scholes delta to find the strike with that delta.

    Used to translate "25Δ put" into a concrete strike for the strategy builder
    and advisor. Sticky-moneyness approximation seeds a Newton solve on delta(K).
    """
    mu = r - q - borrow
    # Seed: delta ≈ N(d1) for calls (ignores df_q factor close to 1) → invert
    seed_d1 = norm.ppf(delta) if kind == "call" else -norm.ppf(-delta)
    sqt = sigma * np.sqrt(t)
    strike = spot * np.exp(mu * t + 0.5 * sigma**2 * t - seed_d1 * sqt)
    for _ in range(max_iter):
        g = bs_greeks(spot, strike, t, sigma, r, q, borrow, kind)
        d = float(np.asarray(g.delta).item())
        # ∂Δ/∂K for a call = −df_q · φ(d1) / (K σ √t);  put = call − 1 (same slope)
        d1 = (np.log(spot / strike) + (mu + 0.5 * sigma**2) * t) / sqt
        slope = -np.exp(-(q + borrow) * t) * norm.pdf(d1) / (strike * sqt)
        if abs(slope) < 1e-12:
            break
        if abs(d - delta) < tol:
            return float(strike)
        strike_new = strike - (d - delta) / slope
        if strike_new <= 0:
            break
        strike = float(strike_new)
    return float(strike)
