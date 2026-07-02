"""Closed-form prices used as Monte Carlo benchmarks.

The key validation (plan §5) is that a single-name, European-KI, fixed-coupon note
with no autocall replicates as a risk-free annuity + discounted par minus a
down-and-in put — and the MC engine must reproduce it within a couple of standard
errors. We derive the European (at-maturity barrier) note value directly, and also
expose the Reiner–Rubinstein *continuous*-barrier down-and-in put for validating
the American-KI Brownian-bridge correction later.

Convention: growth under the risk-neutral measure uses ``r - q - borrow``;
cashflows are discounted on the (possibly distinct) ``funding`` rate.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm


def black_scholes(
    spot: float, strike: float, t: float, sigma: float, r: float, q: float = 0.0, call: bool = True
) -> float:
    if t <= 0:
        return max(spot - strike, 0.0) if call else max(strike - spot, 0.0)
    d1 = (np.log(spot / strike) + (r - q + 0.5 * sigma**2) * t) / (sigma * np.sqrt(t))
    d2 = d1 - sigma * np.sqrt(t)
    if call:
        return spot * np.exp(-q * t) * norm.cdf(d1) - strike * np.exp(-r * t) * norm.cdf(d2)
    return strike * np.exp(-r * t) * norm.cdf(-d2) - spot * np.exp(-q * t) * norm.cdf(-d1)


def down_and_in_put_european(
    spot: float,
    strike: float,
    barrier: float,
    t: float,
    sigma: float,
    r: float,
    q: float = 0.0,
    borrow: float = 0.0,
) -> float:
    """European down-and-in put with the barrier observed *only at maturity*.

    Payoff ``(K - S_T) * 1_{S_T <= B}`` with ``B <= K``. Decomposes into a
    cash-or-nothing put (pays K) and an asset-or-nothing put, both struck at B.
    """
    mu = r - q - borrow
    sqt = sigma * np.sqrt(t)
    d1 = (np.log(spot / barrier) + (mu + 0.5 * sigma**2) * t) / sqt
    d2 = d1 - sqt
    cash = strike * np.exp(-r * t) * norm.cdf(-d2)
    asset = spot * np.exp(-(q + borrow) * t) * norm.cdf(-d1)
    return cash - asset


def reiner_rubinstein_down_in_put(
    spot: float,
    strike: float,
    barrier: float,
    t: float,
    sigma: float,
    r: float,
    q: float = 0.0,
    borrow: float = 0.0,
) -> float:
    """Continuous-barrier down-and-in put (Reiner–Rubinstein / Haug), for ``B <= K``.

    Down barrier (eta=+1), put (phi=-1), strike X >= barrier H: ``pdi = B - C + D``
    in Haug's block notation (rebate omitted). Validated against a Brownian-bridge
    continuous MC and the parity ``DI + DO == vanilla put``.
    """
    H, X = barrier, strike
    b = r - q - borrow  # cost of carry
    sqt = sigma * np.sqrt(t)
    mu = (b - 0.5 * sigma**2) / sigma**2
    eta, phi = 1.0, -1.0
    df_q = np.exp((b - r) * t)
    df_r = np.exp(-r * t)

    x2 = np.log(spot / H) / sqt + (1 + mu) * sqt
    y1 = np.log(H * H / (spot * X)) / sqt + (1 + mu) * sqt
    y2 = np.log(H / spot) / sqt + (1 + mu) * sqt
    pow_a = (H / spot) ** (2 * (mu + 1))
    pow_b = (H / spot) ** (2 * mu)

    B_blk = phi * spot * df_q * norm.cdf(phi * x2) - phi * X * df_r * norm.cdf(phi * x2 - phi * sqt)
    C_blk = (
        phi * spot * df_q * pow_a * norm.cdf(eta * y1)
        - phi * X * df_r * pow_b * norm.cdf(eta * y1 - eta * sqt)
    )
    D_blk = (
        phi * spot * df_q * pow_a * norm.cdf(eta * y2)
        - phi * X * df_r * pow_b * norm.cdf(eta * y2 - eta * sqt)
    )
    return float(B_blk - C_blk + D_blk)


def sharkfin_no_ko(
    spot: float, participation: float, cap: float | None, t: float,
    sigma: float, r: float, q: float = 0.0, notional: float = 100.0,
) -> float:
    """Closed-form SharkFin with the knock-out switched off (barrier -> infinity):
    principal-protected capped call-spread = par*e^{-rT} + participation * call-spread."""
    k1 = spot  # strike at 100%
    call1 = black_scholes(spot, k1, t, sigma, r, q, call=True)
    call2 = (
        black_scholes(spot, cap * spot, t, sigma, r, q, call=True) if cap is not None else 0.0
    )
    scale = notional / spot
    return notional * np.exp(-r * t) + participation * scale * (call1 - call2)


def booster_value(
    spot: float, participation: float, cap: float | None, buffer: float, t: float,
    sigma: float, r: float, q: float = 0.0, notional: float = 100.0,
) -> float:
    """Closed-form Booster = par*e^{-rT} + participation * call-spread(100%, cap)
    − put(100%-buffer) (the airbag)."""
    call1 = black_scholes(spot, spot, t, sigma, r, q, call=True)
    call2 = (
        black_scholes(spot, cap * spot, t, sigma, r, q, call=True) if cap is not None else 0.0
    )
    put_b = black_scholes(spot, (1.0 - buffer) * spot, t, sigma, r, q, call=False)
    scale = notional / spot
    return notional * np.exp(-r * t) + participation * scale * (call1 - call2) - scale * put_b


@dataclass(frozen=True)
class SingleNameNoteValue:
    pv: float
    pv_coupons: float
    pv_redemption: float


def single_name_european_note(
    spot: float,
    initial_fixing: float,
    ki_fraction: float,
    strike_fraction: float,
    sigma: float,
    r: float,
    q: float,
    borrow: float,
    funding: float,
    coupon_rate: float,
    coupon_times: list[float],
    coupon_taus: list[float],
    maturity: float,
    notional: float = 100.0,
) -> SingleNameNoteValue:
    """Closed-form value of a single-name, European-KI, fixed-coupon, no-autocall note.

    Redemption (non-called): par if ``S_T >= B``; else ``notional * S_T / K`` (cash
    conversion at strike). Coupons are guaranteed (no autocall), hence deterministic.
    """
    b_abs = ki_fraction * initial_fixing
    k_abs = strike_fraction * initial_fixing
    mu = r - q - borrow
    sqt = sigma * np.sqrt(maturity)
    d2 = (np.log(spot / b_abs) + (mu - 0.5 * sigma**2) * maturity) / sqt
    d1 = d2 + sqt
    fwd_T = spot * np.exp(mu * maturity)
    df_fund_T = np.exp(-funding * maturity)

    e_redemption = notional * norm.cdf(d2) + (notional / k_abs) * fwd_T * norm.cdf(-d1)
    pv_redemption = float(df_fund_T * e_redemption)

    pv_coupons = float(
        sum(
            notional * coupon_rate * tau * np.exp(-funding * t)
            for t, tau in zip(coupon_times, coupon_taus, strict=True)
        )
    )
    return SingleNameNoteValue(
        pv=pv_coupons + pv_redemption,
        pv_coupons=pv_coupons,
        pv_redemption=pv_redemption,
    )


def prob_below_barrier_european(
    spot: float,
    barrier_abs: float,
    sigma: float,
    t: float,
    r: float,
    q: float = 0.0,
    borrow: float = 0.0,
) -> float:
    """Risk-neutral P(S_T < barrier) at maturity (European observation) = ``N(-d2)``.

    The indicative *capital-at-risk* probability for a European-KI note: capital is
    impaired only if the (here single) underlying finishes below the protection
    barrier. Same ``d2`` as :func:`single_name_european_note`.
    """
    if t <= 0 or sigma <= 0:
        return float(spot < barrier_abs)
    mu = r - q - borrow
    sqt = sigma * np.sqrt(t)
    d2 = (np.log(spot / barrier_abs) + (mu - 0.5 * sigma**2) * t) / sqt
    return float(norm.cdf(-d2))
