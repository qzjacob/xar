"""Vectorised payoff kernel — prices every variant from one code path.

The PV of these notes is **affine in the coupon rate**: the coupon cashflows scale
linearly with the rate while the redemption and all barrier indicators do not.
:meth:`PayoffEngine.evaluate` therefore returns, per path, the rate-independent
``redemption_pv`` and the ``coupon_unit_pv`` (PV of coupons at rate = 1). The price
at any rate ``c`` is ``mean(redemption_pv) + c * mean(coupon_unit_pv)``, which makes
solve-for-coupon exact (a single division, no root finding) and lets the solver and
barrier sensitivities reuse the cached path tensor.

Knock-in is returned as a per-path probability ``p_ki`` (a control-variate-style
smoothing): European KI is 0/1 at maturity; American KI combines the discrete
daily hit with a per-asset Brownian-bridge crossing probability between grid
points (continuous-monitoring bias correction, plan §2.5).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fcn.marketdata.snapshot import MarketSnapshot
from fcn.pricing.grid import TimeGrid
from fcn.pricing.pathgen import PathBundle
from fcn.product.enums import BasketMode, CouponType, KIStyle, Settlement
from fcn.product.termsheet import TermSheet


@dataclass(frozen=True)
class PayoffSpec:
    notional: float
    initial_fixing: np.ndarray  # (A,)
    strikes: np.ndarray  # (A,) fraction of fixing
    weights: np.ndarray  # (A,)
    worst_of: bool
    autocall_idx: np.ndarray
    ac_barriers: np.ndarray
    coupon_idx: np.ndarray
    coupon_tau: np.ndarray
    coupon_type: str
    coupon_barrier: float
    memory: bool
    snowball: bool
    ki_barrier: float
    ki_style: str
    settlement: str
    df_grid: np.ndarray  # (n_steps+1,)
    times: np.ndarray  # (n_steps+1,)
    maturity_idx: int
    ki_vol: np.ndarray  # (n_steps, A) surface vol at the KI-barrier moneyness (for the BB correction)
    part: "ParticipationParams | None" = None  # set -> SharkFin/Booster branch
    ko_vol: np.ndarray | None = None  # (n_steps,) vol at the KO barrier (SharkFin)


@dataclass(frozen=True)
class ParticipationParams:
    style: str  # "sharkfin" | "booster"
    participation: float
    cap: float  # cap level (np.inf if uncapped)
    coupon_floor: float
    ko_barrier: float
    ko_style: str
    buffer: float


@dataclass(frozen=True)
class PayoffResult:
    redemption_pv: np.ndarray  # (P,) rate-independent
    coupon_unit_pv: np.ndarray  # (P,) PV of coupons at rate = 1
    called: np.ndarray  # (P,) bool
    p_ki: np.ndarray  # (P,) knock-in probability
    exit_time: np.ndarray  # (P,) time of autocall or maturity
    worst_of_terminal: np.ndarray  # (P,) worst-of level at maturity (for the barrier ladder)


class PayoffEngine:
    @staticmethod
    def compile(ts: TermSheet, snapshot: MarketSnapshot, grid: TimeGrid) -> PayoffSpec:
        fixings = np.array([a.initial_fixing for a in snapshot.assets], dtype=float)
        strikes = np.array([u.strike for u in ts.underlyings], dtype=float)
        weights = np.array([u.weight for u in ts.underlyings], dtype=float)
        weights = weights / weights.sum()
        if ts.autocall is not None and grid.autocall_idx.size:
            ac_barriers = np.array(ts.autocall.barriers, dtype=float)
        else:
            ac_barriers = np.array([], dtype=float)
        if ts.knock_in is not None and ts.knock_in.style is KIStyle.AMERICAN:
            ki_vol = _barrier_vol_per_asset(snapshot, grid, ts.knock_in.barrier)
        else:
            ki_vol = np.zeros((max(grid.n_steps, 1), snapshot.n_assets))

        part = None
        ko_vol = None
        if ts.participation is not None:
            pp = ts.participation
            part = ParticipationParams(
                style=pp.style.value, participation=pp.participation,
                cap=(pp.cap if pp.cap is not None else np.inf),
                coupon_floor=pp.coupon_floor,
                ko_barrier=(pp.ko_barrier if pp.ko_barrier is not None else 0.0),
                ko_style=pp.ko_style.value, buffer=pp.buffer,
            )
            if pp.ko_barrier is not None and pp.ko_style is KIStyle.AMERICAN:
                ko_vol = _barrier_vol_per_asset(snapshot, grid, pp.ko_barrier).mean(axis=1)
        return PayoffSpec(
            notional=ts.notional,
            initial_fixing=fixings,
            strikes=strikes,
            weights=weights,
            worst_of=ts.basket_mode is BasketMode.WORST_OF,
            autocall_idx=grid.autocall_idx,
            ac_barriers=ac_barriers,
            coupon_idx=grid.coupon_idx,
            coupon_tau=grid.coupon_tau,
            coupon_type=ts.coupon.type.value,
            coupon_barrier=(ts.coupon.barrier if ts.coupon.barrier is not None else 0.0),
            memory=ts.coupon.memory,
            snowball=ts.coupon.accrual_snowball,
            ki_barrier=(ts.knock_in.barrier if ts.knock_in is not None else 0.0),
            ki_style=(ts.knock_in.style.value if ts.knock_in is not None else "none"),
            settlement=(ts.knock_in.settlement.value if ts.knock_in is not None else "cash"),
            df_grid=snapshot.disc.df(grid.times),
            times=grid.times,
            maturity_idx=grid.maturity_idx,
            ki_vol=ki_vol,
            part=part,
            ko_vol=ko_vol,
        )

    @staticmethod
    def evaluate(bundle: PathBundle, spec: PayoffSpec) -> PayoffResult:
        S = bundle.S
        n_paths = S.shape[0]
        perf = S / spec.initial_fixing[None, None, :]  # (P, K, A)
        if spec.worst_of:
            level = perf.min(axis=2)  # (P, K)
        else:
            level = (perf * spec.weights[None, None, :]).sum(axis=2)

        # Participation notes (SharkFin / Booster) are a separate principal-protected
        # family — evaluated here, leaving the FCN short-put path below untouched.
        if spec.part is not None:
            return _participation_payoff(level, spec)

        df = spec.df_grid
        mat = spec.maturity_idx

        # --- autocall & alive ---
        if spec.autocall_idx.size:
            wo_ac = level[:, spec.autocall_idx]
            ac_hit = wo_ac >= spec.ac_barriers[None, :]
            any_ac = ac_hit.any(axis=1)
            first_j = ac_hit.argmax(axis=1)
            call_idx = np.where(any_ac, spec.autocall_idx[first_j], mat)
            called = any_ac
        else:
            called = np.zeros(n_paths, dtype=bool)
            call_idx = np.full(n_paths, mat, dtype=int)
        exit_time = spec.times[call_idx]
        df_call = df[call_idx]

        # --- knock-in probability (BB uses the vol at the KI barrier, not ATM) ---
        p_ki = _knock_in_prob(perf, spec)

        # --- redemption (rate-independent) ---
        level_T = level[:, mat]
        # Loss converts at the WORST PERFORMER's own strike, not min(strikes).
        if spec.worst_of:
            worst_idx = perf[:, mat, :].argmin(axis=1)
            strike_path = spec.strikes[worst_idx]
        else:
            strike_path = np.full(n_paths, float((spec.strikes * spec.weights).sum()))
        below_strike = level_T < strike_path
        loss_val = spec.notional * level_T / strike_path
        red_nc = np.where(
            below_strike,
            (1.0 - p_ki) * spec.notional + p_ki * loss_val,
            spec.notional,
        )
        redemption_pv = np.where(called, spec.notional * df_call, red_nc * df[mat])

        # --- coupons at unit rate ---
        # Snowball accrual halt and the FCN coupon barrier are both defined on the
        # worst-of performance (consistent with _knock_in_prob), even when the
        # basket_mode is WEIGHTED — passing the worst-of perf to _coupon_unit_pv
        # keeps the two paths coherent.
        wo_perf = perf.min(axis=2)
        coupon_unit_pv = _coupon_unit_pv(level, wo_perf, called, call_idx, exit_time, p_ki, df, spec)

        return PayoffResult(
            redemption_pv=redemption_pv,
            coupon_unit_pv=coupon_unit_pv,
            called=called,
            p_ki=np.where(called, 0.0, p_ki),
            exit_time=exit_time,
            worst_of_terminal=level_T,
        )


def _barrier_vol_per_asset(
    snapshot: MarketSnapshot, grid: TimeGrid, barrier_frac: float
) -> np.ndarray:
    """Surface vol at the barrier moneyness, per step per asset (for BB corrections).

    The Brownian-bridge correction must use the vol that governs path behaviour
    *near the barrier* (the deep wing under skew), not ATM — ATM understates the
    crossing probability.
    """
    n_steps = grid.n_steps
    out = np.zeros((max(n_steps, 1), snapshot.n_assets))
    for a, asset in enumerate(snapshot.assets):
        barrier_price = barrier_frac * asset.initial_fixing
        for k in range(n_steps):
            t = float(grid.times[k])
            fwd = float(np.atleast_1d(asset.forward.forward(t))[0])
            logm = float(np.log(barrier_price / fwd))
            out[k, a] = float(asset.surface.implied_vol(np.array([logm]), t)[0])
    return out


def _barrier_crossing_prob(
    level: np.ndarray, spec: PayoffSpec, barrier: float, style: str, vol: np.ndarray, up: bool
) -> np.ndarray:
    """Continuous-monitoring crossing probability of the worst-of ``level`` through a
    single barrier (up or down), via discrete hit OR Brownian-bridge correction."""
    n_paths = level.shape[0]
    mat = spec.maturity_idx
    if barrier <= 0:
        return np.zeros(n_paths)
    if style == KIStyle.EUROPEAN.value:
        end = level[:, mat]
        return (end >= barrier).astype(float) if up else (end < barrier).astype(float)
    L = level[:, : mat + 1]
    s0, s1 = L[:, :-1], L[:, 1:]
    if up:
        disc = (L >= barrier).any(axis=1)
        both = (s0 < barrier) & (s1 < barrier)
    else:
        disc = (L < barrier).any(axis=1)
        both = (s0 > barrier) & (s1 > barrier)
    dt = np.diff(spec.times[: mat + 1])
    denom = vol[:mat] ** 2 * dt
    denom = np.where(denom <= 0, np.inf, denom)[None, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        arg = -2.0 * np.log(s0 / barrier) * np.log(s1 / barrier) / denom
    cross_p = np.where(both, np.exp(np.minimum(arg, 0.0)), 0.0)
    ln_no = np.log1p(-np.clip(cross_p, 0.0, 1.0 - 1e-15)).sum(axis=1)
    return np.where(disc, 1.0, 1.0 - np.exp(ln_no))


def _participation_payoff(level: np.ndarray, spec: PayoffSpec) -> PayoffResult:
    n_paths = level.shape[0]
    mat = spec.maturity_idx
    p = spec.part
    n = spec.notional
    df_T = spec.df_grid[mat]
    R = level[:, mat]
    up = np.minimum(np.maximum(R - 1.0, 0.0), p.cap - 1.0)  # cap-1 = inf if uncapped

    if p.style == "sharkfin":
        ko = _barrier_crossing_prob(level, spec, p.ko_barrier, p.ko_style, spec.ko_vol, up=True)
        # Knocked-out paths are paid the rebate *at the KO time*, not at maturity.
        # For paths with a discrete hit we discount from the actual first-hit index;
        # BB-only smoothed crossings are timing-ambiguous and fall back to df_T
        # (the conservative — issuer-favouring — choice).
        if p.ko_style == KIStyle.AMERICAN.value and p.ko_barrier > 0:
            L = level[:, : mat + 1]
            crossed = L >= p.ko_barrier
            disc_hit = crossed.any(axis=1)
            # first-passage index = first step at/above the KO barrier (argmax of the
            # boolean returns the first True). NOT argmax of the level, which would
            # return the peak — discounting an early KO from near maturity.
            first_hit = np.where(disc_hit, crossed.argmax(axis=1), mat)
            df_path = np.where(disc_hit, spec.df_grid[first_hit], df_T)
        else:
            df_path = np.full(n_paths, float(df_T))
        alive_df = np.where(ko >= 1.0 - 1e-12, df_path, df_T)  # only KO paths use early df
        redeem = (1.0 - ko) * n * (1.0 + p.participation * up) + ko * n * (1.0 + p.coupon_floor)
        risk = ko  # knock-out probability (lost participation)
    else:  # booster / airbag
        upside = n * p.participation * up
        redeem = np.where(
            R >= 1.0, n + upside,
            np.where(R >= 1.0 - p.buffer, float(n), n * (R + p.buffer)),
        ) + n * p.coupon_floor
        risk = (R < 1.0 - p.buffer).astype(float)  # probability of breaching the buffer
        alive_df = np.full(n_paths, float(df_T))

    return PayoffResult(
        redemption_pv=redeem * alive_df,
        coupon_unit_pv=np.zeros(n_paths),  # participation notes: return is in the payoff, not a rate
        called=np.zeros(n_paths, dtype=bool),
        p_ki=risk,
        exit_time=np.full(n_paths, float(spec.times[mat])),
        worst_of_terminal=R,
    )


def _knock_in_prob(perf: np.ndarray, spec: PayoffSpec) -> np.ndarray:
    n_paths = perf.shape[0]
    if spec.ki_style == "none" or spec.ki_barrier <= 0.0:
        return np.zeros(n_paths)
    B = spec.ki_barrier
    mat = spec.maturity_idx
    if spec.ki_style == KIStyle.EUROPEAN.value:
        wo_T = perf[:, mat, :].min(axis=1)
        return (wo_T < B).astype(float)

    # American: discrete daily hit OR per-asset Brownian-bridge crossing.
    s = perf[:, : mat + 1, :]
    disc_hit = (s < B).any(axis=(1, 2))
    s0 = s[:, :-1, :]
    s1 = s[:, 1:, :]
    dt = np.diff(spec.times[: mat + 1])
    vol = spec.ki_vol[:mat, :]
    denom = (vol * vol * dt[:, None])[None, :, :]
    denom = np.where(denom <= 0, np.inf, denom)
    both_above = (s0 > B) & (s1 > B)
    with np.errstate(divide="ignore", invalid="ignore"):
        arg = -2.0 * np.log(s0 / B) * np.log(s1 / B) / denom
    cross_p = np.where(both_above, np.exp(np.minimum(arg, 0.0)), 0.0)
    ln_no_cross = np.log1p(-np.clip(cross_p, 0.0, 1.0 - 1e-15)).sum(axis=(1, 2))
    p_no_ki = np.exp(ln_no_cross)
    return np.where(disc_hit, 1.0, 1.0 - p_no_ki)


def _coupon_unit_pv(
    level: np.ndarray,
    wo_perf: np.ndarray,
    called: np.ndarray,
    call_idx: np.ndarray,
    exit_time: np.ndarray,
    p_ki: np.ndarray,
    df: np.ndarray,
    spec: PayoffSpec,
) -> np.ndarray:
    n_paths = level.shape[0]
    mat = spec.maturity_idx

    if spec.snowball:
        # Snowball: coupon accrues until the FIRST of {autocall, KI, maturity}, paid at
        # exit. Uses the discrete first-passage KI time tau (not just the KI probability)
        # so accrual to tau is preserved — the part before knock-in is not discarded.
        # KI is always defined on worst-of (consistent with _knock_in_prob), so we use
        # ``wo_perf`` rather than ``level`` even when basket_mode is WEIGHTED.
        B = spec.ki_barrier
        breach = wo_perf[:, : mat + 1] < B
        has_breach = breach.any(axis=1)
        fp_idx = np.where(has_breach, breach.argmax(axis=1), mat)
        ki_time = np.where(has_breach, spec.times[fp_idx], np.inf)
        accr_end = np.minimum(np.minimum(exit_time, ki_time), spec.times[mat])
        return accr_end * spec.notional * df[call_idx]

    if spec.coupon_idx.size == 0:
        return np.zeros(n_paths)

    df_c = df[spec.coupon_idx]
    wo_c = level[:, spec.coupon_idx]
    alive_c = spec.coupon_idx[None, :] <= call_idx[:, None]
    coupon_cash_unit = spec.notional * spec.coupon_tau  # (n_c,) at rate = 1

    if spec.coupon_type == CouponType.FIXED.value:
        return (alive_c * coupon_cash_unit[None, :] * df_c[None, :]).sum(axis=1)

    meets = wo_c >= spec.coupon_barrier
    if not spec.memory:
        return ((alive_c & meets) * coupon_cash_unit[None, :] * df_c[None, :]).sum(axis=1)

    # Phoenix memory: accrue owed coupons; pay all owed when barrier next met.
    owed = np.zeros(n_paths)
    pv = np.zeros(n_paths)
    for i in range(spec.coupon_idx.size):
        owed = owed + alive_c[:, i] * coupon_cash_unit[i]
        do_pay = alive_c[:, i] & meets[:, i]
        pv = pv + np.where(do_pay, owed * df_c[i], 0.0)
        owed = np.where(do_pay, 0.0, owed)
    return pv
