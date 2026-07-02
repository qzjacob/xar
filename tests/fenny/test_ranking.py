"""Underlying Finder ranking — closed-form FCN screen, exercised offline."""

from __future__ import annotations

import pytest

from fcn.analytics.closed_form import single_name_european_note
from fcn.marketdata.provider import ManualProvider
from fcn.marketdata.volsurface import FlatVolSurface, ParametricSkewSurface
from fcn.service.ranking import RankStructure, _coupon_schedule, rank_underlyings

STRUCT = RankStructure(tenor_months=6, frequency="quarterly", protection_pct=0.70, reoffer_pct=0.977)


def _prov(vols, rate=0.045, funding=0.05):
    return ManualProvider(
        spots={t: 100.0 for t in vols},
        surfaces={t: ParametricSkewSurface(atm=v, slope=-0.5, curv=0.5) for t, v in vols.items()},
        rate=rate, funding=funding,
    )


def test_ranks_descending_by_coupon_and_higher_vol_pays_more():
    prov = _prov({"LO": 0.18, "MID": 0.32, "HI": 0.55})
    res = rank_underlyings(prov, STRUCT, universe=["LO", "MID", "HI"], use_cache=False)
    assert res["ranked_count"] == 3
    coupons = [r["coupon"] for r in res["ranked"]]
    assert coupons == sorted(coupons, reverse=True)
    assert [r["ticker"] for r in res["ranked"]] == ["HI", "MID", "LO"]  # higher vol -> richer coupon
    for r in res["ranked"]:
        assert 0.0 < r["prob_capital_at_risk"] < 1.0
        assert r["rank"] >= 1


def test_barrier_monotonic_with_flat_vol():
    # Isolate the closed-form (flat vol, no skew sampling): higher barrier -> richer coupon.
    prov = ManualProvider(spots={"X": 100.0}, surfaces={"X": FlatVolSurface(0.35)}, rate=0.045, funding=0.05)
    cps = []
    for b in (0.60, 0.75, 0.90):
        r = rank_underlyings(
            prov, RankStructure(tenor_months=6, protection_pct=b, reoffer_pct=0.977),
            universe=["X"], use_cache=False,
        )
        cps.append(r["ranked"][0]["coupon"])
    assert cps[0] < cps[1] < cps[2]


def test_top_n_and_min_coupon_filter():
    prov = _prov({"A": 0.5, "B": 0.45, "C": 0.4, "D": 0.35, "E": 0.3})
    res = rank_underlyings(prov, STRUCT, universe=list("ABCDE"), top_n=3, use_cache=False)
    assert len(res["ranked"]) == 3
    empty = rank_underlyings(prov, STRUCT, universe=list("ABCDE"), filters={"min_coupon": 9.0}, use_cache=False)
    assert empty["ranked"] == []


def test_kind_filter_stock_vs_etf():
    prov = _prov({"STK": 0.4, "FUND": 0.4})
    uni = [
        {"ticker": "STK", "name": "Stock", "marketCap": 2, "sector": "Tech", "isEtf": False},
        {"ticker": "FUND", "name": "Fund", "marketCap": 1, "sector": "ETF", "isEtf": True},
    ]
    only_etf = rank_underlyings(prov, STRUCT, universe=uni, filters={"kind": "etf"}, use_cache=False)
    assert [r["ticker"] for r in only_etf["ranked"]] == ["FUND"]
    only_stock = rank_underlyings(prov, STRUCT, universe=uni, filters={"kind": "stock"}, use_cache=False)
    assert [r["ticker"] for r in only_stock["ranked"]] == ["STK"]


def test_names_without_surface_are_skipped_and_counted():
    prov = ManualProvider(
        spots={"OK": 100.0}, surfaces={"OK": ParametricSkewSurface(atm=0.4)}, rate=0.045, funding=0.05
    )
    res = rank_underlyings(prov, STRUCT, universe=["OK", "MISSING"], use_cache=False)
    assert res["ranked_count"] == 1
    assert any(s["ticker"] == "MISSING" for s in res["skipped"])
    assert res["universe_size"] == 2


def test_solved_coupon_reprices_to_issue_price():
    # The ranked coupon must re-price the note back to reoffer * notional (flat vol -> exact).
    prov = ManualProvider(spots={"X": 100.0}, surfaces={"X": FlatVolSurface(0.35)}, rate=0.045, funding=0.05)
    row = rank_underlyings(prov, STRUCT, universe=["X"], use_cache=False)["ranked"][0]
    times, taus = _coupon_schedule(0.5, "quarterly")
    v = single_name_european_note(
        spot=100, initial_fixing=100, ki_fraction=0.70, strike_fraction=1.0,
        sigma=0.35, r=0.045, q=0, borrow=0, funding=0.05, coupon_rate=row["coupon"],
        coupon_times=times, coupon_taus=taus, maturity=0.5, notional=100.0,
    )
    assert v.pv == pytest.approx(0.977 * 100.0, rel=1e-6)
