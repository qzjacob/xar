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
    rate = (target_pv - base) / factor

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
    )
