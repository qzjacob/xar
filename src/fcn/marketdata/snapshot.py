"""Immutable market snapshot consumed by the pricing engine.

A snapshot is the fully-resolved market for one pricing: per-asset spot, forward
curve, vol surface and initial fixing, plus the discount curve and correlation.
``fingerprint`` gives a stable cache key for calibrated-market reuse (plan §3.4).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace

import numpy as np

from fcn.marketdata.correlation import Correlation
from fcn.marketdata.curve import DiscountCurve, ForwardCurve
from fcn.marketdata.volsurface import VolSurface


@dataclass(frozen=True)
class AssetMarket:
    ticker: str
    spot: float
    initial_fixing: float
    forward: ForwardCurve
    surface: VolSurface


@dataclass(frozen=True)
class MarketSnapshot:
    asof: str  # ISO date string
    assets: tuple[AssetMarket, ...]
    disc: DiscountCurve
    correlation: Correlation

    @property
    def n_assets(self) -> int:
        return len(self.assets)

    def shock_spots(self, factors: dict[str, float]) -> "MarketSnapshot":
        """Return a snapshot with spots scaled, *pinning the initial fixing*.

        Used for scenario analysis and delta/gamma: the note is already struck, so
        the fixing is frozen while spot (and hence the forward) moves.
        """
        new_assets = []
        for a in self.assets:
            f = factors.get(a.ticker, 1.0)
            new_spot = a.spot * f
            new_assets.append(
                AssetMarket(
                    ticker=a.ticker,
                    spot=new_spot,
                    initial_fixing=a.initial_fixing,
                    forward=replace(a.forward, spot=new_spot),
                    surface=a.surface,
                )
            )
        return replace(self, assets=tuple(new_assets))

    def shock_vols(self, dvol_by_ticker: dict[str, float]) -> "MarketSnapshot":
        """Return a snapshot with each named asset's vol surface shifted by dvol."""
        new_assets = []
        for a in self.assets:
            dv = dvol_by_ticker.get(a.ticker, 0.0)
            surface = a.surface.shifted(dv) if dv else a.surface
            new_assets.append(replace(a, surface=surface))
        return replace(self, assets=tuple(new_assets))

    def shock_rate(self, drate: float) -> "MarketSnapshot":
        """Bump the rate on both discount and forward curves (combined; legacy)."""
        return self.shock_discount_rate(drate).shock_growth_rate(drate)

    def shock_discount_rate(self, drate: float) -> "MarketSnapshot":
        """Bump only the discount/funding curve -> Rho (funding sensitivity)."""
        return replace(self, disc=replace(self.disc, rate=self.disc.rate + drate))

    def shock_growth_rate(self, drate: float) -> "MarketSnapshot":
        """Bump only the forward growth rate -> Carry/projection rho (drift sensitivity)."""
        new_assets = [
            replace(a, forward=replace(a.forward, rate=a.forward.rate + drate))
            for a in self.assets
        ]
        return replace(self, assets=tuple(new_assets))

    def shock_vol_bucket(self, dvol: float, center: float, width: float = 0.08) -> "MarketSnapshot":
        """Bump vol in a log-moneyness bucket on every asset (for bucketed/skew vega)."""
        from fcn.marketdata.volsurface import BumpedSurface

        new_assets = [
            replace(a, surface=BumpedSurface(a.surface, dvol, center, width)) for a in self.assets
        ]
        return replace(self, assets=tuple(new_assets))

    def shock_corr(self, delta: float) -> "MarketSnapshot":
        """Return a snapshot with all off-diagonal correlations bumped by delta."""
        return replace(self, correlation=self.correlation.bumped(delta))

    def fingerprint(self) -> str:
        payload = {
            "asof": self.asof,
            "disc_rate": self.disc.rate,
            "corr": np.round(self.correlation.matrix, 8).tolist(),
            "assets": [
                {
                    "ticker": a.ticker,
                    "spot": a.spot,
                    "fix": a.initial_fixing,
                    "fwd": [a.forward.rate, a.forward.div_yield, a.forward.borrow],
                    "atm": round(a.surface.atm_vol(1.0), 8),
                }
                for a in self.assets
            ],
        }
        blob = json.dumps(payload, sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]
