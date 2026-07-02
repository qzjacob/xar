"""Monte Carlo engine: orchestrate path generation, payoff evaluation, aggregation."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from fcn.core.rng import RNGSpec
from fcn.marketdata.snapshot import MarketSnapshot
from fcn.pricing.grid import build_grid
from fcn.pricing.pathgen import GBMPathGenerator
from fcn.pricing.payoff import PayoffEngine, PayoffResult
from fcn.pricing.results import PricingResult
from fcn.product.termsheet import TermSheet


@dataclass(frozen=True)
class MCConfig:
    n_paths: int = 100_000
    steps_per_year: int = 252
    rng: RNGSpec = field(default_factory=RNGSpec)
    local_vol: bool = True  # Dupire local vol (institution-grade); False = sticky-moneyness proxy


class MCEngine:
    def __init__(
        self, pathgen: GBMPathGenerator | None = None, config: MCConfig | None = None
    ) -> None:
        self.config = config or MCConfig()
        self.pathgen = pathgen or GBMPathGenerator(local_vol=self.config.local_vol)

    def run(self, ts: TermSheet, snapshot: MarketSnapshot) -> PayoffResult:
        """Generate paths and evaluate the rate-independent payoff decomposition."""
        grid = build_grid(ts, steps_per_year=self.config.steps_per_year)
        bundle = self.pathgen.generate(snapshot, grid, self.config.rng, self.config.n_paths)
        spec = PayoffEngine.compile(ts, snapshot, grid)
        return PayoffEngine.evaluate(bundle, spec)

    def price(
        self, ts: TermSheet, snapshot: MarketSnapshot, coupon_rate: float | None = None
    ) -> PricingResult:
        res = self.run(ts, snapshot)
        rate = coupon_rate if coupon_rate is not None else (ts.coupon.rate or 0.0)
        return self.aggregate(res, rate, ts.notional)

    def aggregate(self, res: PayoffResult, rate: float, notional: float) -> PricingResult:
        per_path = res.redemption_pv + rate * res.coupon_unit_pv
        n = per_path.size
        pv = float(per_path.mean())
        se = float(per_path.std(ddof=1) / np.sqrt(n))
        method = f"{self.config.rng.method}"
        if self.config.rng.method == "pseudo" and self.config.rng.antithetic:
            method += "+antithetic"

        counts, edges = np.histogram(res.worst_of_terminal, bins=48, range=(0.0, 2.0), density=True)
        centers = 0.5 * (edges[:-1] + edges[1:])
        wo_hist = {"x": [round(float(c), 4) for c in centers], "p": [round(float(v), 4) for v in counts]}
        return PricingResult(
            notional=notional,
            coupon_rate=rate,
            pv=pv,
            pv_se=se,
            price_pct=100.0 * pv / notional,
            redemption_pv=float(res.redemption_pv.mean()),
            coupon_factor=float(res.coupon_unit_pv.mean()),
            prob_autocall=float(res.called.mean()),
            prob_knock_in=float(res.p_ki.mean()),
            expected_life=float(res.exit_time.mean()),
            n_paths=n,
            method=method,
            wo_hist=wo_hist,
        )
