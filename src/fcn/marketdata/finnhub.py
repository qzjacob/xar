"""Live equity reference data via Finnhub (finnhub.io) — the Underlying Finder's universe.

Why Finnhub: the available FMP tier paywalls the stock screener (HTTP 402) and the
Finnhub plan exposes no screener or index constituents either, but it *does* serve
live **market cap + sector** (`/stock/profile2`) and **quotes** (`/quote`). So the
Finder universe is a curated candidate list of optionable US large caps + ETFs
(``universe_seed.json``, generated from live Finnhub data by ``scripts/build_universe.py``),
which this provider loads, filters by market cap, and — optionally — refreshes live.

Scope honesty: Finnhub profile2 returns market cap in *local currency* for foreign
ADRs and nothing for ETFs, so the seed excludes ADRs and seeds ETF AUM from a table
(see the generator). Option implied vol still comes from Massive; this provider does
not supply a vol surface. The HTTP getter is injectable for offline tests, and the
token is sent as a header (never in the URL) so it can't leak into error messages.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fcn.marketdata.cache import fetch_concurrent
from fcn.marketdata.correlation import Correlation
from fcn.marketdata.volsurface import VolSurface

FINNHUB_BASE = "https://finnhub.io/api/v1"
_SEED_PATH = Path(__file__).parent / "universe_seed.json"
_MAX_PLAUSIBLE_CAP = 10e12  # guard: a USD market cap above $10T is a currency error


class FinnhubUnavailable(RuntimeError):
    """Raised when Finnhub cannot be reached or is not entitled."""


def _finnhub_get(path: str, params: dict, token: str, timeout: float = 30.0):
    url = f"{FINNHUB_BASE}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-Finnhub-Token": token})  # token NOT in URL
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode())
    except Exception as exc:  # network / HTTP error
        raise FinnhubUnavailable(f"Finnhub request failed: {exc}") from exc


def _load_seed(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return []


@dataclass
class FinnhubProvider:
    """MarketDataProvider backed by Finnhub HTTP (or an injected getter for tests)."""

    api_key: str | None = None
    rate: float = 0.045
    funding: float | None = None
    div_yields: dict[str, float] = field(default_factory=dict)
    borrows: dict[str, float] = field(default_factory=dict)
    getter: Callable[[str, dict, str], object] | None = None
    seed_path: Path | None = None

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.environ.get("FINNHUB_API_KEY")
        self._get = self.getter or _finnhub_get
        self._spot_cache: dict[str, float] = {}
        self._seed_path = self.seed_path or _SEED_PATH

    def _call(self, path: str, params: dict):
        if not self.api_key:
            raise FinnhubUnavailable("no FINNHUB_API_KEY set (and no getter injected)")
        return self._get(path, params, self.api_key)

    # --- MarketDataProvider protocol ---
    def spot(self, ticker: str) -> float:
        if ticker in self._spot_cache:
            return self._spot_cache[ticker]
        data = self._call("quote", {"symbol": ticker})
        px = (data or {}).get("c") if isinstance(data, dict) else None
        if not px or float(px) <= 0:
            raise FinnhubUnavailable(f"Finnhub returned no quote for {ticker}")
        px = float(px)
        self._spot_cache[ticker] = px
        return px

    def div_yield(self, ticker: str) -> float:
        return self.div_yields.get(ticker, 0.0)

    def borrow(self, ticker: str) -> float:
        return self.borrows.get(ticker, 0.0)

    def vol_surface(self, ticker: str) -> VolSurface | None:
        return None  # Finnhub option IV is not used; Massive supplies vol surfaces

    def risk_free_rate(self) -> float:
        return self.rate

    def funding_rate(self) -> float:
        return self.rate if self.funding is None else self.funding

    def correlation(self, tickers: list[str]) -> Correlation:
        return Correlation.uniform(len(tickers), 0.0)  # no history endpoint on this plan

    # --- Finnhub specifics ---
    def market_cap(self, ticker: str) -> float | None:
        """Live market cap in USD from profile2, or ``None`` (empty/foreign-ADR/fund)."""
        data = self._call("stock/profile2", {"symbol": ticker})
        if not isinstance(data, dict):
            return None
        cap_m = data.get("marketCapitalization")  # millions, USD for US listings
        if not cap_m:
            return None
        cap = float(cap_m) * 1e6
        return cap if 0 < cap <= _MAX_PLAUSIBLE_CAP else None

    def screen_universe(
        self,
        min_market_cap: float = 2e10,
        include_stocks: bool = True,
        include_etf: bool = True,
        live_market_cap: bool = False,
        max_live: int = 80,
        max_workers: int = 12,
    ) -> list[dict]:
        """US large-cap stocks + ETFs (curated seed, Finnhub-sourced), market-cap filtered.

        Returns ``[{ticker,name,marketCap,sector,isEtf}]`` sorted by market cap desc.
        With ``live_market_cap`` the top ``max_live`` stocks have their cap refreshed
        live via Finnhub profile2 (per-name failures keep the seed value). The seed is
        bundled so this never network-fails by default — the Finder always has a universe.
        """
        rows = _load_seed(self._seed_path)
        if not rows:
            raise FinnhubUnavailable("universe seed missing; run scripts/build_universe.py")

        if live_market_cap:
            stocks = [r for r in rows if not r.get("isEtf")][:max_live]

            def refresh(r):
                cap = self.market_cap(r["ticker"])  # raises FinnhubUnavailable on transport error
                return (r["ticker"], cap) if cap else None

            # (item, result, error) per name; per-name failures just keep the seed cap.
            live = {res[0]: res[1] for _it, res, _err
                    in fetch_concurrent(stocks, refresh, max_workers=max_workers) if res}
            for r in rows:
                if r["ticker"] in live:
                    r["marketCap"] = live[r["ticker"]]

        out = []
        for r in rows:
            is_etf = bool(r.get("isEtf"))
            if is_etf and not include_etf:
                continue
            if not is_etf and not include_stocks:
                continue
            if float(r.get("marketCap", 0.0)) < min_market_cap:
                continue
            out.append({
                "ticker": r["ticker"],
                "name": r.get("name", r["ticker"]),
                "marketCap": float(r.get("marketCap", 0.0)),
                "sector": r.get("sector", "—"),
                "isEtf": is_etf,
            })
        out.sort(key=lambda d: d["marketCap"], reverse=True)
        return out
