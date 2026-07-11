"""FMPLiveProvider — a MarketDataProvider over real FMP data for the no-manual-input pages.

Market Read / Underlying Finder / Options Desk all need *latest real data with zero manual
price input*. Massive (implied-vol surfaces) is not always entitled, so the honest live
source is FMP: **real spot + annualised realized vol** (21d/63d/252d windows → an ATM *term
structure*) wrapped in the desk's parametric put-skew. Callers should label vols
"realized" (``vol_basis``) — they are a proxy for implied, not an option-market quote.

Also supplies ``monthly_samples`` (month-end spot + trailing realized vol) for the market
read's 择时 monthly-trend view, and passes ``screen_universe`` through for the finder's
full US stock+ETF universe. Dual-class tickers gated by FMP's tier (GOOG 402 → GOOGL)
resolve via the same alias map the quote desk uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from fcn.marketdata.correlation import Correlation
from fcn.marketdata.volsurface import ParametricSkewSurface, VolSurface
from fcn.service.market_resolver import _resolve_symbol, realized_vol

_RATE_FALLBACK = 0.045


@dataclass
class FMPLiveProvider:
    """Real spot + realized-vol term surface from FMP; rate from the treasury curve."""

    rate: float | None = None          # None → live 1Y treasury (fallback _RATE_FALLBACK)
    funding: float | None = None       # defaults to rate
    skew_slope: float = -0.4           # desk-standard parametric put skew around the realized ATM
    skew_curv: float = 0.3
    fmp: object | None = None          # injectable FMPProvider for offline tests
    vol_basis: str = "realized"        # honest label consumed by market read / UIs

    _syms: dict[str, str] = field(default_factory=dict, repr=False)      # requested → effective
    _surfaces: dict[str, VolSurface | None] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.fmp is None:
            from fcn.marketdata.fmp import FMPProvider

            self.fmp = FMPProvider()
        if self.rate is None:
            try:
                self.rate = float(self.fmp.treasury_rate())   # live 1Y treasury
            except Exception:  # noqa: BLE001 — offline / not entitled
                self.rate = _RATE_FALLBACK

    # --- symbol resolution (alias-aware: GOOG→GOOGL, BRK.B→BRK-B, …) -----------
    def _sym(self, ticker: str) -> str:
        t = ticker.strip().upper()
        if t not in self._syms:
            sym, spot = _resolve_symbol(self.fmp, t)   # raises when truly unresolvable
            self._syms[t] = sym
            # warm the spot cache under the effective symbol (already done by prov.spot)
            _ = spot
        return self._syms[t]

    # --- MarketDataProvider protocol -------------------------------------------
    def spot(self, ticker: str) -> float:
        return float(self.fmp.spot(self._sym(ticker)))

    def div_yield(self, ticker: str) -> float:
        try:
            return float(self.fmp.div_yield(self._sym(ticker)))
        except Exception:  # noqa: BLE001
            return 0.0

    def borrow(self, ticker: str) -> float:
        try:
            return float(self.fmp.borrow(self._sym(ticker)))
        except Exception:  # noqa: BLE001
            return 0.0

    def vol_surface(self, ticker: str) -> VolSurface | None:
        """Parametric skew around a realized-vol ATM *term structure* (21d/63d/252d).

        Returns ``None`` when the name has too little history — callers skip it
        (never fabricate a flat guess here; the desk-level fallback is explicit).
        """
        t = ticker.strip().upper()
        if t in self._surfaces:
            return self._surfaces[t]
        surface: VolSurface | None = None
        try:
            rets = self.fmp.history_returns(self._sym(t))
            rv_1m = realized_vol(rets, window=21)
            rv_3m = realized_vol(rets, window=63)
            rv_1y = realized_vol(rets, window=252)
            anchors = [(1.0 / 12.0, rv_1m), (0.25, rv_3m), (1.0, rv_1y)]
            term = tuple((tt, v) for tt, v in anchors if v is not None)
            if term:
                atm = term[min(1, len(term) - 1)][1]   # prefer the 3M anchor as headline ATM
                surface = ParametricSkewSurface(
                    atm=atm, slope=self.skew_slope, curv=self.skew_curv, term=term,
                )
        except Exception:  # noqa: BLE001 — history unavailable → no surface → caller skips
            surface = None
        self._surfaces[t] = surface
        return surface

    def risk_free_rate(self) -> float:
        return float(self.rate)

    def funding_rate(self) -> float:
        return float(self.rate if self.funding is None else self.funding)

    def correlation(self, tickers: list[str]) -> Correlation:
        if len(tickers) <= 1:
            return Correlation.uniform(max(1, len(tickers)), 0.0)
        return self.fmp.correlation([self._sym(t) for t in tickers])

    # --- extras consumed by market read / finder --------------------------------
    def _daily_closes(self, ticker: str) -> np.ndarray:
        """Daily closes oldest→newest (market read uses this for realized-vol metrics)."""
        _, closes = self.fmp.history_closes(self._sym(ticker))
        return closes

    def monthly_samples(self, ticker: str, months: int = 7) -> list[dict]:
        """Month-end samples for the 择时 trend view: [{month, spot, rv21}] oldest→newest.

        ``rv21`` is the trailing 21-day annualised realized vol *ending at that
        month-end* — comparing it month over month shows whether vol is building
        or fading, which is the timing signal for income vs participation notes.
        """
        dates, closes = self.fmp.history_closes(self._sym(ticker))
        if len(closes) < 25:
            return []
        # last index per calendar month (dates are ISO yyyy-mm-dd, oldest→newest)
        last_idx: dict[str, int] = {}
        for i, d in enumerate(dates):
            if len(d) >= 7:
                last_idx[d[:7]] = i
        out: list[dict] = []
        for month in sorted(last_idx)[-months:]:
            i = last_idx[month]
            if i < 22:   # need a 21-return trailing window
                continue
            window = np.diff(np.log(closes[i - 21 : i + 1]))
            rv = float(np.std(window, ddof=1) * np.sqrt(252))
            out.append({"month": month, "spot": round(float(closes[i]), 2), "rv21": round(rv, 4)})
        return out

    def screen_universe(self, **kwargs) -> list[dict]:
        """Full US stocks + ETFs above a market-cap floor (FMP company screener)."""
        return self.fmp.screen_universe(**kwargs)
