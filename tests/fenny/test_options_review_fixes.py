"""Regression tests for the Options Desk independent-review fixes.

Each test pins a defect found in the audit so it can't silently come back:
  * implied_vol diverging for OTM strikes
  * theta leaking a nonzero value at expiry
  * advise() crashing on a 'years' horizon (calendar near==far)
  * diagonal_spread shortlisted but unparameterisable
  * margin estimate altitude (defined-risk == max_loss; naked = Reg-T)
  * covered-call / CSP max_loss understated by the old grid floor
  * vol_percentile biased by the implied-vs-realized gap
  * numpy not imported at module scope in the API (live analytics NameError)
  * blotter 500ing on one corrupt entry
  * blotter_add trusting a client-supplied valuation verbatim
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from fcn.api.main import app
from fcn.marketdata.volsurface import ParametricSkewSurface
from fcn.options.advisor import _parameterise, advise
from fcn.options.analytics import analyze_surface
from fcn.options.chain import OptionChain
from fcn.options.greeks import bs_greeks, bs_price, implied_vol
from fcn.options.strategies import bull_call_spread, short_strangle
from fcn.options.strategy_engine import value_strategy
from fcn.options.view import FundamentalView, map_view_to_families

ASOF = date(2026, 6, 30)


def _abstract_chain(atm=0.35, spot=100.0):
    surf = ParametricSkewSurface(atm=atm, slope=-0.5, curv=0.3)
    return OptionChain.abstract("X", spot, surf, rate=0.02, asof=ASOF), surf


def _analytics(chain, surf, history=None):
    return analyze_surface(chain.to_surface() or surf, ticker="X", spot=chain.spot,
                           rate=0.02, asof=ASOF, history=history)


# --- implied_vol robustness (OTM) -----------------------------------------

@pytest.mark.parametrize("strike,kind", [
    (120, "call"), (130, "call"), (150, "call"),
    (80, "put"), (60, "put"), (100, "call"),
])
def test_implied_vol_solves_otm(strike, kind):
    """The safeguarded solver inverts every strike, not just ATM."""
    spot, t, r, true_sigma = 100.0, 0.25, 0.02, 0.35
    px = float(np.asarray(bs_price(spot, strike, t, true_sigma, r, kind=kind)).item())
    iv = implied_vol(px, spot, strike, t, r, kind=kind)
    assert iv is not None
    assert iv == pytest.approx(true_sigma, rel=1e-3)


def test_implied_vol_below_intrinsic_is_none():
    # A target under (forward) intrinsic is genuinely unsolvable → None.
    assert implied_vol(0.001, 100, 80, 0.25, 0.02, kind="call") is None


# --- theta masked at expiry -----------------------------------------------

def test_theta_zero_at_expiry():
    g = bs_greeks(100, 95, 0.0, 0.25, 0.03, kind="call")
    assert float(np.asarray(g.theta)) == 0.0
    g2 = bs_greeks(100, 105, 0.0, 0.25, 0.03, kind="put")
    assert float(np.asarray(g2.theta)) == 0.0


# --- advise() no longer crashes on years horizon --------------------------

def test_advise_neutral_years_spiked_no_crash():
    chain, surf = _abstract_chain(atm=0.45)
    a = _analytics(chain, surf)
    view = FundamentalView(ticker="X", direction="neutral", horizon="years",
                           vol_view="spiked", conviction=4, language="en")
    # calendar_spread must be reachable in the shortlist for this to be a
    # regression of the near==far crash.
    assert "calendar_spread" in [s.name for s in map_view_to_families(view, a)[:3]]
    res = advise(view, chain, a, asof=ASOF, llm_caller=lambda p, system=None: None)
    assert res.candidates                      # produced something
    assert "calendar requires" not in " ".join(res.warnings)


# --- diagonal_spread is parameterisable -----------------------------------

def test_diagonal_spread_parameterises_and_values():
    chain, surf = _abstract_chain()
    a = _analytics(chain, surf)
    view = FundamentalView(ticker="X", direction="bullish", horizon="months",
                           conviction=3, language="en")
    spec = _parameterise("diagonal_spread", view, chain, a, ASOF)
    assert spec is not None and spec.name == "diagonal_spread"
    # near < far (the bug class) and it values without raising.
    exps = sorted({leg.expiry for leg in spec.option_legs})
    assert len(exps) == 2 and exps[0] < exps[1]
    val = value_strategy(spec, chain, asof=ASOF)
    assert val.net_debit == val.net_debit       # not NaN


# --- margin estimate altitude ---------------------------------------------

def test_margin_defined_risk_equals_max_loss():
    chain, _ = _abstract_chain()
    exp = ASOF + timedelta(days=90)
    v = value_strategy(bull_call_spread("X", 100, exp, 100, 110), chain, asof=ASOF)
    assert v.max_loss is not None
    assert v.margin_estimate == pytest.approx(v.max_loss, rel=1e-9)


def test_margin_naked_short_is_regt_positive():
    chain, _ = _abstract_chain()
    exp = ASOF + timedelta(days=90)
    v = value_strategy(short_strangle("X", 100, exp, 110, 90), chain, asof=ASOF)
    assert v.max_loss is None                   # unbounded downside
    # Reg-T per leg: max(20%·S − OTM, 10%·S)·100 + premium, summed over 2 shorts.
    assert v.margin_estimate is not None and v.margin_estimate > 0


# --- max_loss captures the true worst case (spot → 0) ---------------------

def test_covered_call_max_loss_reaches_spot_zero():
    chain, _ = _abstract_chain()
    exp = ASOF + timedelta(days=30)
    from fcn.options.strategies import covered_call
    v = value_strategy(covered_call("X", 100, exp, 110), chain, asof=ASOF)
    # At spot→0 the position is worth 0, so the loss is exactly the net debit
    # paid (stock cost − premium). With the old 0.05·S floor this was ~5% short.
    assert v.max_loss is not None
    assert v.max_loss == pytest.approx(v.net_debit, rel=0.02)


# --- vol_percentile is realized-vs-realized (not IV-vs-RV) ----------------

def test_vol_percentile_high_when_recent_vol_elevated():
    chain, surf = _abstract_chain(atm=0.30)
    rng = np.random.default_rng(7)
    calm = rng.normal(0, 0.007, 230)
    storm = rng.normal(0, 0.035, 30)
    closes = 100 * np.exp(np.cumsum(np.concatenate([calm, storm])))
    a = _analytics(chain, surf, history=closes)
    assert a.vol_1y_percentile is not None
    assert a.vol_1y_percentile >= 70            # recent storm ⇒ high percentile
    assert a.vol_regime in ("high", "extreme")


# --- API: numpy is importable at module scope (live analytics) ------------

def test_api_module_has_numpy():
    import fcn.api.main as m
    assert hasattr(m, "np")


# --- blotter tolerates a corrupt entry ------------------------------------

def test_blotter_skips_corrupt_entry(tmp_path):
    from fcn.options.blotter import BlotterStore, new_entry
    from fcn.options.strategies import long_call

    store = BlotterStore(tmp_path / "blotter.json")
    chain, _ = _abstract_chain()
    good = new_entry(long_call("X", 100, ASOF + timedelta(days=90), 100),
                     value_strategy(long_call("X", 100, ASOF + timedelta(days=90), 100), chain, asof=ASOF))
    store.add(good)
    # Inject a corrupt row alongside the good one.
    raw = json.loads((tmp_path / "blotter.json").read_text())
    raw.append({"id": "bad", "ts": "x", "strategy": {"oops": 1}})  # missing required fields
    (tmp_path / "blotter.json").write_text(json.dumps(raw))
    entries = store.all()                       # must not raise
    assert [e.id for e in entries] == [good.id]


# --- API: blotter_add recomputes the valuation server-side ----------------

def test_blotter_add_recomputes_valuation(tmp_path, monkeypatch):
    import fcn.api.main as m
    from fcn.options.blotter import BlotterStore

    isolated = BlotterStore(tmp_path / "blotter.json")
    orig_get_blotter = m._get_blotter          # keep the lru_cache'd original
    orig_get_blotter.cache_clear()
    monkeypatch.setattr(m, "_get_blotter", lambda: isolated)
    client = TestClient(app)
    try:
        # Post a real long-call spec with a deliberately FABRICATED valuation
        # (zero risk, all-profit) plus manual market inputs → server recomputes.
        r = client.post("/api/v1/blotter", json={
            "source": "manual", "spot": 100.0, "atm_vol": 0.30, "skew_slope": -0.4,
            "rate": 0.02, "asof": "2026-06-30",
            "strategy": {
                "name": "long_call", "family": "directional", "ticker": "X", "spot": 100.0,
                "option_legs": [{"kind": "call", "expiry": "2026-09-28",
                                 "strike": 100.0, "quantity": 1}],
            },
            "valuation": {"net_debit": -1.0, "greeks": {"delta": 0, "gamma": 0, "vega": 0,
                          "theta": 0, "rho": 0, "vanna": 0, "vomma": 0},
                          "max_loss": 0.0, "prob_profit": 1.0},
            "notes": "garbage valuation",
        })
        assert r.status_code == 200
        snap = r.json()["entry"]["valuation_snapshot"]
        # Recomputed: a long call has positive delta and a positive net debit,
        # NOT the fabricated delta=0 / net_debit=-1.
        assert snap["greeks"]["delta"] > 0
        assert snap["net_debit"] > 0
    finally:
        orig_get_blotter.cache_clear()
