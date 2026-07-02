"""Fundamental view schema + deterministic view→strategy-family mapping.

The advisor's first stage is *deterministic*: from a fundamental view (direction
× horizon × conviction × vol regime × holding) plus the live surface analytics,
we score every strategy family in :data:`fcn.options.strategies.STRATEGY_CATALOG`
and keep the top 3. The LLM's second stage then *selects within* those
candidates and writes the rationale — it cannot invent structures the rules
didn't pre-filter.

The mapping rules encode desk heuristics:

  * **Bullish + years + no holding** → LEAPS call (cheap stock substitute).
  * **Bullish + months + low vol + no holding** → long call / bull call spread.
  * **Bullish + months + high vol + holding** → collar / covered call (harvest
    the expensive premium while keeping the long).
  * **Bullish + weeks + spiked vol** → bull put spread (sell the rich premium).
  * **Bearish + holding + high vol** → protective put (insurance, expensive but
    a hedge).
  * **Bearish + weeks + low vol** → long put (cheap optionality).
  * **Neutral + weeks + high vol** → iron condor (sell both wings).
  * **Neutral + weeks + spiked vol** → iron condor / calendar (vol mean-revert).
  * **Neutral + months + holding** → covered call / wheel (income).
  * **Vol up (independent of direction)** → long straddle / strangle.
  * **Vol down** → short straddle / strangle / iron condor.

Conviction scales quantity (1–5 contracts) and risk_budget_pct; horizon scales
the expiry (weeks→30-60d, months→90-180d, years→365-730d).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from fcn.options.analytics import SurfaceAnalytics
from fcn.options.strategies import STRATEGY_CATALOG

Direction = Literal["bullish", "bearish", "neutral"]
Horizon = Literal["weeks", "months", "years"]
VolView = Literal["rising", "falling", "stable", "spiked", "depressed"]
Language = Literal["en", "zh"]

HORIZON_DAYS: dict[Horizon, tuple[int, int]] = {
    "weeks": (30, 60),
    "months": (90, 180),
    "years": (365, 730),
}


class FundamentalView(BaseModel):
    """The desk's fundamental view on a single name."""

    ticker: str
    direction: Direction
    horizon: Horizon = "months"
    conviction: int = Field(3, ge=1, le=5)
    vol_view: VolView = "stable"
    risk_budget_pct: float = Field(5.0, gt=0, le=100)
    holding_shares: float = 0.0           # >0 → triggers collar/CC overlays
    income_preference: bool = False
    language: Language = "zh"
    free_text: str | None = None          # optional prose; LLM may parse it

    @model_validator(mode="after")
    def _check(self) -> "FundamentalView":
        if self.holding_shares < 0:
            raise ValueError("holding_shares must be ≥ 0")
        return self


@dataclass(frozen=True)
class StrategyFamilyScore:
    """One row of the deterministic short-list."""

    name: str
    score: float                # 0-100
    family: str
    view: str
    description: str
    reasons: list[str]


def map_view_to_families(
    view: FundamentalView, analytics: SurfaceAnalytics,
) -> list[StrategyFamilyScore]:
    """Score every strategy family in the catalog; return top candidates.

    The score is a transparent weighted sum of binary "this rule applies"
    predicates — no ML, no randomness. The advisor sorts descending and keeps
    the top 3 (configurable).
    """
    scored: list[StrategyFamilyScore] = []
    for name, meta in STRATEGY_CATALOG.items():
        score, reasons = _score(name, meta, view, analytics)
        if score > 0:
            scored.append(StrategyFamilyScore(
                name=name, score=score, family=meta["family"],
                view=meta["view"], description=meta["desc"], reasons=reasons,
            ))
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored


def _score(
    name: str, meta: dict, view: FundamentalView, a: SurfaceAnalytics,
) -> tuple[float, list[str]]:
    """Return (score 0-100, reasons) for one (strategy, view, surface) triple."""
    s = 0.0
    reasons: list[str] = []
    direction = view.direction
    horizon = view.horizon
    holding = view.holding_shares > 0
    high_vol = a.vol_regime in ("high", "extreme")
    low_vol = a.vol_regime in ("depressed", "low")
    spiked = view.vol_view == "spiked" or a.iv_rv_gap is not None and a.iv_rv_gap > 0.05

    def add(points: float, why: str) -> None:
        nonlocal s
        s += points
        if why:
            reasons.append(why)

    # ---- direction alignment ---------------------------------------------
    if meta["view"] == "bullish" and direction == "bullish":
        add(30, "")
    if meta["view"] == "bearish" and direction == "bearish":
        add(30, "")
    if meta["view"] == "neutral" and direction == "neutral":
        add(30, "")
    # Vol views trigger independent of direction.
    if meta["view"] == "vol_up" and view.vol_view in ("rising", "spiked"):
        add(25, f"view expects vol {view.vol_view}; structure is long vega")
    if meta["view"] == "vol_up" and low_vol:
        add(15, "vol depressed → cheap to buy vega")
    if meta["view"] == "vol_down" and view.vol_view in ("falling",):
        add(25, "view expects vol falling; structure is short vega")
    if meta["view"] == "vol_down" and (high_vol or spiked):
        add(25, f"vol {a.vol_regime}+IV/RV gap {a.iv_rv_gap or 0:.1%} → premium is rich to sell")

    # ---- horizon alignment -----------------------------------------------
    if horizon == "years" and name == "long_leaps_call" and direction == "bullish":
        add(35, "long-dated horizon matches LEAPS tenor (1-2Y)")
    if horizon == "years" and meta["family"] in ("directional", "leaps"):
        add(10, "")
    if horizon == "weeks" and meta["family"] in ("volatility", "income"):
        add(10, "short horizon suits income / vol-capture structures")
    if horizon == "months" and meta["family"] == "directional":
        add(10, "")
    # Penalise long-dated structures on short horizons.
    if horizon == "weeks" and name in ("long_leaps_call",):
        add(-30, "")

    # ---- holding overlay -------------------------------------------------
    if holding and name in {"covered_call", "collar", "protective_put", "wheel"}:
        add(25, f"holding {int(view.holding_shares)} shares → overlay is natural")
    if not holding and name in {"covered_call", "collar", "protective_put", "wheel"}:
        add(-25, "requires existing stock position")
    if name == "covered_call" and holding and (high_vol or spiked):
        add(15, "high vol → call premium rich; covered call harvests it")
    if name == "collar" and holding and high_vol and direction == "bullish":
        add(20, "high vol + long stock → collar caps downside cheaply")
    if name == "protective_put" and holding and direction == "bearish":
        add(15, "bearish view on held stock → insure the downside")

    # ---- conviction / income preference ----------------------------------
    if view.income_preference and meta["family"] == "income":
        add(10, "income preference aligned")

    # ---- regime-specific -------------------------------------------------
    if name == "iron_condor" and direction == "neutral" and (high_vol or spiked):
        add(25, f"neutral + {a.vol_regime} vol → sell both wings")
    if name == "iron_condor" and direction == "neutral" and horizon == "weeks":
        add(10, "")
    if name == "calendar_spread" and spiked:
        add(15, "front-month vol rich vs far → calendar captures the slope")
    if name == "long_straddle" and view.vol_view in ("rising", "spiked") and direction == "neutral":
        add(15, "")
    if name == "cash_secured_put" and direction == "bullish" and (high_vol or spiked):
        add(15, "rich put premium → CSP income or favourable entry")
    if name == "risk_reversal" and direction == "bullish" and horizon in ("months", "years"):
        add(15, "fund the long call via a short put")

    return max(0.0, min(100.0, s)), reasons


def horizon_to_expiry(horizon: Horizon, asof: date) -> date:
    """Pick the *near* end of the horizon window as the strategy expiry."""
    days = HORIZON_DAYS[horizon][0]
    return asof + timedelta(days=days)


def conviction_to_quantity(conviction: int) -> int:
    """Map 1-5 conviction to a contract quantity (1-5)."""
    return max(1, min(5, int(conviction)))
