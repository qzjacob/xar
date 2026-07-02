"""Counter-based RNG for Monte Carlo with common-random-numbers (CRN) support.

The whole credibility of bump-and-revalue Greeks and of the coupon solver rests on
producing *bit-identical* random draws across revaluations. We therefore avoid the
global stateful RNG and derive every draw deterministically from an ``RNGSpec``.

Two engines:

* ``pseudo``  — counter-based Philox generator. Supports antithetic variates and
  yields a statistically valid standard error (``std / sqrt(N)``). This is the
  default for validation tests.
* ``sobol``   — scrambled (Owen) Sobol sequence mapped to normals via the inverse
  CDF. Lower discrepancy, faster convergence. Antithetic is *not* combined with
  Sobol (it breaks the equidistribution); the reported ``std/sqrt(N)`` is then a
  rough proxy rather than a strict CI — flagged in diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from scipy import stats


@dataclass(frozen=True)
class RNGSpec:
    """Reproducible specification of a random source.

    ``seed`` is the root key; CRN is achieved by reusing the same ``RNGSpec`` across
    base and bumped pricings (see :func:`derive`).
    """

    seed: int = 0xC0FFEE
    method: str = "pseudo"  # "pseudo" | "sobol"
    antithetic: bool = True

    def derive(self, tag: int) -> "RNGSpec":
        """Derive a child spec for an independent-but-reproducible sub-stream.

        Used to give, e.g., each Greek bump its own stream while remaining fully
        deterministic. Different ``tag`` -> different stream; same ``tag`` -> CRN.
        """
        # Mix the tag into the seed with a cheap, well-dispersing hash.
        mixed = (self.seed ^ (np.uint64(tag) * np.uint64(0x9E3779B97F4A7C15))) & np.uint64(
            0xFFFFFFFFFFFFFFFF
        )
        return replace(self, seed=int(mixed))


def standard_normals(spec: RNGSpec, n_paths: int, n_steps: int, n_assets: int) -> np.ndarray:
    """Return standard normal draws of shape ``(n_paths, n_steps, n_assets)``.

    Deterministic in ``spec``: identical ``spec`` -> identical array (the CRN guarantee).
    """
    if n_steps <= 0 or n_assets <= 0 or n_paths <= 0:
        raise ValueError("n_paths, n_steps, n_assets must be positive")

    dim = n_steps * n_assets

    if spec.method == "sobol":
        engine = stats.qmc.Sobol(d=dim, scramble=True, seed=spec.seed)
        u = engine.random(n_paths)
        u = np.clip(u, 1e-12, 1.0 - 1e-12)
        z = stats.norm.ppf(u)
        return z.reshape(n_paths, n_steps, n_assets)

    if spec.method == "pseudo":
        gen = np.random.Generator(np.random.Philox(key=spec.seed))
        if spec.antithetic:
            half = (n_paths + 1) // 2
            base = gen.standard_normal((half, n_steps, n_assets))
            z = np.concatenate([base, -base], axis=0)
            return z[:n_paths]
        return gen.standard_normal((n_paths, n_steps, n_assets))

    raise ValueError(f"unknown RNG method: {spec.method!r}")
