"""finder_mc — a mini Monte-Carlo FCN screen for the Underlying Finder.

The closed-form screen (:mod:`fcn.service.ranking`) prices the no-autocall,
European-KI case exactly — but the Finder now mirrors the Quotation Desk's params
(敲出线 KO / 期限 / 敲入类型 / 敲入线 / 观察频率) and can rank by *coupon* or by
*strike*, so it needs autocall + American-KI + solve-strike support. Full
:class:`fcn.pricing.mcengine.MCEngine` runs 40-80k paths with local vol — far too
heavy for screening hundreds of names. This module is the honest middle ground:

  * single name, GBM at one **skew-aware vol sampled at the KI barrier** (the same
    sigma convention as the closed-form screen — downside accuracy matters most);
  * fixed seed + antithetic paths → **common random numbers across names**, so the
    *ranking* is stable even though each PV carries MC noise;
  * PV is affine in the coupon → exact one-division solve; solving the strike at a
    fixed coupon is a bisection over cached paths (only the payoff changes).

~1-3 ms per name for monthly observations; daily monitoring (American KI) stays
vectorised. Screen-grade only — open a name in the Desk for the 80k-path quote.
"""

from __future__ import annotations

import math

import numpy as np

_FREQ_PER_YEAR = {"monthly": 12, "quarterly": 4, "semiannual": 2, "annual": 1}
_N_PATHS = 4096          # antithetic pairs → 2048 independent draws
_SEED = 20260711         # fixed: common random numbers across candidates
_STRIKE_LO, _STRIKE_HI = 0.40, 1.20


def _simulate(sigma: float, drift_rate: float, tenor_years: float,
              obs_times: np.ndarray, daily: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Simulate GBM levels (S/S0). Returns (levels_at_obs, path_min, grid_times).

    ``daily=True`` monitors a ~daily grid (American KI); otherwise only the
    observation dates are simulated. ``path_min`` is the running minimum on the
    simulated grid (obs-only for European — unused there).
    """
    rng = np.random.default_rng(_SEED)
    if daily:
        n_steps = max(len(obs_times), int(round(252 * tenor_years)))
        grid = np.linspace(tenor_years / n_steps, tenor_years, n_steps)
    else:
        grid = obs_times
    dt = np.diff(np.concatenate([[0.0], grid]))
    half = _N_PATHS // 2
    z = rng.standard_normal((half, len(grid)))
    z = np.vstack([z, -z])                                   # antithetic
    incr = (drift_rate - 0.5 * sigma * sigma) * dt + sigma * np.sqrt(dt) * z
    logpath = np.cumsum(incr, axis=1)
    levels = np.exp(logpath)
    # map observation dates onto the grid (nearest index; grid ⊇ obs for daily)
    obs_idx = np.searchsorted(grid, obs_times - 1e-12)
    obs_idx = np.clip(obs_idx, 0, len(grid) - 1)
    return levels[:, obs_idx], levels.min(axis=1), grid


def screen_price(
    *,
    spot: float,
    sigma: float,
    rate: float,
    funding: float | None = None,
    div_yield: float = 0.0,
    borrow: float = 0.0,
    tenor_years: float,
    frequency: str = "monthly",
    ko: float | None = None,          # 敲出线 as fraction of start; None = no autocall
    ki: float = 0.65,                 # 敲入线 as fraction of start
    ki_style: str = "european",       # none | european | american   敲入类型
    strike: float = 1.0,              # 行权价 as fraction of start (coupon mode)
    reoffer: float = 1.0,             # target PV as fraction of par
    coupon_pa: float | None = None,   # fixed annual coupon → solve the strike instead
) -> dict | None:
    """Screen-price one name; solve the coupon (default) or the strike (``coupon_pa`` set).

    Returns ``{coupon | strike, prob_autocall, prob_capital_at_risk, expected_life,
    iv_at_barrier, infeasible|bracketed}`` per unit notional, or ``None`` when the
    inputs cannot price (degenerate schedule).
    """
    if spot <= 0 or sigma <= 0 or tenor_years <= 0:
        return None
    ppy = _FREQ_PER_YEAR.get(frequency, 12)
    n_obs = max(1, int(round(ppy * tenor_years)))
    obs_times = np.array([min((i + 1) / ppy, tenor_years) for i in range(n_obs)])
    obs_times[-1] = tenor_years
    tau = 1.0 / ppy

    # Barrier NONE (无保护) = downside starts at the strike itself, observed at maturity.
    ki_eff = strike if ki_style == "none" else ki
    style = "european" if ki_style == "none" else ki_style
    daily = style == "american"

    drift = rate - div_yield - borrow
    disc_rate = rate if funding is None else funding
    levels, path_min, _ = _simulate(sigma, drift, tenor_years, obs_times, daily)

    # autocall: first observation where the level closes at/above the KO barrier
    if ko is not None:
        hit = levels >= ko
        first = np.where(hit.any(axis=1), hit.argmax(axis=1), n_obs)   # n_obs = never called
    else:
        first = np.full(levels.shape[0], n_obs, dtype=int)
    called = first < n_obs
    call_t = np.where(called, obs_times[np.minimum(first, n_obs - 1)], tenor_years)

    df_obs = np.exp(-disc_rate * obs_times)
    # FCN coupons are unconditional and accrue per period until call/maturity
    cum_cpn = np.cumsum(tau * df_obs)                       # PV of coupons paid through obs i
    n_paid = np.where(called, first + 1, n_obs)             # called at obs i → i+1 coupons paid
    coupon_unit_pv = cum_cpn[n_paid - 1]

    terminal = levels[:, -1]
    ki_breached = (path_min < ki_eff) if daily else (terminal < ki_eff)

    def redemption_pv(k: float) -> np.ndarray:
        conv = (~called) & ki_breached & (terminal < k)
        red = np.where(conv, terminal / k, 1.0)             # conversion loss vs par
        return np.where(called, np.exp(-disc_rate * call_t), red * math.exp(-disc_rate * tenor_years))

    prob_autocall = float(called.mean())
    expected_life = float(np.where(called, call_t, tenor_years).mean())
    cpn_pv = float(coupon_unit_pv.mean())
    if cpn_pv <= 0:
        return None
    base = {
        "prob_autocall": round(prob_autocall, 4),
        "expected_life": round(expected_life, 4),
        "iv_at_barrier": round(float(sigma), 4),
        "buffer_pct": round(1.0 - ki_eff, 4),
    }

    if coupon_pa is None:
        red = float(redemption_pv(strike).mean())
        raw = (reoffer - red) / cpn_pv
        conv = (~called) & ki_breached & (terminal < strike)
        return {
            **base,
            "coupon": max(0.0, float(raw)),
            "infeasible": bool(raw < 0),
            "prob_capital_at_risk": round(float(conv.mean()), 4),
        }

    # solve the strike: PV(k) = E[red(k)] + coupon_pa * coupon_unit_pv is decreasing in k
    def pv(k: float) -> float:
        kk = max(k, 1e-6)
        red = redemption_pv(kk)
        if ki_style == "none":                              # KI moves with the strike
            conv = (~called) & (terminal < kk) & ((path_min < kk) if daily else True)
            red = np.where(called, np.exp(-disc_rate * call_t),
                           np.where(conv, terminal / kk, 1.0) * math.exp(-disc_rate * tenor_years))
        return float(red.mean()) + coupon_pa * cpn_pv

    lo, hi = _STRIKE_LO, _STRIKE_HI
    f_lo, f_hi = pv(lo) - reoffer, pv(hi) - reoffer
    if f_lo <= 0:      # even the lowest strike can't reach the target
        k, bracketed = lo, False
    elif f_hi >= 0:    # target below even the highest strike's PV
        k, bracketed = hi, False
    else:
        bracketed = True
        for _ in range(40):
            k = 0.5 * (lo + hi)
            if pv(k) - reoffer > 0:
                lo = k
            else:
                hi = k
            if hi - lo < 1e-3:
                break
        k = 0.5 * (lo + hi)
    conv = (~called) & ((terminal < k) if ki_style == "none" else (ki_breached & (terminal < k)))
    return {
        **base,
        "strike": round(float(k), 4),
        "bracketed": bracketed,
        "prob_capital_at_risk": round(float(conv.mean()), 4),
    }
