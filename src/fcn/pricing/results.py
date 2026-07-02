"""Result dataclasses returned by the engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class PricingResult:
    notional: float
    coupon_rate: float
    pv: float  # present value (currency)
    pv_se: float
    price_pct: float  # pv / notional * 100
    redemption_pv: float  # rate-independent component (currency)
    coupon_factor: float  # PV of coupons per unit rate (currency)
    prob_autocall: float
    prob_knock_in: float
    expected_life: float
    n_paths: int
    method: str
    wo_hist: dict = field(default_factory=dict)  # {"x": bin centers, "p": density} of worst-of @ maturity

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class GreeksResult:
    delta: list[float] = field(default_factory=list)  # per asset, PV per +1% spot
    gamma: list[float] = field(default_factory=list)  # per asset
    vega: list[float] = field(default_factory=list)  # per asset, parallel, per +1 vol pt
    theta: float = 0.0  # 1-day, clock-advanced on the same paths (CRN-exact)
    rho: float = 0.0  # discount/funding rate, per +1bp
    carry: float = 0.0  # forward growth rate, per +1bp (separate from rho)
    corr_sens: float = 0.0  # per +0.01 correlation
    bucketed_vega: dict = field(default_factory=dict)  # {log-moneyness center: basket vega}
    skew_vega: float = 0.0  # put-wing minus call-wing vega (skew risk summary)
    se: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
