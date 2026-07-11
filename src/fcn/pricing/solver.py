"""Solve-for-coupon.

Because PV is affine in the coupon rate (see payoff.py), the fair coupon is an
exact one-line solve given the Monte Carlo decomposition — no root finding, and
the same paths serve base and solved rate (perfect CRN). A Brent fallback remains
the right tool when solving a *non-linear* knob (a barrier or strike); that is a
Phase-2 addition.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fcn.marketdata.snapshot import MarketSnapshot
from fcn.pricing.mcengine import MCEngine
from fcn.pricing.results import PricingResult
from fcn.product.termsheet import TermSheet


@dataclass(frozen=True)
class SolveResult:
    coupon_rate: float
    coupon_rate_se: float
    reoffer_fraction: float
    pricing: PricingResult
    infeasible: bool = False   # True when the fair coupon would be < 0 (redemption already ≥ reoffer)


@dataclass(frozen=True)
class SolveStrikeResult:
    strike: float                 # conversion price as fraction of the initial fixing (1.0 = ATM)
    reoffer_fraction: float
    pricing: PricingResult
    bracketed: bool               # False = solution hit the [lo, hi] bound (target outside range)


def _with_strike(ts: TermSheet, s: float, couple_ki: bool) -> TermSheet:
    """A copy of ``ts`` with every underlying's strike set to ``s`` (and, when ``couple_ki``,
    the knock-in barrier moved with it — the Barrier-NONE case where the KI sits at the strike)."""
    ups = [u.model_copy(update={"strike": s}) for u in ts.underlyings]
    upd: dict = {"underlyings": ups}
    if couple_ki and ts.knock_in is not None:
        upd["knock_in"] = ts.knock_in.model_copy(update={"barrier": s})
    return ts.model_copy(update=upd)


def solve_strike(
    engine: MCEngine,
    ts: TermSheet,
    snapshot: MarketSnapshot,
    coupon_rate: float,
    reoffer_fraction: float,
    *,
    couple_ki: bool = False,
    lo: float = 0.50,
    hi: float = 1.20,
    max_iter: int = 18,
) -> SolveStrikeResult:
    """Find the strike (fraction of initial fixing) so that ``PV == reoffer_fraction * notional`` at
    the given coupon. PV is monotone DECREASING in strike (a higher strike = more downside handed to
    the holder = lower note value), so a bisection over [lo, hi] with common random numbers (the
    engine re-seeds identically each run → the strike only changes the payoff, not the paths →
    PV(strike) is a smooth deterministic curve) converges in ~log2 steps."""
    target_pv = reoffer_fraction * ts.notional
    tol = 1e-4 * ts.notional          # PV tolerance (tight — PV can be flat in strike near ATM)
    strike_tol = 2e-3                 # ...so also converge on strike width (0.2% of fixing)

    def price_at(s: float) -> PricingResult:
        return engine.aggregate(engine.run(_with_strike(ts, s, couple_ki), snapshot),
                                coupon_rate, ts.notional)

    plo, phi = price_at(lo), price_at(hi)
    # f(s) = PV(s) - target, decreasing. f(lo) >= 0 >= f(hi) expected; else the target lies outside
    # the achievable range → clamp to the nearest bound (bracketed=False so the caller can flag it).
    if plo.pv - target_pv <= 0:
        return SolveStrikeResult(strike=lo, reoffer_fraction=reoffer_fraction, pricing=plo, bracketed=False)
    if phi.pv - target_pv >= 0:
        return SolveStrikeResult(strike=hi, reoffer_fraction=reoffer_fraction, pricing=phi, bracketed=False)

    a, b, pricing = lo, hi, phi
    for _ in range(max_iter):
        m = 0.5 * (a + b)
        pricing = price_at(m)
        f = pricing.pv - target_pv
        if abs(f) < tol or (b - a) < strike_tol:
            return SolveStrikeResult(strike=m, reoffer_fraction=reoffer_fraction, pricing=pricing, bracketed=True)
        if f > 0:        # PV too high → note too safe → strike too low → raise the lower bound
            a = m
        else:
            b = m
    m = 0.5 * (a + b)
    return SolveStrikeResult(strike=m, reoffer_fraction=reoffer_fraction,
                             pricing=price_at(m), bracketed=True)


def solve_coupon(
    engine: MCEngine,
    ts: TermSheet,
    snapshot: MarketSnapshot,
    reoffer_fraction: float,
) -> SolveResult:
    """Find the annualised coupon rate so that ``PV == reoffer_fraction * notional``."""
    res = engine.run(ts, snapshot)
    base = float(res.redemption_pv.mean())
    factor = float(res.coupon_unit_pv.mean())
    if factor <= 0:
        raise ValueError("coupon factor is non-positive; cannot solve for coupon")
    target_pv = reoffer_fraction * ts.notional
    raw = (target_pv - base) / factor
    # Floor at zero: when the rate-independent redemption already exceeds the reoffer target (e.g.
    # low vol + a protective KI that autocalls almost immediately), the "fair" coupon is negative —
    # a nonsensical client quote. Report 0% + infeasible so the desk sees the structure doesn't work
    # at this reoffer, rather than a bogus negative coupon.
    infeasible = raw < 0.0
    rate = max(0.0, raw)

    # Standard error of the solved rate via the SE of the (base, factor) means.
    n = res.redemption_pv.size
    resid = res.redemption_pv + rate * res.coupon_unit_pv - target_pv
    rate_se = float(resid.std(ddof=1) / np.sqrt(n) / factor)

    pricing = engine.aggregate(res, rate, ts.notional)
    return SolveResult(
        coupon_rate=rate,
        coupon_rate_se=rate_se,
        reoffer_fraction=reoffer_fraction,
        pricing=pricing,
        infeasible=infeasible,
    )
