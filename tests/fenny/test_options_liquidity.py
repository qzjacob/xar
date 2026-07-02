"""Liquidity / execution-cost dimension across the Options Desk chain.

Pins the new dimension so a thinly-traded structure can never again look good
on mid: per-contract liquidity, slippage/execution net debit, the
liquidity-optimised strike snap, and the advisor's liquidity-adjusted ranking.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from fcn.api.main import app
from fcn.marketdata.volsurface import ParametricSkewSurface
from fcn.options.advisor import advise
from fcn.options.analytics import analyze_surface
from fcn.options.chain import OptionChain, OptionContract
from fcn.options.liquidity import (
    aggregate_liquidity,
    contract_liquidity,
    liquidity_multiplier,
    liquidity_score,
)
from fcn.options.strategies import long_call, short_strangle
from fcn.options.strategy_engine import value_strategy
from fcn.options.view import FundamentalView

ASOF = date(2026, 6, 30)
EXP = ASOF + timedelta(days=45)


def _abstract():
    surf = ParametricSkewSurface(atm=0.45, slope=-0.5, curv=0.3)
    return OptionChain.abstract("X", 100.0, surf, rate=0.02, asof=ASOF)


# --- per-contract liquidity ----------------------------------------------

def test_quoted_tight_is_tradable_thin_is_not():
    tight = OptionContract("X", EXP, 100.0, "call", iv=0.4, bid=4.0, ask=4.1,
                           volume=500, open_interest=3000, source="live")
    blown = OptionContract("X", EXP, 140.0, "call", iv=0.6, bid=0.05, ask=0.60,
                           volume=0, open_interest=0, source="live")
    lt = contract_liquidity(tight, 100.0, 45 / 365)
    lb = contract_liquidity(blown, 100.0, 45 / 365)
    assert lt.source == "quoted" and lt.tradable and lt.score > 70
    assert lb.source == "quoted" and not lb.tradable and lb.rel_spread > 0.25


def test_abstract_liquidity_is_modeled():
    c = _abstract().select(kind="call", strike=105.0, expiry=EXP)
    liq = contract_liquidity(c, 100.0, 45 / 365)
    assert liq.source == "modeled"
    assert 0.0 <= liq.score <= 100.0
    assert liq.rel_spread > 0


def test_liquidity_multiplier_bounds():
    assert liquidity_multiplier(100.0, True) == pytest.approx(1.0)
    assert liquidity_multiplier(0.0, True) == pytest.approx(0.25)
    assert liquidity_multiplier(100.0, False) <= 0.30   # untradable is capped


def test_aggregate_worst_leg_dominates_and_pure_stock_liquid():
    from fcn.options.liquidity import Liquidity
    good = Liquidity(0.02, 3000, 500, 90.0, True, "quoted")
    bad = Liquidity(0.40, 0, 0, 5.0, False, "quoted")
    agg = aggregate_liquidity([good, bad], slippage=50.0, mid_net_debit=500.0,
                              spot=100.0)
    assert not agg.tradable                # one untradable leg ⇒ untradable
    assert agg.score < (90 + 5) / 2 + 1    # worst-leg-weighted, below the mean
    empty = aggregate_liquidity([], 0.0, 0.0, 100.0)
    assert empty.tradable and empty.score == 100.0


# --- engine: slippage / execution net debit ------------------------------

def test_slippage_equals_exec_minus_mid_and_nonneg():
    ch = _abstract()
    v = value_strategy(long_call("X", 100, EXP, 105), ch, asof=ASOF)
    assert v.slippage >= 0
    assert v.slippage == pytest.approx(v.exec_net_debit - v.net_debit, abs=1e-6)
    assert v.exec_net_debit > v.net_debit          # a debit costs MORE to fill
    assert v.liquidity is not None and 0 <= v.liquidity["score"] <= 100


def test_credit_strategy_slippage_reduces_credit():
    ch = _abstract()
    v = value_strategy(short_strangle("X", 100, EXP, 115, 85), ch, asof=ASOF)
    assert v.net_debit < 0                          # a credit
    # Executing reduces the credit received (exec_net_debit is less negative).
    assert v.exec_net_debit > v.net_debit
    assert v.slippage > 0


def test_optimize_liquidity_snaps_to_fillable_strike():
    # A thin target (138) sits within 6% of a liquid strike (135); optimisation
    # should remap to the fillable contract.
    cons = [
        OptionContract("X", EXP, 100.0, "call", iv=0.4, bid=4.0, ask=4.1,
                       volume=500, open_interest=3000, source="live"),
        OptionContract("X", EXP, 135.0, "call", iv=0.5, bid=1.0, ask=1.08,
                       volume=200, open_interest=1500, source="live"),
        OptionContract("X", EXP, 140.0, "call", iv=0.6, bid=0.05, ask=0.60,
                       volume=0, open_interest=0, source="live"),
    ]
    chain = OptionChain("X", 100.0, ASOF, rate=0.02, contracts=cons)
    base = value_strategy(long_call("X", 100, EXP, 138), chain, asof=ASOF)
    opt = value_strategy(long_call("X", 100, EXP, 138), chain, asof=ASOF,
                         optimize_liquidity=True)
    assert opt.liquidity_remaps and opt.liquidity_remaps[0]["to_strike"] == 135.0
    assert opt.liquidity["tradable"]
    # base (nearest = 140, untradable) is worse than the optimised pick.
    assert base.contracts_audit[0].strike == 140.0


# --- advisor: liquidity-adjusted ranking ----------------------------------

def test_advisor_demotes_illiquid_and_exposes_liquidity():
    surf = ParametricSkewSurface(atm=0.5, slope=-0.6, curv=0.4)
    ch = OptionChain.abstract("NVDA", 120.0, surf, rate=0.045, asof=ASOF)
    a = analyze_surface(ch.to_surface() or surf, ticker="NVDA", spot=120.0,
                        rate=0.045, asof=ASOF)
    view = FundamentalView(ticker="NVDA", direction="bullish", horizon="months",
                           conviction=4, language="en")
    res = advise(view, ch, a, asof=ASOF, llm_caller=lambda p, system=None: None)
    assert res.candidates
    # Candidates are sorted by liquidity-adjusted score (non-increasing).
    scores = [c.liquidity_adjusted_score for c in res.candidates]
    assert scores == sorted(scores, reverse=True)
    # Each candidate exposes a liquidity read + slippage.
    for c in res.candidates:
        assert c.valuation.liquidity is not None
        assert c.valuation.slippage >= 0
    # A tie on fit must be broken by liquidity (adj ≤ fit always).
    for c in res.candidates:
        assert c.liquidity_adjusted_score <= c.fit_score + 1e-9


# --- API: liquidity surfaces in analyze + strategy_build ------------------

def test_api_analyze_includes_liquidity():
    client = TestClient(app)
    import time
    r = client.post("/api/v1/jobs/options_analyze", json={
        "ticker": "AAPL", "source": "manual", "spot": 180.0, "atm_vol": 0.3,
        "rate": 0.045, "asof": "2026-06-30",
    })
    jid = r.json()["job_id"]
    for _ in range(60):
        j = client.get(f"/api/v1/jobs/{jid}").json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert j["status"] == "done"
    liq = j["partial"]["liquidity"]
    assert liq["source"] == "modeled" and liq["median_score"] is not None


def test_api_strategy_build_compares_liquidity():
    client = TestClient(app)
    import time
    payload = {
        "source": "manual", "spot": 100.0, "atm_vol": 0.30, "rate": 0.02,
        "asof": "2026-06-30", "optimize_liquidity": True,
        "strategy": {
            "name": "long_call", "family": "directional", "ticker": "X", "spot": 100.0,
            "option_legs": [{"kind": "call", "expiry": "2026-09-28",
                             "strike": 100.0, "quantity": 1}],
        },
    }
    r = client.post("/api/v1/jobs/strategy_build", json=payload)
    jid = r.json()["job_id"]
    for _ in range(60):
        j = client.get(f"/api/v1/jobs/{jid}").json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert j["status"] == "done"
    p = j["partial"]
    assert "slippage" in p and "liquidity" in p
    assert p["optimization"]["compared"] is True
    assert p["optimization"]["recommended"] in ("requested", "optimized")


# --- review-fix regressions (gaps that previously let bugs ship) ----------

def test_slippage_invariant_holds_for_mixed_long_short_condor():
    """The slippage==exec-mid identity must hold for a MIXED long+short structure,
    not just pure-long/pure-short (a per-leg sign error would net out otherwise)."""
    from fcn.options.strategies import iron_condor
    ch = _abstract()
    v = value_strategy(iron_condor("X", 100, EXP, 85, 95, 105, 115), ch, asof=ASOF)
    assert v.slippage >= 0
    assert v.slippage == pytest.approx(v.exec_net_debit - v.net_debit, abs=1e-6)


def test_optimize_liquidity_does_not_desync_calendar():
    """optimize_liquidity must not turn a same-strike calendar into a diagonal:
    if a per-leg strike snap would break the structure, the remap is abandoned."""
    from fcn.options.strategies import calendar_spread
    near, far = ASOF + timedelta(days=60), ASOF + timedelta(days=240)
    cons = [
        OptionContract("X", near, 100.0, "call", iv=0.4, bid=4.0, ask=4.2, volume=200, open_interest=1500, source="live"),
        OptionContract("X", near, 103.0, "call", iv=0.4, bid=2.8, ask=2.85, volume=3000, open_interest=9000, source="live"),
        OptionContract("X", far, 100.0, "call", iv=0.4, bid=7.0, ask=7.1, volume=3000, open_interest=9000, source="live"),
    ]
    ch = OptionChain("X", 100.0, ASOF, rate=0.02, contracts=cons)
    v = value_strategy(calendar_spread("X", 100.0, near_expiry=near, far_expiry=far, strike=100.0),
                       ch, asof=ASOF, optimize_liquidity=True)
    strikes = {a.strike for a in v.contracts_audit}
    expiries = {a.expiry for a in v.contracts_audit}
    assert strikes == {100.0}            # same-strike preserved (still a calendar)
    assert len(expiries) == 2            # two distinct expiries preserved


def test_preserves_structure_helper():
    from datetime import date as _d
    from fcn.options.strategies import OptionLeg
    from fcn.options.strategy_engine import _preserves_structure
    e1, e2 = _d(2026, 9, 1), _d(2027, 3, 1)
    cal = [OptionLeg(kind="call", expiry=e1, strike=100, quantity=-1),
           OptionLeg(kind="call", expiry=e2, strike=100, quantity=1)]
    assert _preserves_structure(cal, [100, 100]) is True
    assert _preserves_structure(cal, [103, 100]) is False     # would desync same-strike
    vert = [OptionLeg(kind="call", expiry=e1, strike=100, quantity=1),
            OptionLeg(kind="call", expiry=e1, strike=110, quantity=-1)]
    assert _preserves_structure(vert, [102, 108]) is True      # order kept
    assert _preserves_structure(vert, [112, 108]) is False     # order inverted


def test_negative_or_nan_depth_does_not_crash():
    assert liquidity_score(0.05, -5, -3) >= 0           # no math-domain ValueError
    assert liquidity_score(0.05, float("nan"), float("nan")) >= 0
    c = OptionContract("X", EXP, 100.0, "call", iv=0.4, bid=4.0, ask=4.1,
                       volume=-3, open_interest=-5, source="live")
    liq = contract_liquidity(c, 100.0, 45 / 365)        # must not raise
    assert not liq.tradable                              # negative depth ⇒ no real depth


def test_manual_chain_uses_strategy_spot_not_request_spot():
    """When a strategy payload is present, its spot drives the (manual) chain so
    the spec's strikes land at the intended moneyness."""
    from fcn.api.main import _options_ticker_spot
    class _Req:
        spot = 100.0
        ticker = None
        strategy = {"ticker": "X", "spot": 200.0, "option_legs": []}
    assert _options_ticker_spot(_Req()) == ("X", 200.0)


def test_quoted_slippage_uses_true_absolute_spread():
    """A blown but two-sided quote must price slippage off the real (ask-bid),
    not the clamped rel_spread (which would understate penny-option touch cost)."""
    c = OptionContract("X", EXP, 150.0, "call", iv=0.8, bid=0.05, ask=0.60,
                       volume=5, open_interest=50, source="live")
    liq = contract_liquidity(c, 100.0, 45 / 365)
    assert liq.spread_abs == pytest.approx(0.55)
    assert liq.rel_spread == pytest.approx(0.60)         # rel clamped for scoring
    ch = OptionChain("X", 100.0, ASOF, rate=0.02, contracts=[c])
    v = value_strategy(long_call("X", 100, EXP, 150), ch, asof=ASOF)
    # half-spread per contract = 0.5*0.55*100 = $27.5 (true), not the clamp value.
    assert v.slippage == pytest.approx(0.5 * 0.55 * 100, abs=0.01)
