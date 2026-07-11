"""Underlying Finder — rank a universe of single names by indicative FCN coupon.

The desk question this answers: *"fix the protection barrier and tenor, then which
underlyings pay the richest coupon?"* For a single-name, European-KI (at-maturity),
fixed-coupon, no-autocall FCN the value is closed-form
(:func:`fcn.analytics.closed_form.single_name_european_note`) and the PV is **affine
in the coupon**, so the fair coupon is one division per name — microseconds. Ranking
hundreds of names is therefore bottlenecked by **fetching one option chain per name**,
which :func:`fcn.marketdata.cache.fetch_concurrent` parallelises and
:data:`fcn.marketdata.cache.MARKET_CACHE` memoises for a few minutes.

Honest scope (mirrors the at-maturity default of the Quotation Desk):
  * Closed-form screen = European/at-maturity KI, fixed coupon, **no autocall**,
    single underlying. Richer structures (Phoenix/Snowball/autocall/participation)
    are priced by Monte Carlo in the Desk — open a ranked name there to go deeper.
  * Per-name ``sigma`` is sampled at the **barrier** (skew-aware), not ATM.
  * Dividends/borrow default to 0 unless the provider supplies them.
  * Coverage is capped at the most liquid ``max_candidates`` by market cap and the
    result reports ``universe_size`` / ``considered`` / ``ranked_count`` / ``skipped``
    so truncation is never silent.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass

import numpy as np

from fcn.analytics.closed_form import (
    prob_below_barrier_european,
    single_name_european_note,
)
from fcn.marketdata.cache import MARKET_CACHE, fetch_concurrent

_FREQ_PER_YEAR = {"monthly": 12, "quarterly": 4, "semiannual": 2, "annual": 1}
_RANK_KEYS = {  # rank key -> (result field, descending?)
    "coupon": ("coupon", True),
    "strike": ("strike", False),  # lowest fair strike first = biggest downside buffer at the same coupon
    "prob_capital_at_risk": ("prob_capital_at_risk", False),  # safest first
    "iv_at_barrier": ("iv_at_barrier", True),
    "marketCap": ("marketCap", True),
}


@dataclass(frozen=True)
class RankStructure:
    """The fixed note structure every candidate is screened against."""

    product: str = "fcn"  # only the closed-form FCN screen is supported here
    tenor_months: int = 6
    frequency: str = "quarterly"  # coupon / observation frequency (观察频率)
    protection_pct: float = 0.70  # KI / protection barrier as a fraction of start (敲入线)
    strike_pct: float = 1.0  # conversion strike as a fraction of start (行权价)
    reoffer_pct: float = 1.0  # issue price / value as a fraction of par
    div_yield: float = 0.0  # default per-name dividend yield (provider may override)
    borrow: float = 0.0
    # Desk-mirroring extensions (Quotation Desk param names). Any of these set → the
    # per-name pricer switches from the closed-form to the finder mini-MC (finder_mc).
    ko_pct: float | None = None      # 敲出线 autocall barrier as fraction of start; None = no autocall
    ki_style: str = "european"       # 敲入类型: none (无保护) | european | american
    coupon_pa: float | None = None   # fixed annual coupon → solve the fair strike per name


def _coupon_schedule(tenor_years: float, frequency: str) -> tuple[list[float], list[float]]:
    ppy = _FREQ_PER_YEAR.get(frequency, 4)
    n = max(1, int(round(ppy * tenor_years)))
    tau = 1.0 / ppy
    times = [min((i + 1) * tau, tenor_years) for i in range(n)]
    return times, [tau] * n


def _barrier_vol(provider, ticker: str, t: float, log_moneyness: float) -> float | None:
    """Skew-aware vol at the barrier: Massive's single-expiry fast path when present,
    else sample the provider's full surface (Manual/parametric)."""
    point_vol = getattr(provider, "point_vol", None)
    if callable(point_vol):
        return point_vol(ticker, t, log_moneyness)
    surface = provider.vol_surface(ticker)
    if surface is None:
        return None
    return float(surface.implied_vol(np.array([log_moneyness]), t)[0])


def _price_one(provider, ticker: str, structure: RankStructure) -> dict | None:
    """Solve the indicative fair coupon (or strike) for one name. Returns ``None``
    (skip) when no usable live surface exists for the name."""
    spot = provider.spot(ticker)
    if not spot or spot <= 0:
        return None
    t = structure.tenor_months / 12.0
    # sample sigma at the effective downside barrier (无保护 → the strike itself)
    barrier = structure.strike_pct if structure.ki_style == "none" else structure.protection_pct
    sigma = _barrier_vol(provider, ticker, t, math.log(barrier))
    if sigma is None or sigma <= 0:
        return None

    q = provider.div_yield(ticker) or structure.div_yield
    borrow = provider.borrow(ticker) or structure.borrow
    rate = provider.risk_free_rate()
    funding = provider.funding_rate()

    # Desk-mirroring structures (autocall / American KI / solve-strike) go through the
    # finder mini-MC; the plain no-autocall European-KI coupon screen stays closed-form.
    if (structure.ko_pct is not None or structure.ki_style != "european"
            or structure.coupon_pa is not None):
        from fcn.service.finder_mc import screen_price

        priced = screen_price(
            spot=spot, sigma=sigma, rate=rate, funding=funding,
            div_yield=q, borrow=borrow,
            tenor_years=t, frequency=structure.frequency,
            ko=structure.ko_pct, ki=structure.protection_pct, ki_style=structure.ki_style,
            strike=structure.strike_pct, reoffer=structure.reoffer_pct,
            coupon_pa=structure.coupon_pa,
        )
        if priced is None:
            return None
        return {"ticker": ticker, "spot": round(float(spot), 4), **priced}

    times, taus = _coupon_schedule(t, structure.frequency)

    value = single_name_european_note(
        spot=spot, initial_fixing=spot,
        ki_fraction=structure.protection_pct, strike_fraction=structure.strike_pct,
        sigma=sigma, r=rate, q=q, borrow=borrow, funding=funding,
        coupon_rate=1.0, coupon_times=times, coupon_taus=taus, maturity=t, notional=100.0,
    )
    if value.pv_coupons <= 0:
        return None
    coupon = (structure.reoffer_pct * 100.0 - value.pv_redemption) / value.pv_coupons
    p_loss = prob_below_barrier_european(
        spot, structure.protection_pct * spot, sigma, t, rate, q, borrow
    )
    return {
        "ticker": ticker,
        "spot": round(float(spot), 4),
        "coupon": float(coupon),
        "iv_at_barrier": round(float(sigma), 4),
        "prob_capital_at_risk": round(float(p_loss), 4),
        "buffer_pct": round(1.0 - structure.protection_pct, 4),
    }


def _normalise_universe(universe) -> list[dict]:
    """Accept either ticker strings or screener dicts; return metadata dicts."""
    out = []
    for u in universe:
        if isinstance(u, str):
            out.append({"ticker": u, "name": u, "marketCap": 0.0, "sector": "—", "isEtf": False})
        else:
            out.append(dict(u))
    return out


def rank_underlyings(
    provider,
    structure: RankStructure,
    *,
    universe: list | None = None,
    top_n: int = 10,
    rank_by: str = "coupon",
    filters: dict | None = None,
    max_candidates: int = 200,
    max_workers: int = 8,
    use_cache: bool = True,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Rank ``universe`` against ``structure`` by ``rank_by`` (default coupon desc).

    ``provider`` supplies spot + (barrier) vol + rate/funding/div/borrow. ``universe``
    is a list of screener dicts (``{ticker,name,marketCap,sector,isEtf}``) or bare
    tickers; if ``None`` and the provider exposes ``screen_universe`` it is called.
    """
    if universe is None:
        screen = getattr(provider, "screen_universe", None)
        universe = screen() if callable(screen) else []
    meta = _normalise_universe(universe)
    universe_size = len(meta)
    # Metadata-only filters (kind/sector) apply BEFORE the liquidity truncation — otherwise
    # a 仅ETF screen would first cap to the top-N by market cap (mostly stocks) and only
    # then filter, returning far fewer ETFs than the universe actually holds.
    _f = filters or {}
    kind, sector = _f.get("kind"), _f.get("sector")
    if kind == "stock":
        meta = [m for m in meta if not m.get("isEtf")]
    elif kind == "etf":
        meta = [m for m in meta if m.get("isEtf")]
    if sector:
        meta = [m for m in meta if m.get("sector") == sector]
    # Cap to the most liquid names (screener is already market-cap sorted; re-sort defensively).
    meta.sort(key=lambda d: d.get("marketCap", 0.0), reverse=True)
    considered = meta[: max(1, max_candidates)] if max_candidates else meta

    # Cache key includes the provider's vol basis (realized FMP vs implied Massive) so
    # auto and live jobs never share per-name results; manual/tests pass use_cache=False.
    sig = (structure, getattr(provider, "vol_basis", "implied"),
           round(provider.risk_free_rate(), 6), round(provider.funding_rate(), 6))

    def price(meta_row: dict):
        ticker = meta_row["ticker"]
        if use_cache:
            return MARKET_CACHE.get_or_compute(
                ("rank", ticker, sig), lambda: _price_one(provider, ticker, structure)
            )
        return _price_one(provider, ticker, structure)

    results = fetch_concurrent(considered, price, max_workers=max_workers, on_progress=on_progress)

    by_ticker = {m["ticker"]: m for m in considered}
    ranked: list[dict] = []
    skipped: list[dict] = []
    for meta_row, priced, error in results:
        ticker = meta_row["ticker"]
        if error is not None:
            skipped.append({"ticker": ticker, "reason": str(error)[:160]})
            continue
        if priced is None:
            skipped.append({"ticker": ticker, "reason": "no live option surface"})
            continue
        m = by_ticker.get(ticker, meta_row)
        ranked.append({
            **priced,
            "name": m.get("name", ticker),
            "sector": m.get("sector", "—"),
            "isEtf": bool(m.get("isEtf", False)),
            "marketCap": float(m.get("marketCap", 0.0)),
        })

    ranked = _apply_filters(ranked, filters or {})
    field, descending = _RANK_KEYS.get(rank_by, _RANK_KEYS["coupon"])
    if rank_by == "strike":
        # Demote unbracketed solves (clamped at the [0.40, 1.20] bounds): a strike where
        # the target PV was NOT reachable must never outrank a genuine solve — ascending
        # order would otherwise put exactly the failed names at #1.
        ranked.sort(key=lambda r: (0 if r.get("bracketed", True) else 1, r.get(field, 9.9)))
    else:
        ranked.sort(key=lambda r: r.get(field, 0.0), reverse=descending)
    ranked = ranked[: max(1, top_n)]
    for i, row in enumerate(ranked, start=1):
        row["rank"] = i

    return {
        "structure": asdict(structure),
        "ranked": ranked,
        "universe_size": universe_size,
        "considered": len(considered),
        "ranked_count": len(ranked),
        "skipped": skipped,
        "rank_by": rank_by if rank_by in _RANK_KEYS else "coupon",
        "top_n": top_n,
        "liquidity_note": (
            "Underlying size uses market cap as a liquidity proxy; secondary-market "
            "and option-hedge liquidity are not modeled in this screen. Use the "
            "Options Desk for per-contract tradability/slippage."
        ),
    }


def _apply_filters(rows: list[dict], filters: dict) -> list[dict]:
    min_coupon = filters.get("min_coupon")
    max_prob_loss = filters.get("max_prob_loss")
    sector = filters.get("sector")
    kind = filters.get("kind")  # "stock" | "etf" | "all"/None
    out = []
    for r in rows:
        # min_coupon only applies to coupon rows — strike-solve rows carry no coupon and
        # must not be filtered out by a defaulted 0.0
        if min_coupon is not None and "coupon" in r and r["coupon"] < min_coupon:
            continue
        if max_prob_loss is not None and r.get("prob_capital_at_risk", 0.0) > max_prob_loss:
            continue
        if sector and r.get("sector") != sector:
            continue
        if kind == "stock" and r.get("isEtf"):
            continue
        if kind == "etf" and not r.get("isEtf"):
            continue
        out.append(r)
    return out
