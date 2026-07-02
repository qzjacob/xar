"""Option liquidity & execution-cost model.

The desk must never recommend or build a structure that looks good on *mid*
but can't actually be filled. Every contract carries (or is modeled to carry) a
bid/ask spread, open interest and volume; from those we derive a single
liquidity read used everywhere downstream:

  * ``rel_spread`` — (ask−bid)/mid, the dominant slippage driver,
  * ``score`` 0-100 — blends spread tightness, open interest and volume,
  * ``tradable`` — a hard gate (a one-sided/zero-OI/blown-spread quote is not),
  * per-leg / per-strategy **slippage to enter** = crossing mid→touch
    (½·rel_spread·mark per contract), and the **execution net debit**
    (buy legs at the ask, sell legs at the bid).

Live contracts use their real quotes/OI/volume. Abstract (manual-mode) chains
have no real depth, so we MODEL a spread from moneyness & maturity and tag the
result ``source='modeled'`` — the dimension is still exercised offline, but the
UI discloses it isn't live market depth.

This module is pure/deterministic and has no network or pricing dependency, so
it is unit-testable in isolation and reusable by the engine, the advisor and
the chain-level overview.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

LiquiditySource = Literal["quoted", "modeled"]

# --- tunables (documented desk rules of thumb) ----------------------------
REL_SPREAD_CAP = 0.60          # clamp pathological/one-sided quotes here
_SPREAD_ZERO_AT = 0.20         # rel_spread ≥ 20% ⇒ spread score 0
_OI_FULL_AT = 5000             # OI giving a full open-interest score
_VOL_FULL_AT = 1000            # daily volume giving a full volume score
_TRADABLE_MIN_SCORE = 25.0
_TRADABLE_MAX_REL_SPREAD = 0.25
_TRADABLE_MIN_OI = 10          # only enforced on *quoted* contracts


@dataclass(frozen=True)
class Liquidity:
    """Liquidity read for a single option contract."""

    rel_spread: float                 # (ask−bid)/mid (modeled if no live quote)
    open_interest: int | None
    volume: int | None
    score: float                      # 0-100
    tradable: bool
    source: LiquiditySource
    spread_abs: float | None = None   # true (ask−bid) per share for quoted; None if modeled

    def to_dict(self) -> dict:
        return {
            "rel_spread": round(self.rel_spread, 4),
            "spread_abs": None if self.spread_abs is None else round(self.spread_abs, 4),
            "open_interest": self.open_interest,
            "volume": self.volume,
            "score": round(self.score, 1),
            "tradable": self.tradable,
            "source": self.source,
        }


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _safe_count(x) -> int:
    """Coerce an OI/volume to a non-negative finite int (bad feed data → 0).

    Guards math.log10 against negative/NaN/None depth that a provider glitch can
    inject (a negative would otherwise raise a math-domain ValueError)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0
    return int(v) if math.isfinite(v) and v > 0 else 0


def _modeled_rel_spread(spot: float, strike: float, t: float) -> float:
    """Synthetic relative spread for a name with no live depth.

    ATM short-dated is tightest (~3%); spreads widen with |log-moneyness|
    (wings) and a little with maturity. Clamped to ``REL_SPREAD_CAP``.
    """
    if spot <= 0 or strike <= 0:
        return REL_SPREAD_CAP
    logm = abs(math.log(strike / spot))
    rel = 0.03 + 0.55 * logm + 0.015 * math.sqrt(max(t, 0.0))
    return _clamp(rel, 0.02, REL_SPREAD_CAP)


def _modeled_oi_volume(spot: float, strike: float, t: float) -> tuple[int, int]:
    """Synthetic OI / daily volume that decays away from the money."""
    if spot <= 0 or strike <= 0:
        return 1, 0
    logm = abs(math.log(strike / spot))
    oi = int(round(4000 * math.exp(-6.0 * logm)))
    vol = int(round(800 * math.exp(-7.0 * logm)))
    return max(oi, 1), max(vol, 0)


def liquidity_score(rel_spread: float, oi: int | None, volume: int | None) -> float:
    """Blend spread tightness (50%), open interest (30%), volume (20%)."""
    rel = rel_spread if math.isfinite(rel_spread) else REL_SPREAD_CAP
    s_spread = _clamp(1.0 - rel / _SPREAD_ZERO_AT, 0.0, 1.0)
    s_oi = _clamp(math.log10(_safe_count(oi) + 1) / math.log10(_OI_FULL_AT), 0.0, 1.0)
    s_vol = _clamp(math.log10(_safe_count(volume) + 1) / math.log10(_VOL_FULL_AT), 0.0, 1.0)
    return 100.0 * (0.5 * s_spread + 0.3 * s_oi + 0.2 * s_vol)


def contract_liquidity(contract, spot: float, t: float) -> Liquidity:
    """Liquidity read for one :class:`fcn.options.chain.OptionContract`.

    Uses the real two-sided quote + OI/volume when the contract is live and
    quoted; otherwise models the spread/OI/volume from moneyness & maturity.
    """
    bid, ask = contract.bid, contract.ask
    has_quote = (
        getattr(contract, "source", None) == "live"
        and bid is not None and ask is not None and ask >= bid and bid > 0
    )
    if has_quote:
        mid = 0.5 * (bid + ask)
        spread_abs = ask - bid                       # true touch width (unclamped)
        rel = _clamp(spread_abs / mid, 0.0, REL_SPREAD_CAP)
        oi, vol = contract.open_interest, contract.volume
        score = liquidity_score(rel, oi, vol)
        # Require real depth: a tight quote alone isn't proof you can get filled.
        # A live contract with NO open interest and NO volume is not tradable,
        # even if its (possibly stale) bid/ask looks tight.
        has_depth = (oi is not None and oi >= _TRADABLE_MIN_OI) or (vol is not None and vol > 0)
        tradable = (
            score >= _TRADABLE_MIN_SCORE
            and rel <= _TRADABLE_MAX_REL_SPREAD
            and has_depth
        )
        return Liquidity(rel, oi, vol, score, tradable, "quoted", spread_abs=spread_abs)
    # Modeled (abstract chain / quote-less live contract).
    rel = _modeled_rel_spread(spot, contract.strike, t)
    oi, vol = _modeled_oi_volume(spot, contract.strike, t)
    score = liquidity_score(rel, oi, vol)
    tradable = score >= _TRADABLE_MIN_SCORE and rel <= _TRADABLE_MAX_REL_SPREAD
    return Liquidity(rel, oi, vol, score, tradable, "modeled")


def half_spread_cost(rel_spread: float, mark: float) -> float:
    """Per-share cost of crossing mid → touch (½ the spread)."""
    return 0.5 * rel_spread * abs(mark)


@dataclass(frozen=True)
class StrategyLiquidity:
    """Strategy-level liquidity roll-up across all option legs."""

    score: float                      # 0-100 (worst-leg-weighted)
    tradable: bool                    # every leg tradable
    worst_rel_spread: float
    min_open_interest: int | None
    total_volume: int | None
    slippage: float                   # $ to enter (cross mid→touch, all legs)
    slippage_pct: float               # slippage / |mid net debit| (or notional)
    source: LiquiditySource           # 'modeled' if any leg is modeled
    multiplier: float                 # liquidity-adjustment applied to fit score

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 1),
            "label": fill_quality_label(self.score, self.tradable),
            "tradable": self.tradable,
            "worst_rel_spread": round(self.worst_rel_spread, 4),
            "min_open_interest": self.min_open_interest,
            "total_volume": self.total_volume,
            "slippage": round(self.slippage, 2),
            "slippage_pct": round(self.slippage_pct, 4),
            "source": self.source,
            "multiplier": round(self.multiplier, 3),
        }


def aggregate_liquidity(
    leg_liquidities: list[Liquidity], slippage: float, mid_net_debit: float,
    spot: float, contract_size: int = 100,
) -> StrategyLiquidity:
    """Roll up per-leg liquidity into a strategy-level read.

    The strategy is only as fillable as its WORST leg, so the score leans on the
    minimum (0.6·min + 0.4·mean) and ``tradable`` requires every leg tradable.
    """
    if not leg_liquidities:
        # Pure-stock position: liquid by assumption (equities, not options).
        return StrategyLiquidity(100.0, True, 0.0, None, None, 0.0, 0.0, "quoted", 1.0)
    scores = [l.score for l in leg_liquidities]
    score = 0.6 * min(scores) + 0.4 * (sum(scores) / len(scores))
    tradable = all(l.tradable for l in leg_liquidities)
    worst_rel = max(l.rel_spread for l in leg_liquidities)
    ois = [l.open_interest for l in leg_liquidities if l.open_interest is not None]
    vols = [l.volume for l in leg_liquidities if l.volume is not None]
    source: LiquiditySource = "modeled" if any(l.source == "modeled" for l in leg_liquidities) else "quoted"
    # Slippage as a fraction of capital committed. Reference floors prevent a
    # div-by-zero / misleading 0% on ~zero-cost or degenerate-spot structures:
    # 1%-of-notional, the slippage itself, and an absolute $1, whichever largest.
    ref = max(abs(mid_net_debit), 0.01 * spot * contract_size, abs(slippage), 1.0)
    slip_pct = slippage / ref
    mult = liquidity_multiplier(score, tradable)
    return StrategyLiquidity(
        score=score, tradable=tradable, worst_rel_spread=worst_rel,
        min_open_interest=(min(ois) if ois else None),
        total_volume=(sum(vols) if vols else None),
        slippage=slippage, slippage_pct=slip_pct, source=source, multiplier=mult,
    )


def liquidity_multiplier(score: float, tradable: bool) -> float:
    """Map a 0-100 liquidity score to a fit-score multiplier in [0.25, 1.0].

    Untradable structures are capped at 0.3 so they sink below any genuinely
    fillable alternative when the advisor re-ranks after costs.
    """
    mult = 0.25 + 0.75 * _clamp(score / 100.0, 0.0, 1.0)
    if not tradable:
        mult = min(mult, 0.30)
    return mult


def fill_quality_label(score: float, tradable: bool) -> str:
    if not tradable:
        return "untradable"
    if score >= 75:
        return "deep"
    if score >= 50:
        return "liquid"
    if score >= 30:
        return "thin"
    return "very thin"
