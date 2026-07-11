"""API request/response models.

The product is the :class:`TermSheet` itself (single source of truth). Market
inputs are expressed as a parametric skew per name plus rates and correlation —
the realistic equity workflow (no live single-name IV surface, plan §2.9).
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal

from fcn.product.termsheet import TermSheet


class AssetMarketInput(BaseModel):
    ticker: str
    spot: float = Field(..., gt=0)
    atm_vol: float = Field(..., gt=0, description="at-the-money implied vol, e.g. 0.30")
    skew_slope: float = Field(-0.5, description="d(vol)/d(log-moneyness); negative = put skew")
    skew_curv: float = Field(0.5, ge=0, description="smile curvature")
    div_yield: float = 0.0
    borrow: float = 0.0


class MarketInput(BaseModel):
    asof: str = "today"
    rate: float = 0.04
    funding: float | None = None
    issuer_spread: float = 0.0  # issuer CDS/funding spread added to discounting (CVA-lite)
    assets: list[AssetMarketInput]
    correlation: list[list[float]] | None = None  # full matrix
    rho: float | None = None  # uniform correlation shortcut

    source: str = "manual"  # "manual" | "live" (live = MCP/FMP-derived, see plan §2.9)


class MCInput(BaseModel):
    n_paths: int = 80_000
    seed: int = 0xC0FFEE
    method: str = "pseudo"  # "pseudo" | "sobol"
    antithetic: bool = True
    local_vol: bool = True  # Dupire LV (institution-grade) vs sticky-moneyness proxy


class PresetRequest(BaseModel):
    variant: str = "fcn"  # "fcn" | "phoenix" | "snowball"
    tickers: list[str]
    notional: float = 1_000_000
    currency: str = "USD"
    trade_date: str
    strike_date: str
    maturity: str
    coupon_rate: float | None = None
    frequency: str = "quarterly"
    autocall_barrier: float = 1.0
    step_down_per_period: float = 0.0
    ki_barrier: float = 0.65
    ki_style: str = "american"
    settlement: str = "cash"
    coupon_barrier: float = 0.70  # phoenix
    memory: bool = True  # phoenix
    # participation (sharkfin / booster)
    participation: float = 1.0
    ko_barrier: float = 1.30  # sharkfin up-and-out
    cap: float | None = None  # upside cap level
    buffer: float = 0.20  # booster airbag
    coupon_floor: float = 0.0  # sharkfin rebate


# Reference-grid pricing knobs (Extramile FCN columns): the issue price the note is struck at
# and the dealer's gross margin. When either is set, the reoffer target the coupon solves to is
# (note_price - gross_margin)/100 instead of the standard fee model. See main._reoffer_target.
class _ReofferKnobs(BaseModel):
    note_price_pct: float | None = Field(None, gt=0, le=120,
                                         description="issue price as % of par (reference 'Note Price', e.g. 99)")
    gross_margin_pct: float | None = Field(None, ge=0, le=20,
                                           description="dealer gross margin % (reference 'Gross Margin', e.g. 0.7)")


class QuoteRequest(_ReofferKnobs):
    termsheet: TermSheet
    market: MarketInput
    coupon_rate: float | None = None  # if None, uses termsheet.coupon.rate
    mc: MCInput = MCInput()
    include_greeks: bool = False
    include_scenario: bool = True


class SolveRequest(_ReofferKnobs):
    termsheet: TermSheet
    market: MarketInput
    mc: MCInput = MCInput()
    include_greeks: bool = True
    include_scenario: bool = True


class RankStructureInput(BaseModel):
    """Fixed FCN structure every candidate is screened against (closed-form FCN screen)."""

    product: str = "fcn"  # only the closed-form FCN screen is supported by ranking
    tenor_months: int = 6
    frequency: str = "quarterly"
    protection_pct: float = Field(0.70, gt=0, le=1.5)  # KI barrier as fraction of start
    strike_pct: float = Field(1.0, gt=0, le=1.5)
    reoffer_pct: float = Field(1.0, gt=0, le=1.2)  # issue price / value as fraction of par
    div_yield: float = 0.0
    borrow: float = 0.0


class RankRequest(BaseModel):
    structure: RankStructureInput = RankStructureInput()
    source: str = "live"  # "live" (Massive vols + FMP screener) | "manual"
    rate: float = 0.045
    funding: float | None = None
    top_n: int = Field(10, ge=1, le=50)
    rank_by: str = "coupon"  # coupon | prob_capital_at_risk | iv_at_barrier | marketCap
    filters: dict | None = None
    max_candidates: int = Field(200, ge=1, le=1500)
    min_market_cap: float = 2e10
    asof: str = "today"
    # manual mode (offline / tests): supply per-name market + the universe to rank
    assets: list[AssetMarketInput] | None = None
    tickers: list[str] | None = None


class MarketReadRequest(BaseModel):
    indices: list[str] = ["SPY", "QQQ"]
    source: str = "live"  # "live" (Massive surfaces) | "manual"
    rate: float = 0.045
    lang: str = "en"  # "en" | "zh"
    asof: str = "today"
    assets: list[AssetMarketInput] | None = None  # manual mode


class QuoteResponse(BaseModel):
    pricing: dict
    fees: dict
    payoff_diagram: dict
    scenario_table: list[dict] | None = None
    greeks: dict | None = None
    disclaimer: str


class SolveResponse(BaseModel):
    coupon_rate: float
    coupon_rate_se: float
    reoffer_fraction: float
    pricing: dict
    fees: dict
    payoff_diagram: dict
    scenario_table: list[dict] | None = None
    greeks: dict | None = None
    disclaimer: str


# --- Equity Options module ------------------------------------------------
#
# The Options Desk adds four async job endpoints (analyze, advise, strategy_build,
# chain) and synchronous CRUD for the blotter. Re-uses Massive as the live data
# source and the manual provider for offline/tests. Every request that touches
# the market shares one typed base so the handler doesn't duck-type fields.

class OptionsMarketInputs(BaseModel):
    """Common market context for every Options Desk request.

    ``source='live'`` reads Massive; ``'manual'`` synthesises a parametric
    surface from ``atm_vol/skew_slope/skew_curv`` at ``spot``. ``spot`` is
    optional: in live mode it comes from the provider, in manual mode it falls
    back to the strategy payload then 100.0.
    """
    source: str = "live"            # "live" (Massive) | "manual"
    spot: float | None = None        # manual-mode spot (None → from strategy / 100)
    rate: float = 0.045
    div_yield: float = 0.0
    borrow: float = 0.0
    atm_vol: float = 0.30           # manual-mode only
    skew_slope: float = -0.4         # manual-mode only
    skew_curv: float = 0.3           # manual-mode only
    asof: str = "today"
    max_maturity_years: float = 2.0


class OptionsAnalyzeRequest(OptionsMarketInputs):
    ticker: str


class AdvisorRequest(OptionsMarketInputs):
    ticker: str
    direction: str = "bullish"       # bullish | bearish | neutral
    horizon: str = "months"          # weeks | months | years
    conviction: int = 3              # 1-5
    vol_view: str = "stable"         # rising | falling | stable | spiked | depressed
    risk_budget_pct: float = 5.0
    holding_shares: float = 0.0
    income_preference: bool = False
    language: str = "zh"             # zh | en
    free_text: str | None = None


class StrategyBuildRequest(OptionsMarketInputs):
    """Build and value an explicit strategy spec.

    ``strategy`` is a :class:`fcn.options.strategies.StrategySpec` payload (the
    UI hands one in after the user picks strikes/expiries/quantities). ``ticker``
    and ``spot`` are pulled from the strategy when not supplied explicitly.
    """
    strategy: dict
    ticker: str | None = None
    optimize_liquidity: bool = True   # also value a liquid-strike variant + compare


class ChainRequest(OptionsMarketInputs):
    ticker: str


class BlotterAddRequest(OptionsMarketInputs):
    """Add a strategy to the blotter.

    The valuation is recomputed server-side from ``strategy`` + the market
    inputs (so a client cannot post a fabricated risk snapshot); the optional
    ``valuation`` is only a fallback if recomputation fails (e.g. live data out).
    """
    strategy: dict
    valuation: dict | None = None
    notes: str = ""


class BlotterUpdateRequest(BaseModel):
    """Update a blotter entry's notes and/or status."""
    notes: str | None = None
    status: Literal["open", "closed", "rolled"] | None = None
