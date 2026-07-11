"""FQ-A: reference-grid FCN knobs — Note Price % + Gross Margin % move the solved coupon,
and a 4-name worst-of prices (cap raised 3→4)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from fcn.api.main import app

client = TestClient(app)
B = "/api/v1"


def _ts(tickers):
    return client.post(f"{B}/build_termsheet", json={
        "variant": "fcn", "tickers": tickers, "notional": 1_000_000, "currency": "USD",
        "trade_date": "2026-07-13", "strike_date": "2026-07-13", "maturity": "2027-01-13",
        "coupon_rate": None, "frequency": "monthly", "autocall_barrier": 1.0, "ki_barrier": 0.65,
        "coupon_barrier": 0.7, "memory": True}).json()


def _market(tickers):
    return {"source": "manual", "rate": 0.045, "rho": 0.5,
            "assets": [{"ticker": t, "spot": 100, "atm_vol": 0.30, "skew_slope": -0.4,
                        "skew_curv": 0.3} for t in tickers]}


def _solve(tickers, **knobs):
    return client.post(f"{B}/solve", json={
        "termsheet": _ts(tickers), "market": _market(tickers), "mc": {"n_paths": 20000},
        "include_greeks": False, "include_scenario": False, **knobs}).json()


def test_note_price_and_gross_margin_move_the_coupon():
    base = _solve(["AAPL", "MSFT"])
    knob = _solve(["AAPL", "MSFT"], note_price_pct=99, gross_margin_pct=0.7)
    rich = _solve(["AAPL", "MSFT"], note_price_pct=100, gross_margin_pct=0.0)
    # reoffer target = (note_price - gross_margin)/100
    assert abs(knob["reoffer_fraction"] - 0.983) < 1e-6
    assert abs(rich["reoffer_fraction"] - 1.0) < 1e-6
    # higher note price / lower margin → more value to distribute → strictly higher coupon
    assert rich["coupon_rate"] > knob["coupon_rate"] > 0
    # default (no knobs) uses the standard fee model, distinct from the explicit ones
    assert abs(base["reoffer_fraction"] - knob["reoffer_fraction"]) > 1e-4


def test_four_name_worst_of_prices():
    out = _solve(["AAPL", "MSFT", "NVDA", "AMZN"])
    assert out["coupon_rate"] > 0 and out["pricing"]["price_pct"] > 0


def test_knobs_absent_falls_back_to_fee_model():
    out = _solve(["AAPL", "MSFT"])
    # standard FeeModel total 2.3% → reoffer ~0.977
    assert 0.97 < out["reoffer_fraction"] < 0.98
