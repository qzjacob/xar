"""Implied-volatility surfaces.

For equities, a per-name implied-vol *surface* is not obtainable from free/MCP
feeds (see plan §2.9), so the realistic default is :class:`ParametricSkewSurface`
(user supplies ATM term + skew slope/curvature) or :class:`GridVolSurface` (user
pastes marks). :class:`FlatVolSurface` exists mainly for the closed-form
validation gate, where MC must reproduce Black–Scholes exactly.

Surfaces are parameterised in **log-moneyness** ``x = ln(K / F(t))`` so they are
forward-centred (the put wing, ``x < 0``, is what the short down-and-in put cares
about). The Monte Carlo diffusion uses arbitrage-free **Dupire local vol** by default
(:func:`dupire_local_vol`); ``implied_vol`` is also available as a sticky-moneyness
proxy (``MCConfig.local_vol=False``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np
from scipy.interpolate import RegularGridInterpolator


def dupire_local_vol(
    surface: "VolSurface", k: np.ndarray, t: float,
    dk: float = 1e-3, dt: float = 1e-3, cap: float = 3.0,
) -> np.ndarray:
    """Dupire local volatility via Gatheral's total-implied-variance formula.

    Works for any :class:`VolSurface` by numerically differentiating the total
    implied variance ``w(k,t) = sigma_BS(k,t)^2 * t`` in log-moneyness ``k = ln(K/F)``:

        sigma_LV^2 = (dw/dt) / [ 1 - (k/w) dw/dk
                       + 1/4 (-1/4 - 1/w + k^2/w^2) (dw/dk)^2 + 1/2 d2w/dk2 ]

    For a flat surface this collapses to ``sigma`` exactly (so the Black–Scholes
    validation gate still passes). If the surface is locally arbitrageable
    (non-positive denominator/variance, or non-finite), we fall back to the
    implied variance ``w/t`` — a safe, never-NaN guard rather than a blow-up.
    """
    t = max(float(t), 1e-4)
    k = np.asarray(k, dtype=float)

    def w(kk: np.ndarray, tt: float) -> np.ndarray:
        return np.maximum(surface.implied_vol(kk, tt), 1e-6) ** 2 * tt

    w0 = w(k, t)
    wk = (w(k + dk, t) - w(k - dk, t)) / (2 * dk)
    wkk = (w(k + dk, t) - 2 * w0 + w(k - dk, t)) / (dk * dk)
    t_up, t_dn = t + dt, max(t - dt, 1e-4)
    wt = (w(k, t_up) - w(k, t_dn)) / (t_up - t_dn)

    denom = (
        1.0
        - (k / w0) * wk
        + 0.25 * (-0.25 - 1.0 / w0 + (k * k) / (w0 * w0)) * wk * wk
        + 0.5 * wkk
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        local_var = wt / denom
    implied_var = w0 / t
    bad = (denom <= 0) | (local_var <= 0) | ~np.isfinite(local_var)
    local_var = np.where(bad, implied_var, local_var)
    # Hard cap: Dupire's k-derivatives can blow the deep-wing local vol up to
    # hundreds of % at short horizons; clamp so the diffusion can never explode.
    return np.clip(np.sqrt(np.maximum(local_var, 1e-6)), 1e-3, cap)


@runtime_checkable
class VolSurface(Protocol):
    def implied_vol(self, log_moneyness: np.ndarray, t: float) -> np.ndarray:
        """Implied vol at log-moneyness ``x = ln(K/F(t))`` for maturity ``t``."""
        ...

    def atm_vol(self, t: float) -> float:
        """At-the-money (forward) vol for maturity ``t`` (representative scalar)."""
        ...


@dataclass(frozen=True)
class FlatVolSurface:
    sigma: float

    def implied_vol(self, log_moneyness: np.ndarray, t: float) -> np.ndarray:  # noqa: ARG002
        return np.full_like(np.asarray(log_moneyness, dtype=float), self.sigma)

    def atm_vol(self, t: float) -> float:  # noqa: ARG002
        return self.sigma

    def shifted(self, dvol: float) -> "FlatVolSurface":
        return FlatVolSurface(self.sigma + dvol)


@dataclass(frozen=True)
class ParametricSkewSurface:
    """ATM (optionally term-structured) plus a quadratic skew in log-moneyness.

    ``slope`` is typically *negative*: vol rises as strike falls below the forward
    (``x < 0``), i.e. the equity put skew. ``term`` optionally supplies an ATM term
    structure as ascending ``(t, vol)`` points (linear-in-variance interpolation).
    """

    atm: float
    slope: float = 0.0
    curv: float = 0.0
    floor: float = 0.01
    cap: float = 1.5  # cap vol so the quadratic wing cannot explode the diffusion
    x_clamp: float = 1.5  # clamp log-moneyness used for the lookup (flat extrapolation)
    term: tuple[tuple[float, float], ...] = ()

    def atm_vol(self, t: float) -> float:
        if not self.term:
            return self.atm
        ts = np.array([p[0] for p in self.term])
        vs = np.array([p[1] for p in self.term])
        # Interpolate total variance linearly in t, then back out vol.
        var = np.interp(t, ts, vs**2 * ts)
        return float(np.sqrt(max(var, 1e-12) / max(t, 1e-12)))

    def implied_vol(self, log_moneyness: np.ndarray, t: float) -> np.ndarray:
        x = np.clip(np.asarray(log_moneyness, dtype=float), -self.x_clamp, self.x_clamp)
        v = self.atm_vol(t) + self.slope * x + self.curv * x * x
        return np.clip(v, self.floor, self.cap)

    def shifted(self, dvol: float) -> "ParametricSkewSurface":
        from dataclasses import replace

        term = tuple((t, v + dvol) for t, v in self.term) if self.term else ()
        return replace(self, atm=self.atm + dvol, term=term)


@dataclass
class BumpedSurface:
    """Wrap a surface with a vol bump — parallel (``center=None``) or localised in a
    log-moneyness bucket (Gaussian weight). Used for bucketed / skew vega."""

    base: VolSurface
    dvol: float
    center: float | None = None
    width: float = 0.08
    floor: float = 0.01

    def implied_vol(self, log_moneyness: np.ndarray, t: float) -> np.ndarray:
        base = self.base.implied_vol(log_moneyness, t)
        if self.center is None:
            return np.maximum(base + self.dvol, self.floor)
        k = np.asarray(log_moneyness, dtype=float)
        wgt = np.exp(-0.5 * ((k - self.center) / self.width) ** 2)
        return np.maximum(base + self.dvol * wgt, self.floor)

    def atm_vol(self, t: float) -> float:
        return float(self.implied_vol(np.array([0.0]), t)[0])

    def shifted(self, dvol: float) -> "BumpedSurface":
        return BumpedSurface(self.base, self.dvol + dvol, self.center, self.width, self.floor)


@dataclass
class GridVolSurface:
    """User-pasted (log-moneyness x maturity) implied-vol grid with 2D interpolation."""

    log_moneyness: np.ndarray  # ascending 1D
    maturities: np.ndarray  # ascending 1D
    vols: np.ndarray  # shape (len(maturities), len(log_moneyness))
    floor: float = 0.01

    def __post_init__(self) -> None:
        self._interp = RegularGridInterpolator(
            (np.asarray(self.maturities, float), np.asarray(self.log_moneyness, float)),
            np.asarray(self.vols, float),
            bounds_error=False,
            fill_value=None,  # extrapolate by nearest-edge clamp below
        )

    def _eval(self, x: np.ndarray, t: float) -> np.ndarray:
        x = np.atleast_1d(np.asarray(x, dtype=float))
        xc = np.clip(x, self.log_moneyness[0], self.log_moneyness[-1])
        tc = float(np.clip(t, self.maturities[0], self.maturities[-1]))
        pts = np.column_stack([np.full_like(xc, tc), xc])
        return np.maximum(self._interp(pts), self.floor)

    def implied_vol(self, log_moneyness: np.ndarray, t: float) -> np.ndarray:
        return self._eval(log_moneyness, t)

    def atm_vol(self, t: float) -> float:
        return float(self._eval(np.array([0.0]), t)[0])

    def shifted(self, dvol: float) -> "GridVolSurface":
        return GridVolSurface(
            log_moneyness=self.log_moneyness.copy(),
            maturities=self.maturities.copy(),
            vols=self.vols + dvol,
            floor=self.floor,
        )

    @classmethod
    def from_scatter(
        cls,
        t: np.ndarray,
        x: np.ndarray,
        iv: np.ndarray,
        n_t: int = 8,
        n_x: int = 15,
        floor: float = 0.02,
        cap: float = 2.5,
    ) -> "GridVolSurface":
        """Build a regular-grid surface from scattered ``(maturity, log-moneyness, iv)``.

        Used to turn a live option chain into a fast, engine-friendly surface:
        scattered IV points are resampled onto a regular grid via linear
        interpolation with nearest-neighbour fill (so edges/holes are covered).
        """
        from scipy.interpolate import griddata

        t = np.asarray(t, float)
        x = np.asarray(x, float)
        iv = np.asarray(iv, float)

        t_uniq = np.unique(np.round(t, 4))
        if t_uniq.size >= 2:
            t_grid = (
                t_uniq if t_uniq.size <= n_t else np.linspace(t_uniq.min(), t_uniq.max(), n_t)
            )
        else:
            base = float(t_uniq[0]) if t_uniq.size else 1.0
            t_grid = np.array([max(base * 0.5, 1e-3), base])
        lo = max(-0.8, float(np.quantile(x, 0.02)))
        hi = min(0.5, float(np.quantile(x, 0.98)))
        if hi <= lo:
            lo, hi = -0.5, 0.3
        x_grid = np.linspace(lo, hi, n_x)

        tg, xg = np.meshgrid(t_grid, x_grid, indexing="ij")
        pts = np.column_stack([t, x])
        lin = griddata(pts, iv, (tg, xg), method="linear")
        near = griddata(pts, iv, (tg, xg), method="nearest")
        vols = np.where(np.isnan(lin), near, lin)
        vols = np.clip(vols, floor, cap)
        return cls(log_moneyness=x_grid, maturities=t_grid, vols=vols, floor=floor)
