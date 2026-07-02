"""Fee / margin layer: par -> reoffer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeeBreakdown:
    par: float
    structuring: float
    distribution: float
    hedging_reserve: float

    @property
    def total(self) -> float:
        return self.structuring + self.distribution + self.hedging_reserve

    @property
    def reoffer(self) -> float:
        return self.par - self.total

    @property
    def reoffer_fraction(self) -> float:
        return self.reoffer / self.par


@dataclass(frozen=True)
class FeeModel:
    structuring_pct: float = 0.012  # 1.2%
    distribution_pct: float = 0.008  # 0.8%
    hedging_reserve_pct: float = 0.003  # 0.3%

    def breakdown(self, par: float = 100.0) -> FeeBreakdown:
        return FeeBreakdown(
            par=par,
            structuring=par * self.structuring_pct,
            distribution=par * self.distribution_pct,
            hedging_reserve=par * self.hedging_reserve_pct,
        )
