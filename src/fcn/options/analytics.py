"""IV-surface analytics: skew, term structure, risk reversal, IV–RV, vol regime.

The desk reads the surface through a small set of well-defined metrics, all
deterministic (no LLM) so they can be unit-tested against a known surface. The
advisor consumes :class:`SurfaceAnalytics` to map a fundamental view to a
strategy family.

Inputs: a :class:`fcn.marketdata.volsurface.VolSurface` (per name, live from
Massive or parametric) plus an optional price history for realized vol. The
surface is parameterised in log-moneyness; we use 25Δ and 10Δ as the standard
wing markers (FX convention; equity desks use 90% / 110% strikes almost
interchangeably for indices).

  * **ATM term** — ATM vol per standard tenor (1M / 2M / 3M / 6M / 1Y / 2Y).
  * **Skew** — vol at 90% strike minus ATM (3M), in vol points.
  * **Risk reversal (RR)** — vol(25Δ call) − vol(25Δ put). Positive ⇒ OTM calls
    pricier than OTM puts (bullish skew).
  * **Butterfly (BF)** — (vol(25Δ call) + vol(25Δ put))/2 − ATM. Pure smile
    curvature, no direction.
  * **IV–RV gap** — 1M ATM IV − 21d realized. Positive ⇒ implied rich.
  * **Vol regime** — 1Y ATM vol percentile vs last 1Y history (or just the level
    if no history): depressed / low / normal / high / extreme.
  * **Term structure** — contango / flat / backwardated (1Y − 1M).

Honest scope: this is a desk-read of the surface, not a full SVI fit / SABR
calibration. We rely on the surface's own interpolation; for live Massive
surfaces that's the GridVolSurface built from OTM IV marks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import numpy as np

from fcn.marketdata.volsurface import VolSurface
from fcn.options.greeks import Kind, delta_to_strike

VolRegime = Literal["depressed", "low", "normal", "high", "extreme"]
TermStructure = Literal["contango", "flat", "backwardated"]


@dataclass(frozen=True)
class SurfaceAnalytics:
    """Desk read of a single-name implied-vol surface."""

    ticker: str
    spot: float
    asof: date
    rate: float
    div_yield: float
    borrow: float

    atm_term: list[tuple[float, float]]     # (tenor_years, atm_vol)
    skew_90_3m: float                        # vol at 90% strike − ATM, 3M (vol pts)
    risk_reversal_25d_3m: float              # vol(25Δc) − vol(25Δp), 3M (vol pts)
    risk_reversal_10d_3m: float
    butterfly_25d_3m: float                  # (vol(25Δc) + vol(25Δp))/2 − ATM, 3M
    butterfly_10d_3m: float
    term_slope_1y_1m: float                  # ATM(1Y) − ATM(1M), vol pts
    term_structure: TermStructure
    iv_1m_atm: float
    realized_21d: float | None
    realized_63d: float | None
    iv_rv_gap: float | None                  # IV_1M − RV_21d, vol pts
    vol_1y_percentile: float | None          # 0-100
    vol_regime: VolRegime
    wing_marks: dict                         # {25Δ_put, 25Δ_call, 10Δ_put, 10Δ_call, ATM} at 3M

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker, "spot": round(self.spot, 4), "asof": self.asof.isoformat(),
            "rate": self.rate, "div_yield": self.div_yield, "borrow": self.borrow,
            "atm_term": [[round(t, 4), round(v, 4)] for t, v in self.atm_term],
            "skew_90_3m": round(self.skew_90_3m, 4),
            "risk_reversal_25d_3m": round(self.risk_reversal_25d_3m, 4),
            "risk_reversal_10d_3m": round(self.risk_reversal_10d_3m, 4),
            "butterfly_25d_3m": round(self.butterfly_25d_3m, 4),
            "butterfly_10d_3m": round(self.butterfly_10d_3m, 4),
            "term_slope_1y_1m": round(self.term_slope_1y_1m, 4),
            "term_structure": self.term_structure,
            "iv_1m_atm": round(self.iv_1m_atm, 4),
            "realized_21d": None if self.realized_21d is None else round(self.realized_21d, 4),
            "realized_63d": None if self.realized_63d is None else round(self.realized_63d, 4),
            "iv_rv_gap": None if self.iv_rv_gap is None else round(self.iv_rv_gap, 4),
            "vol_1y_percentile": None if self.vol_1y_percentile is None else round(self.vol_1y_percentile, 1),
            "vol_regime": self.vol_regime,
            "wing_marks": {k: round(v, 4) for k, v in self.wing_marks.items()},
        }


# Standard tenors (years) for the ATM term-structure readout.
_STANDARD_TENORS = (1 / 12, 2 / 12, 3 / 12, 6 / 12, 1.0, 2.0)


def analyze_surface(
    surface: VolSurface, *, ticker: str, spot: float, rate: float,
    div_yield: float = 0.0, borrow: float = 0.0, asof: date | None = None,
    history: np.ndarray | None = None,
) -> SurfaceAnalytics:
    """Compute the full surface analytics block.

    ``history`` is the optional daily-close series (most-recent last) used for
    realized vol and the 1Y vol percentile. When absent, both are reported as
    ``None`` (the rest still computes).
    """
    asof = asof or date.today()
    # ATM term structure.
    atm_term = [(t, float(surface.atm_vol(t))) for t in _STANDARD_TENORS]
    iv_1m = atm_term[0][1]
    iv_3m = atm_term[2][1]
    iv_1y = atm_term[4][1]
    term_slope = iv_1y - iv_1m

    # Wing marks at 3M: solve strike for {10Δ, 25Δ} call and put.
    t3 = 3 / 12
    iv_atm_3m = float(surface.atm_vol(t3))
    wing_marks = _wing_marks(surface, spot, t3, iv_atm_3m, rate, div_yield, borrow)
    rr_25 = wing_marks["25Δ_call"] - wing_marks["25Δ_put"]
    rr_10 = wing_marks["10Δ_call"] - wing_marks["10Δ_put"]
    bf_25 = 0.5 * (wing_marks["25Δ_call"] + wing_marks["25Δ_put"]) - iv_atm_3m
    bf_10 = 0.5 * (wing_marks["10Δ_call"] + wing_marks["10Δ_put"]) - iv_atm_3m

    # Equity skew (90% strike − ATM, 3M).
    log_m_90 = float(np.log(0.90))
    skew_90 = float(surface.implied_vol(np.array([log_m_90]), t3)[0]) - iv_atm_3m

    # Realized vol + IV–RV gap.
    rv_21, rv_63 = _realized_vol(history)
    iv_rv_gap = (iv_1m - rv_21) if rv_21 is not None else None

    # 1Y vol percentile: where the *current* realized vol sits within its own
    # trailing realized-vol distribution (self-consistent; not biased by the
    # vol-risk-premium the way comparing IV to the RV distribution would be).
    vol_pctile = _vol_percentile(history)

    return SurfaceAnalytics(
        ticker=ticker, spot=spot, asof=asof, rate=rate,
        div_yield=div_yield, borrow=borrow,
        atm_term=atm_term, skew_90_3m=skew_90,
        risk_reversal_25d_3m=rr_25, risk_reversal_10d_3m=rr_10,
        butterfly_25d_3m=bf_25, butterfly_10d_3m=bf_10,
        term_slope_1y_1m=term_slope,
        term_structure=_term_label(term_slope),
        iv_1m_atm=iv_1m, realized_21d=rv_21, realized_63d=rv_63,
        iv_rv_gap=iv_rv_gap, vol_1y_percentile=vol_pctile,
        vol_regime=_vol_regime(iv_1y, vol_pctile),
        wing_marks=wing_marks,
    )


def _wing_marks(
    surface: VolSurface, spot: float, t: float, atm_iv: float,
    rate: float, div_yield: float, borrow: float,
) -> dict[str, float]:
    """Vol at 10Δ / 25Δ call and put wings for maturity ``t``.

    Solves strike(K) for the target delta under a sticky-moneyness first guess
    (uses the ATM vol as the seed), then reads the surface vol at that strike.
    The surface's own skew is then respected on a second pass — converges in 2-3.
    """
    out = {"ATM": atm_iv}
    targets: list[tuple[str, float, Kind]] = [
        ("25Δ_put", -0.25, "put"),
        ("10Δ_put", -0.10, "put"),
        ("25Δ_call", 0.25, "call"),
        ("10Δ_call", 0.10, "call"),
    ]
    for label, target_d, kind in targets:
        # First pass: seed strike with ATM vol, then refine with surface vol.
        sigma = atm_iv
        for _ in range(3):
            k = delta_to_strike(target_d, spot, t, sigma, rate, div_yield, borrow, kind=kind)
            log_m = float(np.log(k / spot))
            sigma = float(surface.implied_vol(np.array([log_m]), t)[0])
        out[label] = sigma
    return out


def _realized_vol(history: np.ndarray | None) -> tuple[float | None, float | None]:
    """21d and 63d annualised realised vol from daily closes."""
    if history is None or len(history) < 5:
        return None, None
    log_rets = np.diff(np.log(np.asarray(history, dtype=float)))
    rv_21 = float(np.std(log_rets[-21:], ddof=1) * np.sqrt(252)) if len(log_rets) >= 21 else None
    rv_63 = float(np.std(log_rets[-63:], ddof=1) * np.sqrt(252)) if len(log_rets) >= 63 else None
    return rv_21, rv_63


def _vol_percentile(history: np.ndarray | None) -> float | None:
    """1Y vol percentile: where the *current* 21d realized vol sits within its
    own trailing 21d-rolling-RV distribution.

    Self-consistent (RV vs RV), so it is not inflated by the implied-vs-realized
    risk premium. Returns ``None`` without enough history. A proper IV percentile
    would need ~1Y of option-chain snapshots we don't store.
    """
    if history is None or len(history) < 63:
        return None
    log_rets = np.diff(np.log(np.asarray(history, dtype=float)))
    window = 21
    if len(log_rets) < window + 5:
        return None
    roll = np.array([
        np.std(log_rets[i:i + window], ddof=1) * np.sqrt(252)
        for i in range(len(log_rets) - window + 1)
    ])
    roll = roll[-252:]  # last year of rolling RV
    if len(roll) < 10:
        return None
    current = roll[-1]
    return float(np.mean(roll < current) * 100.0)


def _term_label(slope: float) -> TermStructure:
    if slope > 0.005:    # > 0.5 vol pt
        return "contango"
    if slope < -0.005:
        return "backwardated"
    return "flat"


def _vol_regime(iv_1y: float, percentile: float | None) -> VolRegime:
    """Map IV level + history percentile to a regime label.

    When we have a percentile, use it (more robust to name-specific baselines).
    Otherwise fall back to absolute IV thresholds (broad market rule of thumb).
    """
    if percentile is not None:
        if percentile < 10:
            return "depressed"
        if percentile < 30:
            return "low"
        if percentile < 70:
            return "normal"
        if percentile < 90:
            return "high"
        return "extreme"
    # Absolute fallback (S&P single-name rules of thumb).
    if iv_1y < 0.15:
        return "depressed"
    if iv_1y < 0.22:
        return "low"
    if iv_1y < 0.35:
        return "normal"
    if iv_1y < 0.55:
        return "high"
    return "extreme"
