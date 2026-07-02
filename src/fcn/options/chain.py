"""Option chains — live (Massive) or abstract fallback.

The chain is the bridge between the market and the strategy engine. A live chain
comes from Massive's ``/v3/snapshot/options/{ticker}`` and carries real strikes,
expiries, IVs, last/bid/ask, volume and open interest. An abstract chain is
synthesised from a :class:`fcn.marketdata.volsurface.VolSurface` at standard
strikes and tenors — used when the live feed is unavailable (offline, no key,
data plan gap). Every contract carries a ``source`` tag so the UI always discloses
which path it came from.

Selecting a contract for a strategy leg is by *nearest* (kind, strike, expiry) or
by *target delta* (the advisor's preferred idiom: "sell the 25Δ put").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

import numpy as np

from fcn.options.greeks import bs_greeks, implied_vol
from fcn.options.liquidity import contract_liquidity
from fcn.marketdata.volsurface import VolSurface

ContractSource = Literal["live", "abstract"]


@dataclass(frozen=True)
class OptionContract:
    """A single vanilla option contract."""

    ticker: str
    expiry: date
    strike: float
    kind: Literal["call", "put"]
    iv: float | None                 # live IV if present, else BS-implied from mid, else surface
    last: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    source: ContractSource = "abstract"

    @property
    def mid(self) -> float | None:
        if self.bid is not None and self.ask is not None and self.ask >= self.bid > 0:
            return 0.5 * (self.bid + self.ask)
        return self.last

    def years_to(self, asof: date) -> float:
        return max((self.expiry - asof).days, 1) / 365.0


@dataclass
class OptionChain:
    """All listed (or synthesised) contracts for one underlying."""

    ticker: str
    spot: float
    asof: date
    rate: float
    div_yield: float = 0.0
    borrow: float = 0.0
    contracts: list[OptionContract] = field(default_factory=list)

    def expiries(self) -> list[date]:
        return sorted({c.expiry for c in self.contracts})

    def strikes(self, kind: Literal["call", "put"] | None = None) -> list[float]:
        seen = {c.strike for c in self.contracts if kind is None or c.kind == kind}
        return sorted(seen)

    def select(
        self, *,
        kind: Literal["call", "put"] | None = None,
        strike: float | None = None,
        moneyness: float | None = None,
        expiry: date | None = None,
        tenor_days: int | None = None,
    ) -> OptionContract:
        """Pick the nearest contract by kind + strike + expiry.

        ``moneyness`` (=strike/spot) is an alternative to ``strike``. Raises
        :class:`ValueError` if the chain is empty or no kind match exists.
        """
        if not self.contracts:
            raise ValueError(f"empty chain for {self.ticker}")
        cands = self.contracts if kind is None else [c for c in self.contracts if c.kind == kind]
        if not cands:
            raise ValueError(f"no {kind} contracts for {self.ticker}")
        if moneyness is not None:
            strike = moneyness * self.spot
        target_strike = strike if strike is not None else self.spot
        if expiry is None and tenor_days is not None:
            expiry = self.asof + timedelta(days=tenor_days)
        target_expiry = expiry or self._median_expiry()

        def score(c: OptionContract) -> float:
            # Normalise so a 1% strike gap ~ 30 days expiry gap (rough; the chain
            # is rarely dense enough for this to over-discriminate).
            return abs(c.strike - target_strike) / max(target_strike, 1.0) + \
                0.5 * abs((c.expiry - target_expiry).days) / 365.0

        return min(cands, key=score)

    def select_by_delta(
        self, delta: float, *,
        kind: Literal["call", "put"] | None = None,
        tenor_days: int | None = None,
        expiry: date | None = None,
    ) -> OptionContract:
        """Pick the contract whose BS delta (under its IV) is closest to ``delta``.

        Idiom for advisor strategies: ``chain.select_by_delta(-0.25, kind='put')``
        for the short-put leg of a risk reversal.
        """
        if kind is None:
            kind = "call" if delta >= 0 else "put"
        if expiry is None and tenor_days is not None:
            expiry = self.asof + timedelta(days=tenor_days)
        target_expiry = expiry or self._median_expiry()
        cands = [c for c in self.contracts if c.kind == kind]
        if not cands:
            raise ValueError(f"no {kind} contracts for {self.ticker}")

        def score(c: OptionContract) -> tuple[float, float]:
            t = c.years_to(self.asof)
            sigma = c.iv or 0.30
            g = bs_greeks(self.spot, c.strike, t, sigma, self.rate,
                          self.div_yield, self.borrow, kind=kind)
            d = float(np.asarray(g.delta).item())
            # Primary: delta distance; secondary: expiry distance.
            return (abs(d - delta), abs((c.expiry - target_expiry).days))

        return min(cands, key=score)

    def _median_expiry(self) -> date:
        exps = self.expiries()
        return exps[len(exps) // 2] if exps else self.asof + timedelta(days=90)

    def contract_liquidity(self, contract):
        """Liquidity read for one contract (uses the chain's spot + asof)."""
        return contract_liquidity(contract, self.spot, contract.years_to(self.asof))

    def most_liquid_near(
        self, *, kind: Literal["call", "put"], strike: float, expiry: date,
        tol_pct: float = 0.05, min_gain: float = 8.0,
    ) -> OptionContract:
        """Most *liquid* contract within ``tol_pct`` of ``strike`` at ``expiry``.

        Used by liquidity-optimised valuation: instead of the strike nearest the
        target, prefer one that can actually be filled. To avoid silently
        substituting a materially different strike for a trivial gain, we only
        move off the nearest contract when an alternative beats its liquidity
        score by at least ``min_gain``, and ties break toward the *closer*
        strike. Falls back to the nearest contract if the band is empty.
        """
        target = self.select(kind=kind, strike=strike, expiry=expiry)
        target_score = self.contract_liquidity(target).score
        # Stay on the SAME expiry as the (nearest-match) target so optimisation
        # never silently changes a leg's tenor; only the strike may move.
        band = [
            c for c in self.contracts
            if c.kind == kind and c.expiry == target.expiry
            and abs(c.strike - target.strike) <= tol_pct * max(target.strike, 1.0)
        ]
        # Only substitute for a MEANINGFUL liquidity gain; among the contracts
        # that clear the gain threshold, take the one CLOSEST to the target
        # strike (don't drift to a far strike just because it scores highest).
        qualifying = [
            c for c in band
            if c.strike != target.strike
            and self.contract_liquidity(c).score >= target_score + min_gain
        ]
        if not qualifying:
            return target
        return min(qualifying, key=lambda c: (abs(c.strike - target.strike),
                                              -self.contract_liquidity(c).score))

    def liquidity_summary(self) -> dict:
        """Chain-level liquidity overview for the UI (real tradability of the name)."""
        if not self.contracts:
            return {"n": 0, "pct_tradable": None, "median_rel_spread": None,
                    "median_score": None, "total_open_interest": None,
                    "total_volume": None, "source": "modeled"}
        liqs = [self.contract_liquidity(c) for c in self.contracts]
        rels = sorted(l.rel_spread for l in liqs)
        scores = sorted(l.score for l in liqs)
        n = len(liqs)
        ois = [l.open_interest for l in liqs if l.open_interest is not None]
        vols = [l.volume for l in liqs if l.volume is not None]
        any_quoted = any(l.source == "quoted" for l in liqs)
        return {
            "n": n,
            "pct_tradable": round(100.0 * sum(1 for l in liqs if l.tradable) / n, 1),
            "median_rel_spread": round(rels[n // 2], 4),
            "median_score": round(scores[n // 2], 1),
            "total_open_interest": (sum(ois) if ois else None),
            "total_volume": (sum(vols) if vols else None),
            "source": "quoted" if any_quoted else "modeled",
        }

    # ---- builders ----------------------------------------------------------

    @classmethod
    def from_massive(
        cls, provider, ticker: str, *,
        rate: float | None = None, div_yield: float = 0.0, borrow: float = 0.0,
        max_maturity_years: float = 2.0, asof: date | None = None,
    ) -> "OptionChain":
        """Build a live chain from Massive's option-chain snapshot.

        ``provider`` is anything with a ``fetch_option_chain(...)`` method
        (i.e. :class:`fcn.marketdata.massive.MassiveProvider`); unit tests can
        inject a duck-typed fake.
        """
        from fcn.marketdata.massive import MassiveUnavailable

        spot = provider.spot(ticker)
        chain_asof = asof or getattr(provider, "_asof", date.today())
        rate = rate if rate is not None else provider.risk_free_rate()
        q = div_yield or provider.div_yield(ticker)
        b = borrow or provider.borrow(ticker)

        try:
            results = provider.fetch_option_chain(
                ticker, spot=spot,
                max_maturity_years=max_maturity_years,
                asof=chain_asof,
            )
        except (AttributeError, MassiveUnavailable) as exc:
            raise MassiveUnavailable(f"chain fetch failed for {ticker}: {exc}") from exc

        contracts: list[OptionContract] = []
        for r in results:
            det = r.get("details", {}) or {}
            exp = det.get("expiration_date")
            k = det.get("strike_price")
            ctype = det.get("contract_type")
            if not exp or not k or ctype not in ("call", "put"):
                continue
            try:
                exp_d = datetime.strptime(exp, "%Y-%m-%d").date()
            except ValueError:
                continue
            if exp_d <= chain_asof:
                continue
            iv = r.get("implied_volatility")
            iv = float(iv) if iv and 0.02 < iv <= 5.0 else None
            greeks_block = r.get("greeks") or {}
            last = r.get("last_quote") or {}
            bid = (last.get("bid") or greeks_block.get("bid"))
            ask = (last.get("ask") or greeks_block.get("ask"))
            contracts.append(OptionContract(
                ticker=ticker, expiry=exp_d, strike=float(k), kind=ctype, iv=iv,
                last=_safe_float(r.get("last_price")),
                bid=_safe_float(bid), ask=_safe_float(ask),
                volume=_safe_int(r.get("volume")),
                open_interest=_safe_int(r.get("open_interest")),
                source="live",
            ))

        # Backfill IVs missing from the snapshot by Newton on the mid, capped.
        # Rebuild by index (not .index()) to avoid value-equality ambiguity when
        # two byte-identical contracts exist.
        for i, c in enumerate(contracts):
            if c.iv is None and c.mid is not None:
                iv = implied_vol(c.mid, spot, c.strike, c.years_to(chain_asof),
                                 rate, q, b, kind=c.kind)
                contracts[i] = OptionContract(
                    ticker=c.ticker, expiry=c.expiry, strike=c.strike, kind=c.kind,
                    iv=iv, last=c.last, bid=c.bid, ask=c.ask,
                    volume=c.volume, open_interest=c.open_interest, source="live",
                )

        return cls(ticker=ticker, spot=spot, asof=chain_asof, rate=rate,
                   div_yield=q, borrow=b, contracts=contracts)

    @classmethod
    def abstract(
        cls, ticker: str, spot: float, surface: VolSurface, *,
        rate: float, div_yield: float = 0.0, borrow: float = 0.0,
        asof: date | None = None,
        tenors_days: tuple[int, ...] = (30, 60, 91, 182, 273, 365, 547, 730),
        strikes_pct: tuple[float, ...] = (0.75, 0.80, 0.85, 0.90, 0.93, 0.95, 0.97, 1.00,
                                          1.03, 1.05, 1.07, 1.10, 1.15, 1.20, 1.25),
    ) -> "OptionChain":
        """Synthesise a chain from a vol surface at standard tenors × strikes.

        No bid/ask/volume (the strategy engine prices off BS with the surface IV).
        """
        asof = asof or date.today()
        contracts: list[OptionContract] = []
        for d in tenors_days:
            expiry = asof + timedelta(days=d)
            t = max(d, 1) / 365.0
            for pct in strikes_pct:
                strike = round(spot * pct, 2)
                # The surface is parameterised in log-moneyness = ln(K/F), not ln(K/S).
                # For the abstract chain we ignore the forward drift difference
                # (small for short/mid tenors; the surface is a desk-input anyway).
                log_m = float(np.log(strike / spot))
                iv = float(surface.implied_vol(np.array([log_m]), t)[0])
                for kind in ("call", "put"):
                    g = bs_greeks(spot, strike, t, iv, rate, div_yield, borrow, kind=kind)
                    px = float(np.asarray(g.price).item())
                    contracts.append(OptionContract(
                        ticker=ticker, expiry=expiry, strike=strike, kind=kind,
                        iv=iv, last=px, bid=px, ask=px, volume=None, open_interest=None,
                        source="abstract",
                    ))
        return cls(ticker=ticker, spot=spot, asof=asof, rate=rate,
                   div_yield=div_yield, borrow=borrow, contracts=contracts)

    def summary(self) -> dict:
        """Compact stats for the UI header."""
        live = sum(1 for c in self.contracts if c.source == "live")
        n_exp = len(self.expiries())
        n_k = len(self.strikes())
        return {
            "ticker": self.ticker, "spot": round(self.spot, 4),
            "n_contracts": len(self.contracts), "n_expiries": n_exp, "n_strikes": n_k,
            "live_contracts": live,
            "source": "live" if live > 0 else "abstract",
            "asof": self.asof.isoformat(),
            "rate": self.rate, "div_yield": self.div_yield, "borrow": self.borrow,
        }

    def to_surface(self):
        """Build a :class:`GridVolSurface` from the chain's OTM contracts.

        This is the zero-network-cost path: the chain already carries every
        contract's IV, so we resample them onto a regular grid via
        :meth:`GridVolSurface.from_scatter`. Returns ``None`` if the chain has
        fewer than 6 usable OTM marks (too sparse to interpolate).
        """
        from fcn.marketdata.volsurface import GridVolSurface

        ts, xs, ivs = [], [], []
        for c in self.contracts:
            if c.iv is None or c.iv <= 0.02 or c.iv > 3.0:
                continue
            # OTM wing only (same convention as MassiveProvider.vol_surface):
            # puts below spot, calls above spot — most reliable IV marks.
            if (c.kind == "put" and c.strike > self.spot) or \
               (c.kind == "call" and c.strike < self.spot):
                continue
            t = c.years_to(self.asof)
            if t <= 0:
                continue
            ts.append(t)
            xs.append(float(np.log(c.strike / self.spot)))
            ivs.append(c.iv)
        if len(ts) < 6:
            return None
        return GridVolSurface.from_scatter(
            np.array(ts), np.array(xs), np.array(ivs),
        )


def _safe_float(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _safe_int(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None
