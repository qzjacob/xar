"""Finnhub adapter + universe seed, exercised offline via an injected getter / fixture."""

from __future__ import annotations

import json

import pytest

from fcn.marketdata.finnhub import FinnhubProvider, FinnhubUnavailable

SEED = [
    {"ticker": "NVDA", "name": "NVIDIA", "sector": "Semiconductors", "marketCap": 4.8e12, "isEtf": False},
    {"ticker": "AAPL", "name": "Apple", "sector": "Technology", "marketCap": 4.3e12, "isEtf": False},
    {"ticker": "SMALL", "name": "Small Co", "sector": "X", "marketCap": 5e9, "isEtf": False},  # < $20B
    {"ticker": "SPY", "name": "SPDR S&P 500", "sector": "ETF", "marketCap": 6.2e11, "isEtf": True},
    {"ticker": "ARKK", "name": "ARK Innovation", "sector": "ETF", "marketCap": 8e9, "isEtf": True},  # < $20B
]


@pytest.fixture
def seed_file(tmp_path):
    p = tmp_path / "universe_seed.json"
    p.write_text(json.dumps(SEED))
    return p


def test_screen_universe_filters_and_sorts(seed_file):
    p = FinnhubProvider(api_key="x", seed_path=seed_file)
    uni = p.screen_universe(min_market_cap=2e10)
    assert [u["ticker"] for u in uni] == ["NVDA", "AAPL", "SPY"]  # cap-desc; sub-$20B dropped
    assert any(u["ticker"] == "SPY" and u["isEtf"] for u in uni)


def test_include_flags(seed_file):
    p = FinnhubProvider(api_key="x", seed_path=seed_file)
    assert "SPY" not in [u["ticker"] for u in p.screen_universe(2e10, include_etf=False)]
    assert [u["ticker"] for u in p.screen_universe(2e10, include_stocks=False)] == ["SPY"]


def test_live_market_cap_refresh(seed_file):
    # profile2 reports cap in millions USD; live refresh should override the seed.
    def getter(path, params, token):
        assert path == "stock/profile2"
        return {"NVDA": {"marketCapitalization": 5_000_000}, "AAPL": {"marketCapitalization": 4_000_000}}\
            .get(params["symbol"], {})
    p = FinnhubProvider(api_key="x", seed_path=seed_file, getter=getter)
    uni = p.screen_universe(min_market_cap=2e10, live_market_cap=True)
    nvda = next(u for u in uni if u["ticker"] == "NVDA")
    assert nvda["marketCap"] == pytest.approx(5e12)  # 5,000,000 mn -> $5T


def test_market_cap_currency_guard():
    # Foreign-ADR caps come back in local currency -> implausibly large -> None.
    def getter(path, params, token):
        return {"marketCapitalization": 65_000_000}  # 65,000,000 mn = $65T -> guarded
    p = FinnhubProvider(api_key="x", getter=getter)
    assert p.market_cap("TSM") is None


def test_spot_from_quote():
    p = FinnhubProvider(api_key="x", getter=lambda path, params, token: {"c": 294.3, "pc": 297.0})
    assert p.spot("AAPL") == pytest.approx(294.3)


def test_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)  # may be exported in the shell
    p = FinnhubProvider(api_key=None)
    with pytest.raises(FinnhubUnavailable):
        p.spot("AAPL")


def test_missing_seed_raises(tmp_path):
    p = FinnhubProvider(api_key="x", seed_path=tmp_path / "nope.json")
    with pytest.raises(FinnhubUnavailable):
        p.screen_universe()


def test_bundled_seed_is_valid_and_large_cap():
    # The shipped seed must exist, be non-trivial, and all entries >$20B-ish.
    p = FinnhubProvider(api_key="x")  # default bundled seed
    uni = p.screen_universe(min_market_cap=2e10)
    assert len(uni) > 100
    assert all(u["marketCap"] >= 2e10 for u in uni)
    assert any(u["isEtf"] for u in uni) and any(not u["isEtf"] for u in uni)
