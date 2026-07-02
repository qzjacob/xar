"""Correlation matrix with PSD repair and Cholesky.

Correlation is never a clean feed (plan §2.4): it is estimated from historical
returns or supplied by the user. A user-supplied matrix can be non-PSD, so we
project to the nearest correlation matrix (Higham-style spectral clip) before
Cholesky. For worst-of products the *sign* matters: higher correlation -> the
note is worth more to the investor (less dispersion in the worst performer); this
is asserted in the test suite.
"""

from __future__ import annotations

import numpy as np


class Correlation:
    def __init__(self, matrix: np.ndarray):
        m = np.asarray(matrix, dtype=float)
        if m.ndim != 2 or m.shape[0] != m.shape[1]:
            raise ValueError("correlation must be a square matrix")
        m = 0.5 * (m + m.T)
        np.fill_diagonal(m, 1.0)
        self.matrix = _nearest_psd_correlation(m)

    @property
    def n(self) -> int:
        return self.matrix.shape[0]

    @classmethod
    def uniform(cls, n: int, rho: float) -> "Correlation":
        m = np.full((n, n), rho, dtype=float)
        np.fill_diagonal(m, 1.0)
        return cls(m)

    @classmethod
    def from_returns(cls, returns: np.ndarray) -> "Correlation":
        """Estimate from a ``(n_obs, n_assets)`` array of (log) returns."""
        return cls(np.corrcoef(np.asarray(returns, dtype=float), rowvar=False))

    def cholesky(self) -> np.ndarray:
        # matrix is PSD after repair; add a tiny jitter for numerical safety.
        try:
            return np.linalg.cholesky(self.matrix)
        except np.linalg.LinAlgError:
            return np.linalg.cholesky(self.matrix + 1e-12 * np.eye(self.n))

    def bumped(self, delta: float) -> "Correlation":
        """Shift all off-diagonal correlations by ``delta`` (PSD-repaired)."""
        m = self.matrix.copy()
        off = ~np.eye(self.n, dtype=bool)
        m[off] = np.clip(m[off] + delta, -0.999, 0.999)
        return Correlation(m)


def _nearest_psd_correlation(m: np.ndarray) -> np.ndarray:
    """Project a symmetric matrix to the nearest PSD correlation matrix."""
    vals, vecs = np.linalg.eigh(m)
    if np.all(vals >= -1e-12):
        return m
    vals_clipped = np.clip(vals, 0.0, None)
    psd = (vecs * vals_clipped) @ vecs.T
    # Rescale to unit diagonal.
    d = np.sqrt(np.clip(np.diag(psd), 1e-12, None))
    psd = psd / np.outer(d, d)
    np.fill_diagonal(psd, 1.0)
    return psd
