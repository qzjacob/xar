"""FG-3: bidirectional coupon <-> strike solve, and the corrected snowball coupon accrual."""
from __future__ import annotations

from fastapi.testclient import TestClient

from fcn.api.main import app

client = TestClient(app)
B = "/api/v1"


def _ts(variant="fcn", strike=100, coupon=None, ki=65):
    ts = client.post(f"{B}/build_termsheet", json={
        "variant": variant, "tickers": ["AAPL", "MSFT"], "notional": 1_000_000, "currency": "USD",
        "trade_date": "2026-07-13", "strike_date": "2026-07-27", "maturity": "2027-01-27",
        "coupon_rate": coupon, "frequency": "monthly", "autocall_barrier": 1.0, "ki_barrier": ki / 100,
        "coupon_barrier": 0.7, "memory": False, "ki_style": "european"}).json()
    ts["underlyings"] = [{**u, "strike": strike / 100} for u in ts["underlyings"]]
    ts["knock_in"] = {"barrier": ki / 100, "style": "european", "settlement": "cash"}
    return ts


_MKT = {"source": "manual", "rate": 0.045, "rho": 0.5,
        "assets": [{"ticker": t, "spot": 100, "atm_vol": 0.30, "skew_slope": -0.4, "skew_curv": 0.3}
                   for t in ["AAPL", "MSFT"]]}


def _solve(ts, **extra):
    return client.post(f"{B}/solve", json={
        "termsheet": ts, "market": _MKT, "mc": {"n_paths": 20000}, "include_greeks": False,
        "include_scenario": False, "note_price_pct": 99, "gross_margin_pct": 0.7, **extra}).json()


def test_coupon_strike_roundtrip():
    # solve the coupon at strike 100 → then solve the strike at that coupon → recover ~100%
    cpn = _solve(_ts(strike=100))["coupon_rate"]
    r = _solve(_ts(strike=100, coupon=cpn), solve_for="strike")
    assert "solved_strike" in r
    assert abs(r["solved_strike"] - 1.0) < 0.015          # within ~1.5%
    assert r["strike_bracketed"] is True


def test_higher_coupon_needs_higher_strike():
    cpn = _solve(_ts(strike=100))["coupon_rate"]
    lo = _solve(_ts(coupon=cpn * 0.7), solve_for="strike")["solved_strike"]
    hi = _solve(_ts(coupon=cpn * 1.3), solve_for="strike")["solved_strike"]
    assert hi > lo                                         # more coupon ⇒ more downside ⇒ higher strike


def test_extreme_coupon_hits_bound():
    r = _solve(_ts(coupon=2.0), solve_for="strike")       # absurd 200% coupon → strike caps at 120%
    assert r["solved_strike"] >= 1.19 and r["strike_bracketed"] is False


def test_snowball_prices_with_positive_coupon_leg():
    # the corrected accrual (to the exit date, not truncated at KI) still yields a positive,
    # finite solved coupon; a snowball is a distinct structure from the fixed-coupon FCN.
    snow = _solve(_ts(variant="snowball", strike=100))
    fcn = _solve(_ts(variant="fcn", strike=100))
    assert 0 < snow["coupon_rate"] < 2.0
    assert abs(snow["coupon_rate"] - fcn["coupon_rate"]) > 1e-4
