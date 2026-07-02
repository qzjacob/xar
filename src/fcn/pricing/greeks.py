"""Greeks via CRN bump-and-revalue.

Every revaluation runs through the same RNGSpec (and, for spot/vol/rate/correlation
bumps, the same time grid) so draws are bit-identical and the variance of the
*difference* collapses. Refinements over a naive parallel-bump set (review groups):

* **Rho vs Carry** split: bump the discount/funding curve (Rho) separately from the
  forward growth rate (Carry) — for an FCN these have opposite signs, so a combined
  bump understates both.
* **Bucketed + skew vega**: worst-of autocallables are hedged on the OTM put-wing
  skew, not a parallel ATM move. We report basket vega per log-moneyness bucket and
  a put-minus-call skew-vega summary.
* **CRN-exact theta**: instead of rebuilding the grid from a shifted date (which
  changes the step count and breaks CRN), we advance the clock 1 day on the *same*
  realised paths — re-evaluate the cached bundle against a spec whose times are
  shifted by −1/365. Same draws, shorter horizon: a clean roll-down + decay.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from fcn.marketdata.snapshot import MarketSnapshot
from fcn.pricing.grid import build_grid
from fcn.pricing.payoff import PayoffEngine, PayoffResult
from fcn.pricing.results import GreeksResult
from fcn.product.termsheet import TermSheet

_VEGA_BUCKETS = (-0.30, -0.20, -0.10, 0.0, 0.10)  # log-moneyness centres


def _pp(res: PayoffResult, rate: float) -> np.ndarray:
    return res.redemption_pv + rate * res.coupon_unit_pv


class GreeksEngine:
    def __init__(
        self,
        engine,
        spot_bump: float = 0.01,
        vol_bump: float = 0.01,
        rate_bump: float = 1e-4,
        corr_bump: float = 0.01,
    ) -> None:
        self.engine = engine
        self.spot_bump = spot_bump
        self.vol_bump = vol_bump
        self.rate_bump = rate_bump
        self.corr_bump = corr_bump
        # Vega (an implied-vol sensitivity) is measured on the sticky-moneyness engine:
        # pushing a localised implied bump through Dupire's k-derivatives produces
        # unstable, sign-flipping local-vol artefacts. Sticky gives the economic
        # implied-vol hedge ratio the desk actually trades.
        from fcn.pricing.mcengine import MCEngine

        self._sticky = MCEngine(config=replace(engine.config, local_vol=False))

    def _pv_paths(self, ts: TermSheet, snap: MarketSnapshot, rate: float) -> np.ndarray:
        return _pp(self.engine.run(ts, snap), rate)

    def _pv_paths_sticky(self, ts: TermSheet, snap: MarketSnapshot, rate: float) -> np.ndarray:
        return _pp(self._sticky.run(ts, snap), rate)

    def compute(
        self, ts: TermSheet, snapshot: MarketSnapshot, coupon_rate: float
    ) -> GreeksResult:
        rate = coupon_rate
        cfg = self.engine.config
        grid = build_grid(ts, steps_per_year=cfg.steps_per_year)
        base_bundle = self.engine.pathgen.generate(snapshot, grid, cfg.rng, cfg.n_paths)
        base_spec = PayoffEngine.compile(ts, snapshot, grid)
        base = _pp(PayoffEngine.evaluate(base_bundle, base_spec), rate)
        tickers = [a.ticker for a in snapshot.assets]
        se: dict = {}

        delta, gamma, vega, delta_se, vega_se = [], [], [], [], []
        for tk in tickers:
            up = self._pv_paths(ts, snapshot.shock_spots({tk: 1 + self.spot_bump}), rate)
            dn = self._pv_paths(ts, snapshot.shock_spots({tk: 1 - self.spot_bump}), rate)
            d = 0.5 * (up - dn)
            g = up - 2 * base + dn
            delta.append(float(d.mean()))
            gamma.append(float(g.mean()))
            delta_se.append(float(d.std(ddof=1) / np.sqrt(d.size)))

            vu = self._pv_paths_sticky(ts, snapshot.shock_vols({tk: self.vol_bump}), rate)
            vd = self._pv_paths_sticky(ts, snapshot.shock_vols({tk: -self.vol_bump}), rate)
            v = 0.5 * (vu - vd)
            vega.append(float(v.mean()))
            vega_se.append(float(v.std(ddof=1) / np.sqrt(v.size)))

        # Rho (funding/discount) and Carry (forward growth) — separated.
        rho = float(
            0.5 * (
                self._pv_paths(ts, snapshot.shock_discount_rate(self.rate_bump), rate)
                - self._pv_paths(ts, snapshot.shock_discount_rate(-self.rate_bump), rate)
            ).mean()
        )
        carry = float(
            0.5 * (
                self._pv_paths(ts, snapshot.shock_growth_rate(self.rate_bump), rate)
                - self._pv_paths(ts, snapshot.shock_growth_rate(-self.rate_bump), rate)
            ).mean()
        )

        corr_sens = 0.0
        if snapshot.n_assets > 1:
            corr_sens = float(
                0.5 * (
                    self._pv_paths(ts, snapshot.shock_corr(self.corr_bump), rate)
                    - self._pv_paths(ts, snapshot.shock_corr(-self.corr_bump), rate)
                ).mean()
            )

        # Bucketed vega across the basket (the note's vega profile by strike region).
        bucketed: dict[str, float] = {}
        for c in _VEGA_BUCKETS:
            bu = self._pv_paths_sticky(ts, snapshot.shock_vol_bucket(self.vol_bump, c), rate)
            bd = self._pv_paths_sticky(ts, snapshot.shock_vol_bucket(-self.vol_bump, c), rate)
            bucketed[f"{c:+.2f}"] = float(0.5 * (bu - bd).mean())
        skew_vega = bucketed.get("-0.20", 0.0) - bucketed.get("+0.10", 0.0)

        theta = self._theta(ts, snapshot, grid, rate, float(base.mean()))

        se["delta"] = delta_se
        se["vega"] = vega_se
        return GreeksResult(
            delta=delta, gamma=gamma, vega=vega, theta=theta, rho=rho, carry=carry,
            corr_sens=corr_sens, bucketed_vega=bucketed, skew_vega=skew_vega, se=se,
        )

    def _theta(self, ts, snapshot, grid, rate, pv0, days: int = 1) -> float:
        """1-day theta that captures optionality decay AND stays CRN-exact.

        We keep the grid's node *count* (so the same RNG draws apply — CRN collapses
        the difference variance) but scale every node time by ``(T-dt)/T`` and
        regenerate the paths. The realised paths then diffuse over a one-day-shorter
        horizon, so the short-put time value actually decays — unlike clock-advancing
        the same path values, which would discard the decay entirely.

        Note: ``coupon_tau`` must scale with ``times`` so per-period coupon accrual
        stays consistent with the shortened tenor (otherwise coupon cashflows are
        overstated by a factor of ~T/(T-dt)).
        """
        cfg = self.engine.config
        big_t = float(grid.times[grid.maturity_idx])
        if big_t <= 0:
            return 0.0
        scale = max(0.0, big_t - days / 365.0) / big_t
        grid_th = replace(grid, times=grid.times * scale, coupon_tau=grid.coupon_tau * scale)
        bundle_th = self.engine.pathgen.generate(snapshot, grid_th, cfg.rng, cfg.n_paths)
        spec_th = PayoffEngine.compile(ts, snapshot, grid_th)
        pv1 = float(_pp(PayoffEngine.evaluate(bundle_th, spec_th), rate).mean())
        return pv1 - pv0
