"""Glue between API requests and the engine: build snapshots, engines, contexts."""

from __future__ import annotations

import numpy as np

from fcn.api.schemas import MarketInput, MCInput
from fcn.core.rng import RNGSpec
from fcn.marketdata.correlation import Correlation
from fcn.marketdata.provider import ManualProvider, assemble_snapshot
from fcn.marketdata.snapshot import MarketSnapshot
from fcn.marketdata.volsurface import ParametricSkewSurface
from fcn.pricing.mcengine import MCConfig, MCEngine
from fcn.product.enums import CouponType
from fcn.product.termsheet import TermSheet


def build_snapshot(ts: TermSheet, market: MarketInput) -> MarketSnapshot:
    if market.source == "live":
        return _build_snapshot_live(ts, market)
    surfaces = {
        a.ticker: ParametricSkewSurface(atm=a.atm_vol, slope=a.skew_slope, curv=a.skew_curv)
        for a in market.assets
    }
    spots = {a.ticker: a.spot for a in market.assets}
    divs = {a.ticker: a.div_yield for a in market.assets}
    borrows = {a.ticker: a.borrow for a in market.assets}
    tickers = [u.ticker for u in ts.underlyings]
    if market.correlation is not None:
        corr = Correlation(np.array(market.correlation, dtype=float))
    else:
        corr = Correlation.uniform(len(tickers), market.rho if market.rho is not None else 0.0)
    provider = ManualProvider(
        spots=spots, surfaces=surfaces, rate=market.rate, funding=market.funding,
        div_yields=divs, borrows=borrows, corr=corr,
    )
    snap = assemble_snapshot(provider, ts, market.asof)
    if market.issuer_spread:
        snap = snap.shock_discount_rate(market.issuer_spread)  # CVA-lite: discount on issuer funding
    return snap


def _build_snapshot_live(ts: TermSheet, market: MarketInput) -> MarketSnapshot:
    """Build a snapshot from live Massive data (real IV surfaces + correlation).

    Per-name: live skew surface when the chain has enough data, else the user's
    parametric skew. Spot is always live (failure -> MassiveUnavailable -> 503).
    """
    from datetime import date

    from fcn.marketdata.curve import DiscountCurve, ForwardCurve
    from fcn.marketdata.massive import MassiveProvider
    from fcn.marketdata.snapshot import AssetMarket, MarketSnapshot

    user = {a.ticker: a for a in market.assets}
    try:
        asof = date.fromisoformat(market.asof)
    except (ValueError, TypeError):
        asof = None
    div = {t: a.div_yield for t, a in user.items()}
    borrow = {t: a.borrow for t, a in user.items()}
    years = max(0.5, (ts.maturity - ts.strike_date).days / 365.0)
    prov = MassiveProvider(
        rate=market.rate, funding=market.funding, div_yields=div, borrows=borrow,
        asof=asof, max_maturity_years=years,
    )

    assets = []
    for u in ts.underlyings:
        spot = prov.spot(u.ticker)  # live; raises MassiveUnavailable on failure
        surface = prov.vol_surface(u.ticker)
        if surface is None:
            ai = user.get(u.ticker)
            surface = (
                ParametricSkewSurface(atm=ai.atm_vol, slope=ai.skew_slope, curv=ai.skew_curv)
                if ai
                else ParametricSkewSurface(atm=0.30)
            )
        assets.append(
            AssetMarket(
                ticker=u.ticker, spot=spot,
                initial_fixing=u.initial_fixing if u.initial_fixing is not None else spot,
                forward=ForwardCurve(
                    spot=spot, rate=market.rate,
                    div_yield=prov.div_yield(u.ticker), borrow=prov.borrow(u.ticker),
                ),
                surface=surface,
            )
        )
    tickers = [u.ticker for u in ts.underlyings]
    corr = (
        Correlation(np.array(market.correlation, dtype=float))
        if market.correlation is not None
        else prov.correlation(tickers)
    )
    funding = market.rate if market.funding is None else market.funding
    return MarketSnapshot(
        asof=market.asof, assets=tuple(assets),
        disc=DiscountCurve(funding + market.issuer_spread),  # CVA-lite issuer discounting
        correlation=corr,
    )


def make_engine(mc: MCInput) -> MCEngine:
    return MCEngine(
        config=MCConfig(
            n_paths=mc.n_paths,
            rng=RNGSpec(seed=mc.seed, method=mc.method, antithetic=mc.antithetic),
            local_vol=mc.local_vol,
        )
    )


def variant_label(ts: TermSheet) -> str:
    if ts.participation is not None:
        return "SharkFin" if ts.participation.style.value == "sharkfin" else "Booster / Airbag"
    if ts.coupon.accrual_snowball:
        return "Snowball (雪球)"
    if ts.coupon.type is CouponType.CONDITIONAL:
        return "Phoenix (memory)" if ts.coupon.memory else "Phoenix"
    return "Classic FCN"


def product_context(ts: TermSheet) -> dict:
    return {
        "variant": variant_label(ts),
        "n_assets": ts.n_assets,
        "basket": ts.basket_mode.value,
        "currency": ts.currency,
        "notional": ts.notional,
        "maturity": ts.maturity.isoformat(),
        "tickers": [u.ticker for u in ts.underlyings],
    }
