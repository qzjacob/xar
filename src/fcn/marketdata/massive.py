"""Live equity market data via Massive (massive.com) — Polygon-compatible.

This is the data source that finally makes equity FCN quoting *live*: Massive's
option-chain snapshot returns per-contract **implied volatility** (and Greeks), so
we can build a real per-name **skew surface** — the input that dominates the price
of a short worst-of down-and-in put (plan §2.3, §2.9). Spot comes from the chain's
underlying price; correlation from daily stock aggregates.

Endpoints (Bearer auth, host https://api.massive.com):
  GET /v3/snapshot/options/{ticker}   — option chain (implied_volatility, greeks, details, underlying_asset)
  GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}  — daily bars (for correlation)

The HTTP getter is injectable for offline tests. Dividends/borrow are not in the
options feed and remain user inputs; the risk-free rate is configurable.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np

from fcn.marketdata.correlation import Correlation
from fcn.marketdata.volsurface import GridVolSurface, VolSurface

MASSIVE_BASE = "https://api.massive.com"
_TARGET_TENORS_DAYS = (30, 60, 91, 182, 365, 547, 730)


class MassiveUnavailable(RuntimeError):
    """Raised when Massive cannot be reached or is not entitled."""


def _massive_get(path: str, params: dict, api_key: str, timeout: float = 30.0):
    url = f"{MASSIVE_BASE}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode())
    except Exception as exc:
        raise MassiveUnavailable(f"Massive request failed: {exc}") from exc


@dataclass
class MassiveProvider:
    api_key: str | None = None
    rate: float = 0.04
    funding: float | None = None
    div_yields: dict[str, float] = field(default_factory=dict)
    borrows: dict[str, float] = field(default_factory=dict)
    max_maturity_years: float = 2.0
    asof: date | None = None
    lookback_days: int = 252
    getter: Callable[[str, dict, str], object] | None = None

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.environ.get("MASSIVE_API_KEY")
        self._get = self.getter or _massive_get
        self._asof = self.asof or date.today()
        self._spot_cache: dict[str, float] = {}
        self._aggs_cache: dict[str, list] = {}   # daily aggregates shared by closes/monthly

    def _call(self, path: str, params: dict):
        if not self.api_key:
            raise MassiveUnavailable("no MASSIVE_API_KEY set (and no getter injected)")
        return self._get(path, params, self.api_key)

    def fetch_option_chain(self, ticker: str, *, spot: float | None = None,
                           max_maturity_years: float | None = None,
                           asof: date | None = None) -> list[dict]:
        """Public accessor: return raw option-chain result rows.

        Uses the same per-tenor approach as :meth:`vol_surface` — Massive rejects
        wide-window single calls (HTTP 400), so we iterate over standard tenors
        with a narrow ±7-10 day bracket and ``limit=250`` per call, accumulating
        all contracts. Both :meth:`vol_surface` and
        :meth:`fcn.options.chain.OptionChain.from_massive` consume this.
        """
        spot = spot or self.spot(ticker)
        base = asof or self._asof
        max_yrs = max_maturity_years or self.max_maturity_years
        max_days = int(max_yrs * 365)
        tenors = [d for d in _TARGET_TENORS_DAYS if d <= max_days + 30]
        all_rows: list[dict] = []
        for d in tenors:
            target = base + timedelta(days=d)
            params = {
                "expiration_date.gte": (target - timedelta(days=7)).isoformat(),
                "expiration_date.lte": (target + timedelta(days=10)).isoformat(),
                "strike_price.gte": round(0.5 * spot, 2),
                "strike_price.lte": round(1.6 * spot, 2),
                "limit": 250,
            }
            try:
                data = self._call(f"v3/snapshot/options/{ticker}", params)
            except MassiveUnavailable:
                continue
            rows = (data or {}).get("results", []) if isinstance(data, dict) else []
            all_rows.extend(rows)
        return all_rows

    # --- MarketDataProvider protocol ---
    def spot(self, ticker: str) -> float:
        if ticker in self._spot_cache:
            return self._spot_cache[ticker]
        data = self._call(f"v3/snapshot/options/{ticker}", {"limit": 1})
        results = (data or {}).get("results") if isinstance(data, dict) else None
        if not results:
            raise MassiveUnavailable(f"no chain/underlying for {ticker}")
        ua = results[0].get("underlying_asset") or {}
        px = ua.get("price")
        if px is None:
            raise MassiveUnavailable(f"Massive returned no underlying price for {ticker}")
        try:
            px = float(px)
        except (TypeError, ValueError) as exc:
            raise MassiveUnavailable(f"Massive underlying price not numeric for {ticker}") from exc
        self._spot_cache[ticker] = px
        return px

    def div_yield(self, ticker: str) -> float:
        return self.div_yields.get(ticker, 0.0)

    def borrow(self, ticker: str) -> float:
        return self.borrows.get(ticker, 0.0)

    def risk_free_rate(self) -> float:
        return self.rate

    def funding_rate(self) -> float:
        return self.rate if self.funding is None else self.funding

    def vol_surface(self, ticker: str) -> VolSurface | None:
        """Build a per-name skew surface from the live option chain (OTM IVs)."""
        spot = self.spot(ticker)
        max_days = int(self.max_maturity_years * 365)
        tenors = [d for d in _TARGET_TENORS_DAYS if d <= max_days + 30]
        ts, xs, ivs = [], [], []
        for d in tenors:
            target = self._asof + timedelta(days=d)
            params = {
                "expiration_date.gte": (target - timedelta(days=7)).isoformat(),
                "expiration_date.lte": (target + timedelta(days=10)).isoformat(),
                "strike_price.gte": round(0.5 * spot, 2),
                "strike_price.lte": round(1.6 * spot, 2),
                "limit": 250,
            }
            try:
                data = self._call(f"v3/snapshot/options/{ticker}", params)
            except MassiveUnavailable:
                continue
            for r in (data or {}).get("results", []):
                iv = r.get("implied_volatility")
                det = r.get("details", {})
                k = det.get("strike_price")
                exp = det.get("expiration_date")
                ctype = det.get("contract_type")
                if not iv or not k or not exp or iv <= 0.02 or iv > 3.0:
                    continue
                # OTM wing only: puts below spot, calls above spot (most reliable IVs).
                if (ctype == "put" and k > spot) or (ctype == "call" and k < spot):
                    continue
                t = (datetime.strptime(exp, "%Y-%m-%d").date() - self._asof).days / 365.0
                if t <= 0:
                    continue
                ts.append(t)
                xs.append(float(np.log(k / spot)))
                ivs.append(float(iv))
        if len(ts) < 6:
            return None  # not enough live data -> caller falls back to parametric
        return GridVolSurface.from_scatter(np.array(ts), np.array(xs), np.array(ivs))

    def point_vol(self, ticker: str, t: float, log_moneyness: float = 0.0,
                  *, spot: float | None = None) -> float | None:
        """Single-tenor implied vol near maturity ``t`` at ``log_moneyness`` (=ln(K/S)).

        Ranking needs only one expiry per name, so this fetches a single bracket
        around ``t`` (far fewer calls than the 7-tenor :meth:`vol_surface`) and
        returns the IV of the OTM contract closest to the requested strike/expiry.
        Callers that already hold a real spot pass it in (saves one snapshot call).
        Returns ``None`` when the chain lacks usable data (caller skips the name).
        """
        spot = spot or self.spot(ticker)
        target = self._asof + timedelta(days=max(1, int(round(t * 365))))
        strike = spot * float(np.exp(log_moneyness))
        params = {
            "expiration_date.gte": (target - timedelta(days=21)).isoformat(),
            "expiration_date.lte": (target + timedelta(days=21)).isoformat(),
            "strike_price.gte": round(0.5 * spot, 2),
            "strike_price.lte": round(1.6 * spot, 2),
            "limit": 250,
        }
        try:
            data = self._call(f"v3/snapshot/options/{ticker}", params)
        except MassiveUnavailable:
            return None
        best: tuple[float, float] | None = None  # (score, iv)
        for r in (data or {}).get("results", []):
            iv = r.get("implied_volatility")
            det = r.get("details", {})
            k = det.get("strike_price")
            exp = det.get("expiration_date")
            ctype = det.get("contract_type")
            if not iv or not k or not exp or iv <= 0.02 or iv > 3.0:
                continue
            # OTM wing only (most reliable IVs): puts below spot, calls above spot.
            if (ctype == "put" and k > spot) or (ctype == "call" and k < spot):
                continue
            tt = (datetime.strptime(exp, "%Y-%m-%d").date() - self._asof).days / 365.0
            if tt <= 0:
                continue
            score = abs(float(np.log(k / strike))) + abs(tt - t)  # nearest strike & expiry
            if best is None or score < best[0]:
                best = (score, float(iv))
        return None if best is None else best[1]

    def _daily_aggs(self, ticker: str) -> list:
        """Daily aggregate rows (cached per instance — closes/monthly share one fetch)."""
        if ticker in self._aggs_cache:
            return self._aggs_cache[ticker]
        frm = (self._asof - timedelta(days=int(self.lookback_days * 1.6))).isoformat()
        to = self._asof.isoformat()
        data = self._call(
            f"v2/aggs/ticker/{ticker}/range/1/day/{frm}/{to}", {"limit": 5000, "sort": "asc"}
        )
        rows = (data or {}).get("results", []) if isinstance(data, dict) else []
        self._aggs_cache[ticker] = rows
        return rows

    def _daily_closes(self, ticker: str) -> np.ndarray:
        return np.array([float(r["c"]) for r in self._daily_aggs(ticker)], dtype=float)

    def monthly_samples(self, ticker: str, months: int = 7) -> list[dict]:
        """Month-end samples for the market-read 择时 trend view: [{month, spot, rv21}].

        Same contract as ``FMPLiveProvider.monthly_samples`` — daily aggregates carry
        millisecond timestamps (``t``), so month-ends come straight from the bars.
        """
        try:
            rows = self._daily_aggs(ticker)   # cached — shares the closes fetch
        except MassiveUnavailable:
            return []
        closes = np.array([float(r["c"]) for r in rows], dtype=float)
        if len(closes) < 25:
            return []
        months_key = [
            datetime.utcfromtimestamp(float(r["t"]) / 1000.0).strftime("%Y-%m") for r in rows
        ]
        last_idx: dict[str, int] = {}
        for i, m in enumerate(months_key):
            last_idx[m] = i
        out: list[dict] = []
        for month in sorted(last_idx)[-months:]:
            i = last_idx[month]
            if i < 21:   # need a full 21-return trailing window
                continue
            window = np.diff(np.log(closes[i - 21 : i + 1]))
            rv = float(np.std(window, ddof=1) * np.sqrt(252))
            if not np.isfinite(rv):
                continue
            out.append({"month": month, "spot": round(float(closes[i]), 2), "rv21": round(rv, 4)})
        return out

    def correlation(self, tickers: list[str]) -> Correlation:
        if len(tickers) == 1:
            return Correlation.uniform(1, 0.0)
        series = []
        for t in tickers:
            closes = self._daily_closes(t)
            series.append(np.diff(np.log(closes)) if closes.size > 2 else np.array([]))
        n = min((s.size for s in series), default=0)
        if n < 20:
            warnings.warn(
                f"Massive history insufficient ({n} overlapping returns) to estimate "
                f"correlation for {tickers}; falling back to ρ=0.5. Supply a correlation "
                "override rather than trusting this default.",
                stacklevel=2,
            )
            return Correlation.uniform(len(tickers), 0.5)
        mat = np.column_stack([s[-n:] for s in series])
        return Correlation.from_returns(mat)
