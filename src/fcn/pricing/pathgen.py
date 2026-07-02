"""Correlated multi-asset path generation.

Each asset's capital process is GBM with drift ``mu = r - q - borrow`` (taken from
the forward curve), so for the continuous-dividend case the simulated spot is a
martingale to the forward and flat vol reproduces Black–Scholes exactly (the
validation gate). Two refinements over the naive model:

* **Discrete dividends** (``ForwardCurve.dividends``): the spot drops by the cash
  amount on each ex-date (a piecewise-lognormal "spot model"), consistent with the
  discrete-dividend forward in :class:`ForwardCurve`. This captures the ex-div jump
  that a continuous escrowed approximation misses near a knock-in barrier.
* **Diffusion vol**: either the sticky-moneyness implied vol at the running
  log-moneyness (a documented proxy) or, by default, arbitrage-free **Dupire local
  vol** (:func:`dupire_local_vol`) — the correct deterministic dynamics for
  worst-of autocallables.

Returns a :class:`PathBundle` with the spot tensor ``S`` of shape
``(n_paths, n_steps+1, n_assets)``. The Brownian-bridge knock-in/out correction
samples the vol at the *barrier* moneyness inside the payoff kernel, so the path
generator no longer carries an ATM step-vol.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fcn.core.rng import RNGSpec, standard_normals
from fcn.marketdata.snapshot import MarketSnapshot
from fcn.marketdata.volsurface import dupire_local_vol
from fcn.pricing.grid import TimeGrid


@dataclass(frozen=True)
class PathBundle:
    S: np.ndarray  # (n_paths, n_steps+1, n_assets)


class GBMPathGenerator:
    def __init__(self, local_vol: bool = True) -> None:
        # local_vol=True -> Dupire; False -> sticky-moneyness implied vol (proxy).
        self.local_vol = local_vol

    def generate(
        self, snapshot: MarketSnapshot, grid: TimeGrid, rng: RNGSpec, n_paths: int
    ) -> PathBundle:
        times = grid.times
        n_steps = grid.n_steps
        n_assets = snapshot.n_assets

        z = standard_normals(rng, n_paths, n_steps, n_assets)
        dW = z @ snapshot.correlation.cholesky().T  # correlate across assets

        spots = np.array([a.spot for a in snapshot.assets], dtype=float)
        mu = np.array([a.forward.mu for a in snapshot.assets], dtype=float)  # (A,)
        fwd = np.array([a.forward.forward(times) for a in snapshot.assets]).T  # (n_steps+1, A)
        dt = np.diff(times)
        sqrt_dt = np.sqrt(dt)

        # Map each asset's discrete dividends to the grid step they fall in.
        div_jumps: list[list[float]] = [[0.0] * n_steps for _ in range(n_assets)]
        for a, asset in enumerate(snapshot.assets):
            for d in asset.forward.dividends:
                k = int(np.searchsorted(times, d.t, side="left"))
                if 1 <= k <= n_steps:
                    div_jumps[a][k - 1] += d.amount

        S = np.empty((n_paths, n_steps + 1, n_assets), dtype=float)
        S[:, 0, :] = spots
        for k in range(1, n_steps + 1):
            prev = S[:, k - 1, :]
            t0 = float(times[k - 1])
            for a, asset in enumerate(snapshot.assets):
                logm = np.log(np.maximum(prev[:, a], 1e-300) / fwd[k - 1, a])
                if self.local_vol:
                    vol = dupire_local_vol(asset.surface, logm, t0)
                else:
                    vol = asset.surface.implied_vol(logm, t0)
                drift = mu[a] * dt[k - 1] - 0.5 * vol * vol * dt[k - 1]
                nxt = prev[:, a] * np.exp(drift + vol * sqrt_dt[k - 1] * dW[:, k - 1, a])
                jump = div_jumps[a][k - 1]
                if jump:
                    nxt = np.maximum(nxt - jump, 1e-300)
                S[:, k, a] = nxt

        return PathBundle(S=S)
