"""market_resolver: real-data resolution with dual-class aliasing + per-name graceful fallback.

Regression for the worst-of coupon reversal: FMP gates GOOG (HTTP 402) while GOOGL is free, so a
requested GOOG used to drop out of the basket entirely. The desk frontend then discarded ALL real
data and priced the whole basket at a flat assumption — a lower flat vol read as a LOWER coupon for
MORE names (the exact reversal the desk saw). Aliasing GOOG→GOOGL keeps the real vol on every name.
"""

from __future__ import annotations

import numpy as np
import pytest

from fcn.marketdata.correlation import Correlation
from fcn.marketdata.fmp import FMPUnavailable


class _FakeProv:
    """Mimics FMPProvider but gates a set of symbols (like FMP's 402 on GOOG/BRK.B)."""

    def __init__(self, gated: set[str], spots: dict[str, float]):
        self.gated = gated
        self.spots = spots

    def spot(self, sym: str) -> float:
        if sym in self.gated:
            raise FMPUnavailable(f"HTTP 402 for {sym}")
        if sym not in self.spots:
            raise FMPUnavailable(f"no quote for {sym}")
        return self.spots[sym]

    def history_returns(self, sym: str) -> np.ndarray:
        rng = np.random.default_rng(abs(hash(sym)) % (2**32))
        return rng.normal(0, 0.02, 120)

    def div_yield(self, sym: str) -> float:
        return 0.0

    def borrow(self, sym: str) -> float:
        return 0.0

    def correlation(self, syms: list[str]) -> Correlation:
        return Correlation.uniform(len(syms), 0.3)

    def risk_free_rate(self) -> float:
        return 0.04


def _patch(monkeypatch, prov):
    monkeypatch.setattr("fcn.marketdata.fmp.FMPProvider", lambda *a, **k: prov)


def test_goog_aliases_to_googl_and_keeps_real_vol(monkeypatch):
    from fcn.service import market_resolver as MR

    _patch(monkeypatch, _FakeProv(gated={"GOOG"}, spots={"TSLA": 400.0, "GOOGL": 350.0}))
    rm = MR.resolve_market(["TSLA", "GOOG"])

    # both names present (no silent drop) — the term sheet keeps the requested "GOOG" ticker
    assert [a["ticker"] for a in rm["assets"]] == ["TSLA", "GOOG"]
    goog = next(a for a in rm["assets"] if a["ticker"] == "GOOG")
    assert goog["spot"] == pytest.approx(350.0)               # resolved via GOOGL's price
    assert goog["atm_vol"] > 0.08                              # a real realized vol, not a drop
    meta = {m["ticker"]: m for m in rm["resolved"]}
    assert meta["GOOG"]["resolved"] is True
    assert meta["GOOG"]["resolved_as"] == "GOOGL"             # transparency: alias recorded
    assert rm["correlation"] is not None and len(rm["correlation"]) == 2


def test_dotted_ticker_falls_back_to_dash(monkeypatch):
    from fcn.service import market_resolver as MR

    # BRK.B gated but the dash form resolves (FMP's actual symbol)
    _patch(monkeypatch, _FakeProv(gated={"BRK.B"}, spots={"BRK-B": 450.0}))
    rm = MR.resolve_market(["BRK.B"])
    assert rm["assets"][0]["ticker"] == "BRK.B"
    assert rm["assets"][0]["spot"] == pytest.approx(450.0)
    assert rm["resolved"][0]["resolved_as"] == "BRK-B"


def test_truly_unresolvable_name_is_dropped_not_faked(monkeypatch):
    from fcn.service import market_resolver as MR

    # a bogus ticker with no alias/dash variant: dropped from assets, flagged resolved=False
    _patch(monkeypatch, _FakeProv(gated=set(), spots={"TSLA": 400.0}))
    rm = MR.resolve_market(["TSLA", "ZZZZ"])
    assert [a["ticker"] for a in rm["assets"]] == ["TSLA"]
    meta = {m["ticker"]: m for m in rm["resolved"]}
    assert meta["ZZZZ"]["resolved"] is False
    # single resolved name -> no correlation matrix
    assert rm["correlation"] is None
