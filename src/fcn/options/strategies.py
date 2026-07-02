"""Strategy specifications and 21 named-strategy factories.

A :class:`StrategySpec` is the source-of-truth for any options structure the desk
builds — one or more :class:`OptionLeg` plus an optional :class:`StockLeg` for
overlays (collar, covered call, protective put, wheel). Each named-strategy
factory is a pure builder: it does not price (that is :mod:`strategy_engine`); it
just instantiates the spec with the right legs and a :class:`ViewTag` so the
advisor/UI can filter and explain it.

Conventions:
  * All legs are per-contract (one option = 100 shares multiplier, applied in
    :func:`fcn.options.strategy_engine.value_strategy`); ``quantity`` is signed
    (+long / −short) and expressed in *contracts*.
  * Strikes are absolute (currency); the advisor converts "25Δ put" to a strike
    via :func:`fcn.options.greeks.delta_to_strike` before calling the factory.
  * Expries are :class:`datetime.date`; the advisor/UI choose them from the chain.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


Kind = Literal["call", "put"]
Family = Literal["directional", "volatility", "income", "hedging", "leaps"]
ViewTag = Literal["bullish", "bearish", "neutral", "vol_up", "vol_down"]


class OptionLeg(BaseModel):
    kind: Kind
    expiry: date
    strike: float = Field(..., gt=0)
    quantity: int                   # +long / −short, in contracts
    unit_price: float | None = None  # None: filled from the chain at pricing time
    source: Literal["live", "bs"] = "bs"


class StockLeg(BaseModel):
    quantity: int                   # +long / −short, in shares
    entry_price: float = Field(..., gt=0)


class StrategySpec(BaseModel):
    name: str
    family: Family
    ticker: str
    spot: float = Field(..., gt=0)
    option_legs: list[OptionLeg]
    stock_leg: StockLeg | None = None
    notional: float | None = None
    view_tag: ViewTag | None = None
    rationale: str = ""

    @model_validator(mode="after")
    def _check(self) -> "StrategySpec":
        if not self.option_legs and self.stock_leg is None:
            raise ValueError("strategy must have at least one option or stock leg")
        return self


# ---------------------------------------------------------------------------
# Directional
# ---------------------------------------------------------------------------

def long_call(ticker: str, spot: float, expiry: date, strike: float, *,
              qty: int = 1) -> StrategySpec:
    return StrategySpec(name="long_call", family="directional", ticker=ticker, spot=spot,
                        option_legs=[OptionLeg(kind="call", expiry=expiry, strike=strike, quantity=qty)],
                        view_tag="bullish")


def long_put(ticker: str, spot: float, expiry: date, strike: float, *,
             qty: int = 1) -> StrategySpec:
    return StrategySpec(name="long_put", family="directional", ticker=ticker, spot=spot,
                        option_legs=[OptionLeg(kind="put", expiry=expiry, strike=strike, quantity=qty)],
                        view_tag="bearish")


def bull_call_spread(ticker: str, spot: float, expiry: date,
                     long_strike: float, short_strike: float, *, qty: int = 1) -> StrategySpec:
    if short_strike <= long_strike:
        raise ValueError("bull_call_spread requires short_strike > long_strike")
    return StrategySpec(
        name="bull_call_spread", family="directional", ticker=ticker, spot=spot,
        option_legs=[
            OptionLeg(kind="call", expiry=expiry, strike=long_strike, quantity=qty),
            OptionLeg(kind="call", expiry=expiry, strike=short_strike, quantity=-qty),
        ],
        view_tag="bullish",
    )


def bull_put_spread(ticker: str, spot: float, expiry: date,
                    short_strike: float, long_strike: float, *, qty: int = 1) -> StrategySpec:
    """Sell ATM/OTM put, buy further-OTM put (for protection). Bullish, credit."""
    if long_strike >= short_strike:
        raise ValueError("bull_put_spread requires short_strike > long_strike")
    return StrategySpec(
        name="bull_put_spread", family="directional", ticker=ticker, spot=spot,
        option_legs=[
            OptionLeg(kind="put", expiry=expiry, strike=short_strike, quantity=-qty),
            OptionLeg(kind="put", expiry=expiry, strike=long_strike, quantity=qty),
        ],
        view_tag="bullish",
    )


def bear_call_spread(ticker: str, spot: float, expiry: date,
                     short_strike: float, long_strike: float, *, qty: int = 1) -> StrategySpec:
    """Sell ATM/OTM call, buy further-OTM call (for protection). Bearish, credit."""
    if long_strike <= short_strike:
        raise ValueError("bear_call_spread requires long_strike > short_strike")
    return StrategySpec(
        name="bear_call_spread", family="directional", ticker=ticker, spot=spot,
        option_legs=[
            OptionLeg(kind="call", expiry=expiry, strike=short_strike, quantity=-qty),
            OptionLeg(kind="call", expiry=expiry, strike=long_strike, quantity=qty),
        ],
        view_tag="bearish",
    )


def bear_put_spread(ticker: str, spot: float, expiry: date,
                    long_strike: float, short_strike: float, *, qty: int = 1) -> StrategySpec:
    if short_strike >= long_strike:
        raise ValueError("bear_put_spread requires long_strike > short_strike")
    return StrategySpec(
        name="bear_put_spread", family="directional", ticker=ticker, spot=spot,
        option_legs=[
            OptionLeg(kind="put", expiry=expiry, strike=long_strike, quantity=qty),
            OptionLeg(kind="put", expiry=expiry, strike=short_strike, quantity=-qty),
        ],
        view_tag="bearish",
    )


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------

def long_straddle(ticker: str, spot: float, expiry: date, atm_strike: float, *,
                  qty: int = 1) -> StrategySpec:
    return StrategySpec(
        name="long_straddle", family="volatility", ticker=ticker, spot=spot,
        option_legs=[
            OptionLeg(kind="call", expiry=expiry, strike=atm_strike, quantity=qty),
            OptionLeg(kind="put", expiry=expiry, strike=atm_strike, quantity=qty),
        ],
        view_tag="vol_up",
    )


def long_strangle(ticker: str, spot: float, expiry: date,
                  otm_call_strike: float, otm_put_strike: float, *, qty: int = 1) -> StrategySpec:
    if otm_call_strike <= otm_put_strike:
        raise ValueError("strangle requires call_strike > put_strike")
    return StrategySpec(
        name="long_strangle", family="volatility", ticker=ticker, spot=spot,
        option_legs=[
            OptionLeg(kind="call", expiry=expiry, strike=otm_call_strike, quantity=qty),
            OptionLeg(kind="put", expiry=expiry, strike=otm_put_strike, quantity=qty),
        ],
        view_tag="vol_up",
    )


def short_straddle(ticker: str, spot: float, expiry: date, atm_strike: float, *,
                   qty: int = 1) -> StrategySpec:
    return StrategySpec(
        name="short_straddle", family="volatility", ticker=ticker, spot=spot,
        option_legs=[
            OptionLeg(kind="call", expiry=expiry, strike=atm_strike, quantity=-qty),
            OptionLeg(kind="put", expiry=expiry, strike=atm_strike, quantity=-qty),
        ],
        view_tag="vol_down",
    )


def short_strangle(ticker: str, spot: float, expiry: date,
                   otm_call_strike: float, otm_put_strike: float, *, qty: int = 1) -> StrategySpec:
    if otm_call_strike <= otm_put_strike:
        raise ValueError("strangle requires call_strike > put_strike")
    return StrategySpec(
        name="short_strangle", family="volatility", ticker=ticker, spot=spot,
        option_legs=[
            OptionLeg(kind="call", expiry=expiry, strike=otm_call_strike, quantity=-qty),
            OptionLeg(kind="put", expiry=expiry, strike=otm_put_strike, quantity=-qty),
        ],
        view_tag="vol_down",
    )


def iron_condor(ticker: str, spot: float, expiry: date,
                put_long: float, put_short: float, call_short: float, call_long: float,
                *, qty: int = 1) -> StrategySpec:
    """Standard iron condor: short OTM put + short OTM call, both capped."""
    if not (put_long < put_short <= spot <= call_short < call_long):
        raise ValueError("iron_condor wings must satisfy put_long < put_short ≤ spot ≤ call_short < call_long")
    return StrategySpec(
        name="iron_condor", family="volatility", ticker=ticker, spot=spot,
        option_legs=[
            OptionLeg(kind="put", expiry=expiry, strike=put_long, quantity=qty),
            OptionLeg(kind="put", expiry=expiry, strike=put_short, quantity=-qty),
            OptionLeg(kind="call", expiry=expiry, strike=call_short, quantity=-qty),
            OptionLeg(kind="call", expiry=expiry, strike=call_long, quantity=qty),
        ],
        view_tag="vol_down",
    )


def iron_butterfly(ticker: str, spot: float, expiry: date, atm_strike: float,
                   wing_width: float, *, qty: int = 1) -> StrategySpec:
    """Short ATM straddle + protective wings equidistant from ATM."""
    if wing_width <= 0:
        raise ValueError("wing_width must be positive")
    return iron_condor(
        ticker, spot, expiry,
        put_long=atm_strike - wing_width, put_short=atm_strike,
        call_short=atm_strike, call_long=atm_strike + wing_width, qty=qty,
    ).model_copy(update={"name": "iron_butterfly"})


def calendar_spread(ticker: str, spot: float, *,
                    near_expiry: date, far_expiry: date,
                    strike: float, qty: int = 1) -> StrategySpec:
    """Short near-term, long far-term at the same strike (vol term-structure play)."""
    if near_expiry >= far_expiry:
        raise ValueError("calendar requires near_expiry < far_expiry")
    return StrategySpec(
        name="calendar_spread", family="volatility", ticker=ticker, spot=spot,
        option_legs=[
            OptionLeg(kind="call", expiry=near_expiry, strike=strike, quantity=-qty),
            OptionLeg(kind="call", expiry=far_expiry, strike=strike, quantity=qty),
        ],
        view_tag="vol_up",
    )


def diagonal_spread(ticker: str, spot: float, *,
                    near_expiry: date, far_expiry: date,
                    near_strike: float, far_strike: float,
                    kind: Kind = "call", qty: int = 1) -> StrategySpec:
    """Short near-term lower strike, long far-term higher strike (call diag)."""
    if near_expiry >= far_expiry:
        raise ValueError("diagonal requires near_expiry < far_expiry")
    return StrategySpec(
        name="diagonal_spread", family="volatility", ticker=ticker, spot=spot,
        option_legs=[
            OptionLeg(kind=kind, expiry=near_expiry, strike=near_strike, quantity=-qty),
            OptionLeg(kind=kind, expiry=far_expiry, strike=far_strike, quantity=qty),
        ],
        view_tag="bullish" if kind == "call" and far_strike > near_strike else "neutral",
    )


# ---------------------------------------------------------------------------
# Income
# ---------------------------------------------------------------------------

def covered_call(ticker: str, spot: float, expiry: date,
                 short_call_strike: float, *, shares: int = 100, qty: int = 1) -> StrategySpec:
    return StrategySpec(
        name="covered_call", family="income", ticker=ticker, spot=spot,
        option_legs=[OptionLeg(kind="call", expiry=expiry,
                               strike=short_call_strike, quantity=-qty)],
        stock_leg=StockLeg(quantity=shares, entry_price=spot),
        view_tag="neutral",
    )


def cash_secured_put(ticker: str, spot: float, expiry: date,
                     short_put_strike: float, *, qty: int = 1) -> StrategySpec:
    return StrategySpec(
        name="cash_secured_put", family="income", ticker=ticker, spot=spot,
        option_legs=[OptionLeg(kind="put", expiry=expiry,
                               strike=short_put_strike, quantity=-qty)],
        view_tag="bullish",
    )


def wheel(ticker: str, spot: float, expiry: date,
          short_put_strike: float, *, qty: int = 1) -> StrategySpec:
    """Wheel = the assignment-aware narrative of a cash-secured put; same legs.

    The wheel is a *process* (CSP → assignment → covered call → assignment → CSP)
    rather than a one-shot payoff. We expose it as a tagged CSP so the blotter
    can roll it forward; the rationale text carries the cycle description.
    """
    spec = cash_secured_put(ticker, spot, expiry, short_put_strike, qty=qty)
    spec.name = "wheel"
    spec.rationale = (
        "Wheel: short cash-secured put; on assignment, switch to a covered call "
        "at the next expiry above the new cost basis; on called-away, restart."
    )
    return spec


# ---------------------------------------------------------------------------
# Hedging
# ---------------------------------------------------------------------------

def protective_put(ticker: str, spot: float, expiry: date,
                   put_strike: float, *, shares: int = 100, qty: int = 1) -> StrategySpec:
    return StrategySpec(
        name="protective_put", family="hedging", ticker=ticker, spot=spot,
        option_legs=[OptionLeg(kind="put", expiry=expiry, strike=put_strike, quantity=qty)],
        stock_leg=StockLeg(quantity=shares, entry_price=spot),
        view_tag="bullish",  # still long the underlying; just insuring
    )


def collar(ticker: str, spot: float, expiry: date,
           long_put_strike: float, short_call_strike: float, *,
           shares: int = 100, qty: int = 1) -> StrategySpec:
    if short_call_strike <= spot or long_put_strike >= spot:
        raise ValueError("collar requires put_strike < spot < call_strike")
    return StrategySpec(
        name="collar", family="hedging", ticker=ticker, spot=spot,
        option_legs=[
            OptionLeg(kind="put", expiry=expiry, strike=long_put_strike, quantity=qty),
            OptionLeg(kind="call", expiry=expiry, strike=short_call_strike, quantity=-qty),
        ],
        stock_leg=StockLeg(quantity=shares, entry_price=spot),
        view_tag="bullish",
    )


def risk_reversal(ticker: str, spot: float, expiry: date,
                  long_call_strike: float, short_put_strike: float, *, qty: int = 1) -> StrategySpec:
    """Long OTM call funded by short OTM put — bullish, low/no upfront cost."""
    if not (short_put_strike < spot < long_call_strike):
        raise ValueError("risk_reversal requires put_strike < spot < call_strike")
    return StrategySpec(
        name="risk_reversal", family="hedging", ticker=ticker, spot=spot,
        option_legs=[
            OptionLeg(kind="call", expiry=expiry, strike=long_call_strike, quantity=qty),
            OptionLeg(kind="put", expiry=expiry, strike=short_put_strike, quantity=-qty),
        ],
        view_tag="bullish",
    )


# ---------------------------------------------------------------------------
# LEAPS
# ---------------------------------------------------------------------------

def long_leaps_call(ticker: str, spot: float, expiry: date, strike: float, *,
                    qty: int = 1) -> StrategySpec:
    """Long-dated (≥ 1Y) call as a leveraged stock substitute.

    Tagged ``leaps`` family so the advisor prioritises it for high-conviction
    multi-year bullish views. Factory is identical to ``long_call``; the family
    tag is what the strategy-engine analytics key off.
    """
    return StrategySpec(
        name="long_leaps_call", family="leaps", ticker=ticker, spot=spot,
        option_legs=[OptionLeg(kind="call", expiry=expiry, strike=strike, quantity=qty)],
        view_tag="bullish",
    )


# ---------------------------------------------------------------------------
# Registry — advisor uses this to enumerate the catalog
# ---------------------------------------------------------------------------

STRATEGY_CATALOG: dict[str, dict] = {
    "long_call":         {"family": "directional", "view": "bullish",   "desc": "Long call — leveraged long delta"},
    "long_put":          {"family": "directional", "view": "bearish",   "desc": "Long put — leveraged short delta"},
    "bull_call_spread":  {"family": "directional", "view": "bullish",   "desc": "Debit spread; capped upside, low premium"},
    "bull_put_spread":   {"family": "directional", "view": "bullish",   "desc": "Credit spread; income, capped loss"},
    "bear_call_spread":  {"family": "directional", "view": "bearish",   "desc": "Credit spread; income, capped loss"},
    "bear_put_spread":   {"family": "directional", "view": "bearish",   "desc": "Debit spread; capped downside, low premium"},
    "long_straddle":     {"family": "volatility",  "view": "vol_up",    "desc": "Long call+put ATM; long gamma/vega"},
    "long_strangle":     {"family": "volatility",  "view": "vol_up",    "desc": "Long OTM call+put; cheaper than straddle"},
    "short_straddle":    {"family": "volatility",  "view": "vol_down",  "desc": "Short ATM call+put; income, unlimited risk"},
    "short_strangle":    {"family": "volatility",  "view": "vol_down",  "desc": "Short OTM call+put; income, undefined risk"},
    "iron_condor":       {"family": "volatility",  "view": "vol_down",  "desc": "Range-bound income; defined risk"},
    "iron_butterfly":    {"family": "volatility",  "view": "vol_down",  "desc": "ATM-pinned income; defined risk"},
    "calendar_spread":   {"family": "volatility",  "view": "vol_up",    "desc": "Term-structure play; long far, short near"},
    "diagonal_spread":   {"family": "volatility",  "view": "bullish",   "desc": "Asymmetric calendar; light directional"},
    "covered_call":      {"family": "income",      "view": "neutral",   "desc": "Long stock + short OTM call; income"},
    "cash_secured_put":  {"family": "income",      "view": "bullish",   "desc": "Short OTM put; income or assignment"},
    "wheel":             {"family": "income",      "view": "bullish",   "desc": "CSP→CC cycle on a wanted underlying"},
    "protective_put":    {"family": "hedging",     "view": "bullish",   "desc": "Long stock + long put; insured downside"},
    "collar":            {"family": "hedging",     "view": "bullish",   "desc": "Long stock + put − call; zero-cost cap"},
    "risk_reversal":     {"family": "hedging",     "view": "bullish",   "desc": "Long call funded by short put"},
    "long_leaps_call":   {"family": "leaps",       "view": "bullish",   "desc": "Long-dated call; stock substitute"},
}
