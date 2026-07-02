"""Option chain: abstract construction + Massive live-path with injected getter."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from fcn.marketdata.volsurface import FlatVolSurface, ParametricSkewSurface
from fcn.options.chain import OptionChain
from fcn.options.greeks import bs_greeks


# --- abstract chain --------------------------------------------------------

def test_abstract_chain_construction():
    surf = ParametricSkewSurface(atm=0.30, slope=-0.5, curv=0.5)
    asof = date(2026, 6, 18)
    chain = OptionChain.abstract("AAPL", 180.0, surf, rate=0.045, asof=asof)
    s = chain.summary()
    assert s["ticker"] == "AAPL" and s["source"] == "abstract"
    assert s["n_contracts"] > 0 and s["n_expiries"] >= 5
    assert all(c.source == "abstract" for c in chain.contracts)


def test_abstract_chain_put_call_parity_at_same_strike():
    """At a given K and T, abstract call + put satisfy BS parity."""
    surf = FlatVolSurface(0.30)
    chain = OptionChain.abstract("X", 100.0, surf, rate=0.04, div_yield=0.01)
    c = chain.select(kind="call", strike=100.0, tenor_days=365)
    p = chain.select(kind="put", strike=100.0, tenor_days=365)
    T = c.years_to(chain.asof)
    expected_diff = 100.0 * np.exp(-0.01 * T) - 100.0 * np.exp(-0.04 * T)
    assert c.last is not None and p.last is not None
    assert abs((c.last - p.last) - expected_diff) < 1e-6


def test_abstract_chain_atm_vol_matches_surface():
    surf = ParametricSkewSurface(atm=0.25, slope=-0.4)
    chain = OptionChain.abstract("X", 100.0, surf, rate=0.03)
    c = chain.select(kind="call", strike=100.0, tenor_days=182)
    assert c.iv is not None
    assert abs(c.iv - surf.atm_vol(c.years_to(chain.asof))) < 1e-9


# --- selection -------------------------------------------------------------

def test_select_nearest_strike():
    chain = OptionChain.abstract("X", 100.0, FlatVolSurface(0.25), rate=0.03,
                                 strikes_pct=(0.9, 1.0, 1.1))
    c = chain.select(kind="call", strike=98.0, tenor_days=60)
    assert c.strike == 100.0
    c2 = chain.select(kind="call", moneyness=1.10, tenor_days=60)
    assert c2.strike == 110.0


def test_select_by_delta_25d_put():
    """The 25Δ put pick lands within ~5 delta-pts of the target on a dense chain."""
    surf = ParametricSkewSurface(atm=0.30, slope=-0.5, curv=0.3)
    chain = OptionChain.abstract("X", 100.0, surf, rate=0.04,
                                 tenors_days=(365, 730))  # long-dated for deep OTM
    c = chain.select_by_delta(-0.25, kind="put")
    assert c.iv is not None
    T = c.years_to(chain.asof)
    g = bs_greeks(chain.spot, c.strike, T, c.iv, chain.rate, kind="put")
    assert abs(float(g.delta) - (-0.25)) < 0.05


def test_select_raises_on_empty():
    chain = OptionChain(ticker="X", spot=100.0, asof=date(2026, 1, 1), rate=0.04)
    with pytest.raises(ValueError):
        chain.select(kind="call")


# --- Massive live-path with injected fake provider -------------------------

def _fake_massive_provider(spot=180.0):
    """Minimal duck-typed provider matching what from_massive() needs."""
    today = date(2026, 6, 18)

    def fetch_option_chain(ticker, *, spot=None, max_maturity_years=None, asof=None):
        results = []
        for d_offset in (30, 90, 180, 365):
            exp = (today + timedelta(days=d_offset)).isoformat()
            for mult, kind in [(0.9, "put"), (1.0, "call"), (1.0, "put"), (1.1, "call")]:
                results.append({
                    "details": {
                        "expiration_date": exp,
                        "strike_price": round(spot_val * mult, 2),
                        "contract_type": kind,
                    },
                    "implied_volatility": 0.30 + 0.0001 * d_offset,
                    "last_quote": {"bid": 5.0, "ask": 5.5},
                    "volume": 100,
                    "open_interest": 1000,
                })
        return results

    spot_val = spot
    return SimpleNamespace(
        fetch_option_chain=fetch_option_chain,
        spot=lambda ticker: spot_val,
        risk_free_rate=lambda: 0.045,
        div_yield=lambda t: 0.005,
        borrow=lambda t: 0.0,
        _asof=today,
    )


def test_from_massive_builds_live_chain():
    prov = _fake_massive_provider(spot=180.0)
    chain = OptionChain.from_massive(prov, "AAPL", max_maturity_years=1.0)
    s = chain.summary()
    assert s["source"] == "live" and s["live_contracts"] > 0
    assert s["n_expiries"] == 4
    assert all(c.source == "live" for c in chain.contracts)
    assert all(c.iv is not None and 0.20 < c.iv < 0.40 for c in chain.contracts)


def test_from_massive_iv_backfill_when_missing():
    """If Massive omits IV but gives bid/ask, we back-fill via Newton."""
    today = date(2026, 6, 18)

    def fetch_option_chain(ticker, *, spot=None, max_maturity_years=None, asof=None):
        exp = (today + timedelta(days=90)).isoformat()
        return [
            {"details": {"expiration_date": exp, "strike_price": 100.0, "contract_type": "call"},
             "implied_volatility": None,
             "last_quote": {"bid": 9.0, "ask": 10.0},
             "volume": 0, "open_interest": 0},
        ]

    prov = SimpleNamespace(
        fetch_option_chain=fetch_option_chain,
        spot=lambda t: 100.0, risk_free_rate=lambda: 0.04,
        div_yield=lambda t: 0.0, borrow=lambda t: 0.0, _asof=today,
    )
    chain = OptionChain.from_massive(prov, "X", max_maturity_years=0.5)
    c = chain.contracts[0]
    assert c.iv is not None and 0.15 < c.iv < 0.50
