"""finder_mc — the Finder's mini-MC autocallable FCN screen (offline, deterministic seed).

Locks the economic invariants the screen must honour, plus the ranking dispatch
(desk params → MC; plain coupon screen stays closed-form) and the strike sort order.
"""

from __future__ import annotations

import pytest

from fcn.marketdata.provider import ManualProvider
from fcn.marketdata.volsurface import ParametricSkewSurface
from fcn.service.finder_mc import screen_price
from fcn.service.ranking import RankStructure, rank_underlyings

_BASE = dict(spot=100.0, rate=0.04, tenor_years=0.5, frequency="monthly",
             ki=0.65, strike=0.80, reoffer=0.99)


def test_coupon_rises_with_vol():
    cs = [screen_price(sigma=s, ko=1.0, ki_style="european", **_BASE)["coupon"]
          for s in (0.20, 0.35, 0.50)]
    assert cs[0] < cs[1] < cs[2]


def test_higher_ko_barrier_lengthens_life_and_cuts_autocall():
    a = screen_price(sigma=0.4, ko=1.00, ki_style="european", **_BASE)
    b = screen_price(sigma=0.4, ko=1.10, ki_style="european", **_BASE)
    assert b["prob_autocall"] < a["prob_autocall"]
    assert b["expected_life"] > a["expected_life"]


def test_american_ki_riskier_than_european():
    e = screen_price(sigma=0.4, ko=None, ki_style="european", **_BASE)
    a = screen_price(sigma=0.4, ko=None, ki_style="american", **_BASE)
    assert a["coupon"] >= e["coupon"]
    assert a["prob_capital_at_risk"] >= e["prob_capital_at_risk"]


def test_barrier_none_unprotected_pays_more_than_ki65():
    prot = screen_price(sigma=0.4, ko=1.0, ki_style="european", **_BASE)
    nake = screen_price(sigma=0.4, ko=1.0, ki_style="none", **_BASE)
    assert nake["coupon"] > prot["coupon"]           # no protection buffer → richer coupon
    assert nake["buffer_pct"] == pytest.approx(0.20)  # buffer measured to the strike


def test_strike_coupon_round_trip():
    cp = screen_price(sigma=0.4, ko=1.0, ki_style="european", **_BASE)["coupon"]
    args = {k: v for k, v in _BASE.items() if k != "strike"}
    ks = screen_price(sigma=0.4, ko=1.0, ki_style="european", coupon_pa=cp, **args)
    assert ks["bracketed"] is True
    assert ks["strike"] == pytest.approx(0.80, abs=0.02)


def test_same_coupon_higher_vol_affords_lower_strike():
    args = {k: v for k, v in _BASE.items() if k != "strike"}
    k_lo = screen_price(sigma=0.55, ko=1.0, ki_style="european", coupon_pa=0.12, **args)
    k_hi = screen_price(sigma=0.30, ko=1.0, ki_style="european", coupon_pa=0.12, **args)
    assert k_lo["strike"] < k_hi["strike"]


def _prov():
    return ManualProvider(
        spots={"LOWV": 100.0, "HIGHV": 100.0},
        surfaces={"LOWV": ParametricSkewSurface(atm=0.25, slope=-0.4, curv=0.3),
                  "HIGHV": ParametricSkewSurface(atm=0.55, slope=-0.4, curv=0.3)},
        rate=0.04,
    )


def test_rank_by_strike_lowest_first():
    st = RankStructure(tenor_months=6, frequency="monthly", protection_pct=0.65,
                       ko_pct=1.0, ki_style="european", coupon_pa=0.12)
    r = rank_underlyings(_prov(), st, universe=["LOWV", "HIGHV"], rank_by="strike", use_cache=False)
    assert [x["ticker"] for x in r["ranked"]] == ["HIGHV", "LOWV"]  # lowest strike (best buffer) first
    assert all("strike" in x for x in r["ranked"])
    assert r["rank_by"] == "strike"


def test_rank_with_desk_params_uses_mc_and_coupon_order_holds():
    st = RankStructure(tenor_months=6, frequency="monthly", protection_pct=0.65,
                       ko_pct=1.0, ki_style="american")
    r = rank_underlyings(_prov(), st, universe=["LOWV", "HIGHV"], rank_by="coupon", use_cache=False)
    assert [x["ticker"] for x in r["ranked"]] == ["HIGHV", "LOWV"]
    assert all("prob_autocall" in x for x in r["ranked"])  # MC rows carry autocall stats


def test_plain_structure_still_closed_form():
    st = RankStructure(tenor_months=6, frequency="quarterly", protection_pct=0.70)
    r = rank_underlyings(_prov(), st, universe=["LOWV", "HIGHV"], rank_by="coupon", use_cache=False)
    assert [x["ticker"] for x in r["ranked"]] == ["HIGHV", "LOWV"]
    assert all("prob_autocall" not in x for x in r["ranked"])  # closed-form rows have no MC fields
