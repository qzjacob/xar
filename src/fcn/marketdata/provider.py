"""Market-data providers and snapshot assembly.

``MarketDataProvider`` is the seam between the engine and the outside world. The
shipped :class:`ManualProvider` takes explicit inputs (the desk workflow and the
basis of every test); the live ``MCPFMPProvider`` (Phase 5) will fetch spot /
dividends / history via FMP and fall back to a parametric skew for the vol surface
(equity IV surfaces are not available from free feeds, plan §2.9). The
:class:`ManualOverrideProvider` lets a UI override any field of a base provider —
override always wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from fcn.marketdata.correlation import Correlation
from fcn.marketdata.curve import ForwardCurve
from fcn.marketdata.snapshot import AssetMarket, MarketSnapshot
from fcn.marketdata.volsurface import ParametricSkewSurface, VolSurface


class MarketDataProvider(Protocol):
    def spot(self, ticker: str) -> float: ...
    def div_yield(self, ticker: str) -> float: ...
    def borrow(self, ticker: str) -> float: ...
    def vol_surface(self, ticker: str) -> VolSurface | None: ...
    def risk_free_rate(self) -> float: ...
    def funding_rate(self) -> float: ...
    def correlation(self, tickers: list[str]) -> Correlation: ...


@dataclass
class ManualProvider:
    """All inputs supplied explicitly. The primary path for desks and tests."""

    spots: dict[str, float]
    surfaces: dict[str, VolSurface]
    rate: float = 0.0
    funding: float | None = None  # defaults to ``rate`` if None
    div_yields: dict[str, float] = field(default_factory=dict)
    borrows: dict[str, float] = field(default_factory=dict)
    corr: Correlation | None = None

    def spot(self, ticker: str) -> float:
        return self.spots[ticker]

    def div_yield(self, ticker: str) -> float:
        return self.div_yields.get(ticker, 0.0)

    def borrow(self, ticker: str) -> float:
        return self.borrows.get(ticker, 0.0)

    def vol_surface(self, ticker: str) -> VolSurface | None:
        return self.surfaces.get(ticker)

    def risk_free_rate(self) -> float:
        return self.rate

    def funding_rate(self) -> float:
        return self.rate if self.funding is None else self.funding

    def correlation(self, tickers: list[str]) -> Correlation:
        if self.corr is not None:
            return self.corr
        return Correlation.uniform(len(tickers), 0.0)


@dataclass
class ManualOverrideProvider:
    """Wrap a base provider; any value present in ``overrides`` wins."""

    base: MarketDataProvider
    spots: dict[str, float] = field(default_factory=dict)
    surfaces: dict[str, VolSurface] = field(default_factory=dict)
    div_yields: dict[str, float] = field(default_factory=dict)
    borrows: dict[str, float] = field(default_factory=dict)
    rate: float | None = None
    funding: float | None = None
    corr: Correlation | None = None

    def spot(self, ticker: str) -> float:
        return self.spots.get(ticker, self.base.spot(ticker))

    def div_yield(self, ticker: str) -> float:
        return self.div_yields.get(ticker, self.base.div_yield(ticker))

    def borrow(self, ticker: str) -> float:
        return self.borrows.get(ticker, self.base.borrow(ticker))

    def vol_surface(self, ticker: str) -> VolSurface | None:
        return self.surfaces.get(ticker, self.base.vol_surface(ticker))

    def risk_free_rate(self) -> float:
        return self.base.risk_free_rate() if self.rate is None else self.rate

    def funding_rate(self) -> float:
        return self.base.funding_rate() if self.funding is None else self.funding

    def correlation(self, tickers: list[str]) -> Correlation:
        return self.corr if self.corr is not None else self.base.correlation(tickers)


def assemble_snapshot(provider: MarketDataProvider, termsheet, asof: str) -> MarketSnapshot:
    """Resolve a :class:`MarketSnapshot` for a term sheet from a provider.

    Initial fixings default to spot; a missing vol surface falls back to a flat
    parametric surface at a conservative 30% ATM (and is flagged as a default by
    callers if needed).
    """
    from fcn.marketdata.curve import DiscountCurve

    rate = provider.risk_free_rate()
    tickers = [u.ticker for u in termsheet.underlyings]
    assets = []
    for u in termsheet.underlyings:
        spot = provider.spot(u.ticker)
        surface = provider.vol_surface(u.ticker)
        if surface is None:
            surface = ParametricSkewSurface(atm=0.30)
        assets.append(
            AssetMarket(
                ticker=u.ticker,
                spot=spot,
                initial_fixing=u.initial_fixing if u.initial_fixing is not None else spot,
                forward=ForwardCurve(
                    spot=spot,
                    rate=rate,
                    div_yield=provider.div_yield(u.ticker),
                    borrow=provider.borrow(u.ticker),
                ),
                surface=surface,
            )
        )
    return MarketSnapshot(
        asof=asof,
        assets=tuple(assets),
        disc=DiscountCurve(rate=provider.funding_rate()),
        correlation=provider.correlation(tickers),
    )
