"""API endpoint smoke/regression tests."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from fcn.api.main import app  # noqa: E402

client = TestClient(app)

PRESET = {
    "variant": "phoenix", "tickers": ["AAA", "BBB"], "notional": 1_000_000, "currency": "USD",
    "trade_date": "2026-06-18", "strike_date": "2026-06-18", "maturity": "2027-06-18",
    "coupon_rate": None, "frequency": "quarterly", "autocall_barrier": 1.0,
    "step_down_per_period": 0.0, "ki_barrier": 0.65, "ki_style": "european",
    "settlement": "cash", "coupon_barrier": 0.70, "memory": True,
}
MARKET = {
    "asof": "2026-06-18", "rate": 0.04, "rho": 0.6,
    "assets": [
        {"ticker": "AAA", "spot": 100, "atm_vol": 0.28, "skew_slope": -0.5, "skew_curv": 0.5},
        {"ticker": "BBB", "spot": 100, "atm_vol": 0.32, "skew_slope": -0.5, "skew_curv": 0.5},
    ],
}
MC = {"n_paths": 8_000, "method": "pseudo"}


def test_health_and_spa():
    assert client.get("/api/v1/health").json()["status"] == "ok"
    assert "FCN" in client.get("/").text


def test_build_termsheet():
    ts = client.post("/api/v1/build_termsheet", json=PRESET).json()
    assert ts["coupon"]["type"] == "conditional"
    assert len(ts["autocall"]["dates"]) == len(ts["autocall"]["barriers"])


def test_quote_and_solve():
    ts = client.post("/api/v1/build_termsheet", json=PRESET).json()
    body = {"termsheet": ts, "market": MARKET, "mc": MC,
            "include_greeks": False, "include_scenario": True}
    sol = client.post("/api/v1/solve", json=body).json()
    assert 0.0 < sol["coupon_rate"] < 2.0
    assert sol["pricing"]["price_pct"] == pytest.approx(sol["reoffer_fraction"] * 100, abs=0.5)
    assert len(sol["scenario_table"]) == 5

    qbody = {**body, "coupon_rate": sol["coupon_rate"]}
    q = client.post("/api/v1/quote", json=qbody).json()
    assert q["pricing"]["price_pct"] == pytest.approx(sol["pricing"]["price_pct"], abs=1.0)


def test_quotesheet_has_disclaimer():
    ts = client.post("/api/v1/build_termsheet", json=PRESET).json()
    r = client.post("/api/v1/report/quotesheet", json={"termsheet": ts, "market": MARKET, "mc": MC})
    assert r.status_code == 200
    assert "INDICATIVE TERMS ONLY" in r.text
    assert "Market inputs used" in r.text


def test_async_job_quote_streams_then_completes():
    import time

    ts = client.post("/api/v1/build_termsheet", json=PRESET).json()
    body = {"termsheet": ts, "market": MARKET, "mc": MC,
            "include_greeks": True, "include_scenario": True}
    jid = client.post("/api/v1/jobs/solve", json=body).json()["job_id"]
    saw_partial = False
    result = None
    for _ in range(120):
        j = client.get(f"/api/v1/jobs/{jid}").json()
        if j["status"] == "partial" and j["partial"].get("pricing"):
            saw_partial = True
        if j["status"] == "done":
            result = j
            break
        if j["status"] == "error":
            raise AssertionError(j["error"])
        time.sleep(0.25)
    assert result is not None, "job did not finish"
    assert result["partial"]["pricing"]["price_pct"] > 0
    assert result["partial"]["greeks"] is not None  # Greeks filled in by completion


def test_quotesheet_works_for_participation():
    """Participation notes have no coupon to solve — the quote sheet must still render
    (regression: /report/quotesheet used to call solve_coupon unconditionally -> 500)."""
    preset = {**PRESET, "variant": "sharkfin", "tickers": ["X"], "ko_barrier": 1.3, "participation": 1.0}
    ts = client.post("/api/v1/build_termsheet", json=preset).json()
    market = {**MARKET, "assets": [{"ticker": "X", "spot": 100, "atm_vol": 0.25}]}
    r = client.post("/api/v1/report/quotesheet", json={"termsheet": ts, "market": market, "mc": MC})
    assert r.status_code == 200
    assert "INDICATIVE TERMS ONLY" in r.text


def test_sharkfin_prices_via_api():
    preset = {**PRESET, "variant": "sharkfin", "tickers": ["X"], "ko_barrier": 1.3, "participation": 1.0}
    ts = client.post("/api/v1/build_termsheet", json=preset).json()
    assert ts["participation"]["style"] == "sharkfin"
    market = {**MARKET, "assets": [{"ticker": "X", "spot": 100, "atm_vol": 0.25}]}
    body = {"termsheet": ts, "market": market, "mc": MC,
            "include_greeks": False, "include_scenario": False}
    r = client.post("/api/v1/solve", json=body).json()
    assert r["pricing"]["price_pct"] > 0


def _poll(jid, n=120):
    import time
    for _ in range(n):
        j = client.get(f"/api/v1/jobs/{jid}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.05)
    return j


RANK_ASSETS = [
    {"ticker": "HI", "spot": 100, "atm_vol": 0.55, "skew_slope": -0.5, "skew_curv": 0.5},
    {"ticker": "MID", "spot": 100, "atm_vol": 0.32, "skew_slope": -0.5, "skew_curv": 0.5},
    {"ticker": "LO", "spot": 100, "atm_vol": 0.18, "skew_slope": -0.5, "skew_curv": 0.5},
]


def test_jobs_rank_manual_ranks_by_coupon():
    r = client.post("/api/v1/jobs/rank", json={
        "source": "manual", "rate": 0.045, "top_n": 5,
        "structure": {"tenor_months": 6, "frequency": "quarterly",
                      "protection_pct": 0.70, "reoffer_pct": 0.977},
        "assets": RANK_ASSETS, "tickers": ["HI", "MID", "LO"]})
    j = _poll(r.json()["job_id"])
    assert j["status"] == "done"
    ranked = j["partial"]["ranked"]
    assert [x["ticker"] for x in ranked] == ["HI", "MID", "LO"]  # higher vol -> richer coupon
    assert j["partial"]["ranked_count"] == 3
    assert j["partial"]["source"] == "manual"


def test_jobs_market_read_manual():
    r = client.post("/api/v1/jobs/market_read", json={
        "source": "manual", "rate": 0.043, "lang": "en",
        "assets": [
            {"ticker": "SPY", "spot": 540, "atm_vol": 0.15, "skew_slope": -0.6, "skew_curv": 0.8},
            {"ticker": "QQQ", "spot": 470, "atm_vol": 0.21, "skew_slope": -0.7, "skew_curv": 0.9},
        ]})
    j = _poll(r.json()["job_id"])
    assert j["status"] == "done"
    p = j["partial"]
    assert "metrics" in p and "suitability" in p
    assert p["narrative_source"] in ("template", "llm")
    assert "FCN" in p["suitability"]
