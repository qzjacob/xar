"""Massive live-data adapter, exercised offline via an injected HTTP getter."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from fcn.marketdata.massive import MassiveProvider, MassiveUnavailable
from fcn.marketdata.volsurface import GridVolSurface

ASOF = date(2026, 6, 20)
SPOT = 100.0


def _chain_for_expiry(exp: str, term_bump: float):
    # OTM puts below spot (higher IV = skew), OTM calls above spot.
    rows = []
    for k, iv in [(80, 0.40), (90, 0.34), (100, 0.30)]:
        rows.append({"implied_volatility": iv + term_bump,
                     "details": {"strike_price": k, "expiration_date": exp, "contract_type": "put"},
                     "underlying_asset": {"price": SPOT}})
    for k, iv in [(110, 0.275), (120, 0.265)]:
        rows.append({"implied_volatility": iv + term_bump,
                     "details": {"strike_price": k, "expiration_date": exp, "contract_type": "call"},
                     "underlying_asset": {"price": SPOT}})
    return rows


def _getter(prices: dict[str, list[float]]):
    exp1 = (ASOF + timedelta(days=30)).isoformat()
    exp2 = (ASOF + timedelta(days=180)).isoformat()

    def getter(path: str, params: dict, api_key: str):
        if path.startswith("v3/snapshot/options/"):
            if params.get("limit") == 1:
                return {"results": [{"underlying_asset": {"price": SPOT},
                                     "details": {"strike_price": 100, "expiration_date": exp1,
                                                 "contract_type": "call"},
                                     "implied_volatility": 0.30}]}
            return {"results": _chain_for_expiry(exp1, 0.0) + _chain_for_expiry(exp2, 0.02)}
        if path.startswith("v2/aggs/ticker/"):
            sym = path.split("/")[3]
            return {"results": [{"c": c} for c in prices[sym]]}
        raise AssertionError(path)
    return getter


def test_live_surface_has_put_skew():
    p = MassiveProvider(api_key="x", asof=ASOF, getter=_getter({}))
    surf = p.vol_surface("AAPL")
    assert isinstance(surf, GridVolSurface)
    put = surf.implied_vol(np.array([-0.2]), 0.5)[0]
    call = surf.implied_vol(np.array([0.1]), 0.5)[0]
    assert put > call  # real equity put skew recovered from the chain
    assert 0.25 < surf.atm_vol(0.5) < 0.45


def test_spot_from_chain():
    p = MassiveProvider(api_key="x", asof=ASOF, getter=_getter({}))
    assert p.spot("AAPL") == pytest.approx(SPOT)


def test_correlation_from_aggs():
    rng = np.random.default_rng(1)
    base = np.cumprod(1 + rng.normal(0, 0.01, 200))
    prices = {"AAA": (100 * base).tolist(),
              "BBB": (50 * base * (1 + rng.normal(0, 0.002, 200))).tolist()}
    p = MassiveProvider(api_key="x", asof=ASOF, getter=_getter(prices))
    corr = p.correlation(["AAA", "BBB"])
    assert corr.matrix[0, 1] > 0.5


def test_unavailable_without_key():
    p = MassiveProvider(api_key=None)
    with pytest.raises(MassiveUnavailable):
        p.spot("AAA")
