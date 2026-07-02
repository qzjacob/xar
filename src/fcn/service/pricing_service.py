"""High-level facade: build a market snapshot, then quote / solve / scenario.

This is the single entry point the API, the Streamlit sandbox and the tests call,
so the wiring (provider + overrides -> snapshot -> engine) lives in one place.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fcn.marketdata.provider import MarketDataProvider, assemble_snapshot
from fcn.marketdata.snapshot import MarketSnapshot
from fcn.pricing.fees import FeeModel
from fcn.pricing.mcengine import MCConfig, MCEngine
from fcn.pricing.results import PricingResult
from fcn.pricing.solver import SolveResult, solve_coupon
from fcn.product.termsheet import TermSheet


@dataclass
class PricingService:
    engine: MCEngine
    fee_model: FeeModel | None = None

    def __post_init__(self) -> None:
        if self.fee_model is None:
            self.fee_model = FeeModel()

    @classmethod
    def default(cls, n_paths: int = 100_000) -> "PricingService":
        return cls(engine=MCEngine(config=MCConfig(n_paths=n_paths)))

    def snapshot(
        self, provider: MarketDataProvider, ts: TermSheet, asof: str
    ) -> MarketSnapshot:
        return assemble_snapshot(provider, ts, asof)

    def quote(
        self, ts: TermSheet, snapshot: MarketSnapshot, coupon_rate: float | None = None
    ) -> PricingResult:
        return self.engine.price(ts, snapshot, coupon_rate)

    def solve(self, ts: TermSheet, snapshot: MarketSnapshot) -> SolveResult:
        assert self.fee_model is not None  # set in __post_init__
        reoffer = self.fee_model.breakdown().reoffer_fraction
        return solve_coupon(self.engine, ts, snapshot, reoffer)

    def scenario_table(
        self,
        ts: TermSheet,
        snapshot: MarketSnapshot,
        coupon_rate: float,
        shocks=(-0.30, -0.20, -0.10, 0.0, 0.10),
    ) -> list[dict]:
        """Re-price under parallel spot shocks, pinning the initial fixing."""
        rows = []
        for shock in shocks:
            factors = {u.ticker: 1.0 + shock for u in ts.underlyings}
            res = self.quote(ts, snapshot.shock_spots(factors), coupon_rate)
            rows.append(
                {"shock": shock, "pv": res.pv, "price_pct": res.price_pct,
                 "prob_autocall": res.prob_autocall, "prob_knock_in": res.prob_knock_in}
            )
        return rows

    def payoff_diagram(
        self, ts: TermSheet, snapshot: MarketSnapshot, n: int = 61
    ) -> dict:
        """Terminal redemption vs worst-of level at maturity for the given product."""
        x = np.linspace(0.2, 1.5, n)
        notional = ts.notional
        if ts.participation is not None:
            p = ts.participation
            cap_up = (p.cap - 1.0) if p.cap is not None else np.inf
            up = np.minimum(np.maximum(x - 1.0, 0.0), cap_up)
            if p.style.value == "sharkfin":
                ko = p.ko_barrier or np.inf
                alive = notional * (1.0 + p.participation * up)
                redemption = np.where(x >= ko, notional * (1.0 + p.coupon_floor), alive)
                markers = {"ki": 0.0, "strike": 1.0, "ko": ko}
            else:  # booster
                redemption = np.where(
                    x >= 1.0, notional * (1.0 + p.participation * up),
                    np.where(x >= 1.0 - p.buffer, float(notional), notional * (x + p.buffer)),
                ) + notional * p.coupon_floor
                markers = {"ki": 1.0 - p.buffer, "strike": 1.0, "ko": 0.0}
            return {"worst_of": x.tolist(), "redemption": redemption.tolist(), **markers}

        strike = float(min(u.strike for u in ts.underlyings))
        ki = ts.knock_in.barrier if ts.knock_in is not None else 0.0
        redemption = np.where((x >= ki) | (x >= strike), notional, notional * x / strike)
        return {"worst_of": x.tolist(), "redemption": redemption.tolist(),
                "ki": ki, "strike": strike}
