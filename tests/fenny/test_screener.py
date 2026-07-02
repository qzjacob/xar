"""FMP company-screener universe, exercised offline via an injected getter."""

from __future__ import annotations

import pytest

from fcn.marketdata.fmp import FMPProvider, FMPUnavailable

_ROWS = [
    {"symbol": "AAA", "companyName": "Alpha", "marketCap": 9e11, "sector": "Tech",
     "exchangeShortName": "NASDAQ", "isEtf": False, "isFund": False},
    {"symbol": "SPY", "companyName": "S&P 500 ETF", "marketCap": 5e11, "sector": None,
     "exchangeShortName": "AMEX", "isEtf": True, "isFund": False},
    {"symbol": "BBB", "companyName": "Beta", "marketCap": 3e11, "sector": "Health",
     "exchangeShortName": "NYSE", "isEtf": False},
    {"symbol": "FGN", "companyName": "Foreign", "marketCap": 4e11, "sector": "X",
     "exchangeShortName": "LSE", "isEtf": False},  # excluded by exchange filter
]


def _getter(path, params, api_key):
    assert path == "company-screener"
    assert int(params["marketCapMoreThan"]) == 20_000_000_000
    return _ROWS


def test_screen_universe_filters_and_sorts():
    p = FMPProvider(api_key="x", getter=_getter)
    uni = p.screen_universe(min_market_cap=2e10)
    assert [u["ticker"] for u in uni] == ["AAA", "SPY", "BBB"]  # cap-desc; LSE excluded
    spy = next(u for u in uni if u["ticker"] == "SPY")
    assert spy["isEtf"] is True and spy["sector"] == "ETF"  # None sector -> "ETF" label


def test_include_flags_split_stocks_and_etfs():
    p = FMPProvider(api_key="x", getter=_getter)
    stocks = p.screen_universe(min_market_cap=2e10, include_etf=False)
    assert "SPY" not in [u["ticker"] for u in stocks]
    etfs = p.screen_universe(min_market_cap=2e10, include_stocks=False)
    assert [u["ticker"] for u in etfs] == ["SPY"]


def test_unavailable_without_key():
    with pytest.raises(FMPUnavailable):
        FMPProvider(api_key=None).screen_universe()
