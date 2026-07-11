"""Live equity market data via Financial Modeling Prep (FMP).

Honest scope (plan §2.9): FMP provides **spot, treasury rates, and history**
(from which we estimate correlation). It does **not** provide single-name implied
vol surfaces, dividends-as-a-curve, or borrow — so :meth:`vol_surface` returns
``None`` (the engine falls back to a user-supplied parametric skew) and dividends/
borrow default to user input. This is the realistic equity quoting workflow:
*live spot + rates, BYO surface*.

Two important operational notes:
  * The FMP **MCP** tools are only callable from an agent context, not from this
    server process; production uses FMP's HTTP API with an ``FMP_API_KEY``.
  * FMP's quote/history endpoints require a paid tier; without a key (or on a
    lower tier) the provider raises :class:`FMPUnavailable`, which callers should
    surface to the user rather than silently fabricating data.

The HTTP fetch is injectable (``getter``) so the adapter is fully unit-testable
offline.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from fcn.marketdata.correlation import Correlation
from fcn.marketdata.volsurface import VolSurface

FMP_BASE = "https://financialmodelingprep.com/stable"


class FMPUnavailable(RuntimeError):
    """Raised when FMP cannot be reached / is not entitled (no key, lower tier)."""


def _http_get(path: str, params: dict, api_key: str, timeout: float = 10.0):
    q = dict(params)
    q["apikey"] = api_key
    url = f"{FMP_BASE}/{path}?{urllib.parse.urlencode(q)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())
    except Exception as exc:  # network / HTTP error
        raise FMPUnavailable(f"FMP request failed: {exc}") from exc
    if isinstance(data, dict) and data.get("Error Message"):
        raise FMPUnavailable(data["Error Message"])
    return data


@dataclass
class FMPProvider:
    """MarketDataProvider backed by FMP HTTP (or an injected getter for tests)."""

    api_key: str | None = None
    rate: float = 0.04  # fallback risk-free; can be refreshed from treasury_rate()
    funding: float | None = None
    div_yields: dict[str, float] = field(default_factory=dict)
    borrows: dict[str, float] = field(default_factory=dict)
    user_surfaces: dict[str, VolSurface] = field(default_factory=dict)
    lookback_days: int = 252
    getter: Callable[[str, dict, str], object] | None = None

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.environ.get("FMP_API_KEY")
        self._get = self.getter or _http_get
        self._spot_cache: dict[str, float] = {}
        self._hist_cache: dict[str, np.ndarray] = {}

    def _call(self, path: str, params: dict):
        if not self.api_key:
            raise FMPUnavailable("no FMP_API_KEY set (and no getter injected)")
        return self._get(path, params, self.api_key)

    # --- MarketDataProvider protocol ---
    def spot(self, ticker: str) -> float:
        if ticker in self._spot_cache:
            return self._spot_cache[ticker]
        data = self._call("quote", {"symbol": ticker})
        rows = data if isinstance(data, list) else [data]
        if not rows or "price" not in rows[0]:
            raise FMPUnavailable(f"no quote for {ticker}")
        px = float(rows[0]["price"])
        self._spot_cache[ticker] = px
        return px

    def div_yield(self, ticker: str) -> float:
        return self.div_yields.get(ticker, 0.0)

    def borrow(self, ticker: str) -> float:
        return self.borrows.get(ticker, 0.0)

    def vol_surface(self, ticker: str) -> VolSurface | None:
        # FMP has no equity option/IV data -> fall back to a parametric skew.
        return self.user_surfaces.get(ticker)

    def risk_free_rate(self) -> float:
        return self.rate

    def funding_rate(self) -> float:
        return self.rate if self.funding is None else self.funding

    def history_returns(self, ticker: str) -> np.ndarray:
        if ticker in self._hist_cache:
            return self._hist_cache[ticker]
        data = self._call("historical-price-eod/light", {"symbol": ticker})
        rows = data["historical"] if isinstance(data, dict) and "historical" in data else data
        # the FMP "light" EOD endpoint returns the close under `price` (not `close`); accept both.
        closes = np.array(
            [float(r.get("close", r.get("price"))) for r in rows[: self.lookback_days]],
            dtype=float)
        closes = closes[::-1]  # FMP returns most-recent-first
        rets = np.diff(np.log(closes))
        self._hist_cache[ticker] = rets
        return rets

    def correlation(self, tickers: list[str]) -> Correlation:
        if len(tickers) == 1:
            return Correlation.uniform(1, 0.0)
        series = [self.history_returns(t) for t in tickers]
        n = min(len(s) for s in series)
        mat = np.column_stack([s[-n:] for s in series])
        return Correlation.from_returns(mat)

    def screen_universe(
        self,
        min_market_cap: float = 2e10,
        exchanges: tuple[str, ...] = ("NASDAQ", "NYSE", "AMEX"),
        include_stocks: bool = True,
        include_etf: bool = True,
        country: str = "US",
        limit: int = 3000,
    ) -> list[dict]:
        """US stocks + ETFs with market cap > ``min_market_cap`` via FMP company-screener.

        Returns ``[{ticker, name, marketCap, sector, isEtf, exchange}]`` sorted by
        market cap descending. ``isEtf`` is *not* passed to the API (so both stocks
        and funds come back); stock/ETF inclusion is filtered client-side. Raises
        :class:`FMPUnavailable` when the screener can't be reached or the key/tier is
        not entitled (callers surface this as HTTP 503).
        """
        params = {
            "marketCapMoreThan": int(min_market_cap),
            "country": country,
            "isActivelyTrading": "true",
            "limit": limit,
        }
        data = self._call("company-screener", params)
        rows = data if isinstance(data, list) else []
        wanted = {e.upper() for e in exchanges}
        out: list[dict] = []
        for r in rows:
            sym = r.get("symbol")
            if not sym:
                continue
            ex = (r.get("exchangeShortName") or r.get("exchange") or "").upper()
            if wanted and ex not in wanted:
                continue
            is_etf = bool(r.get("isEtf") or r.get("isFund"))
            if is_etf and not include_etf:
                continue
            if not is_etf and not include_stocks:
                continue
            mc = r.get("marketCap")
            out.append({
                "ticker": sym,
                "name": r.get("companyName") or sym,
                "marketCap": float(mc) if mc is not None else 0.0,
                "sector": r.get("sector") or ("ETF" if is_etf else "—"),
                "isEtf": is_etf,
                "exchange": ex,
            })
        out.sort(key=lambda d: d["marketCap"], reverse=True)
        return out

    def treasury_rate(self, tenor_field: str = "year1") -> float:
        """Refresh the risk-free rate from FMP treasury rates (best-effort)."""
        data = self._call("treasury-rates", {})
        rows = data if isinstance(data, list) else [data]
        if rows and tenor_field in rows[0]:
            self.rate = float(rows[0][tenor_field]) / 100.0
        return self.rate
