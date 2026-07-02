"""API tests for the Equity Options Desk endpoints (manual-mode, no Massive)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from fcn.api.main import app

client = TestClient(app)


def _wait_for_job(jid: str, timeout: float = 10.0) -> dict:
    """Poll the job until done/error; raise on error."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/v1/jobs/{jid}").json()
        if r["status"] in ("done", "error"):
            if r["status"] == "error":
                raise RuntimeError(f"job failed: {r.get('error')}")
            return r
        time.sleep(0.05)
    raise TimeoutError(f"job {jid} did not complete in {timeout}s")


_MANUAL = {
    "source": "manual", "spot": 180.0, "atm_vol": 0.30, "skew_slope": -0.4,
    "rate": 0.045, "asof": "2026-06-18",
}


# --- options_analyze ------------------------------------------------------

def test_options_analyze_manual():
    r = client.post("/api/v1/jobs/options_analyze", json={
        "ticker": "AAPL", **_MANUAL,
    })
    assert r.status_code == 200
    job = _wait_for_job(r.json()["job_id"])
    a = job["partial"]["analytics"]
    assert a["ticker"] == "AAPL"
    assert "iv_1m_atm" in a
    assert a["vol_regime"] in ("depressed", "low", "normal", "high", "extreme")
    c = job["partial"]["chain"]
    assert c["ticker"] == "AAPL" and c["spot"] == 180.0


# --- options_advise -------------------------------------------------------

def test_options_advise_manual_template():
    r = client.post("/api/v1/jobs/options_advise", json={
        "ticker": "AAPL", "direction": "bullish", "horizon": "months",
        "conviction": 4, "holding_shares": 100, "language": "zh",
        **_MANUAL,
    })
    assert r.status_code == 200
    job = _wait_for_job(r.json()["job_id"])
    p = job["partial"]
    assert p["narrative_source"] == "template"
    assert len(p["candidates"]) >= 1
    assert p["candidates"][0]["name"]            # has a strategy name
    assert "AAPL" in p["narrative"]


def test_options_advise_bearish_weeks():
    r = client.post("/api/v1/jobs/options_advise", json={
        "ticker": "X", "direction": "bearish", "horizon": "weeks", "conviction": 3,
        "language": "en", **{**_MANUAL, "spot": 100.0},
    })
    job = _wait_for_job(r.json()["job_id"])
    assert job["status"] == "done"
    # Bearish short-horizon → long_put or bearish spread in the candidates.
    names = {c["name"] for c in job["partial"]["candidates"]}
    assert names & {"long_put", "bear_put_spread", "bear_call_spread"}


# --- strategy_build -------------------------------------------------------

def test_strategy_build_long_call():
    payload = {
        "ticker": "AAPL", **_MANUAL,
        "strategy": {
            "name": "long_call", "family": "directional", "ticker": "AAPL", "spot": 180.0,
            "option_legs": [{"kind": "call", "expiry": "2026-12-18",
                             "strike": 180.0, "quantity": 1}],
            "view_tag": "bullish",
        },
    }
    r = client.post("/api/v1/jobs/strategy_build", json=payload)
    job = _wait_for_job(r.json()["job_id"])
    v = job["partial"]
    assert v["net_debit"] > 0
    assert v["max_profit"] is None          # long call has unbounded upside
    assert v["max_loss"] is not None and v["max_loss"] > 0
    assert v["greeks"]["delta"] > 0
    assert len(v["payoff_at_expiry"]) > 50  # full spot scan


def test_strategy_build_iron_condor():
    payload = {
        "ticker": "X", "source": "manual", "spot": 100.0, "atm_vol": 0.30,
        "skew_slope": -0.4, "rate": 0.045, "asof": "2026-06-18",
        "strategy": {
            "name": "iron_condor", "family": "volatility", "ticker": "X", "spot": 100.0,
            "option_legs": [
                {"kind": "put", "expiry": "2026-09-18", "strike": 85.0, "quantity": 1},
                {"kind": "put", "expiry": "2026-09-18", "strike": 95.0, "quantity": -1},
                {"kind": "call", "expiry": "2026-09-18", "strike": 105.0, "quantity": -1},
                {"kind": "call", "expiry": "2026-09-18", "strike": 115.0, "quantity": 1},
            ],
            "view_tag": "vol_down",
        },
    }
    r = client.post("/api/v1/jobs/strategy_build", json=payload)
    job = _wait_for_job(r.json()["job_id"])
    v = job["partial"]
    assert v["net_debit"] < 0               # credit
    assert v["max_profit"] is not None
    assert v["max_loss"] is not None
    assert len(v["breakevens"]) == 2


# --- chain ----------------------------------------------------------------

def test_chain_endpoint_returns_contracts():
    r = client.post("/api/v1/jobs/chain", json={
        "ticker": "AAPL", **_MANUAL,
    })
    job = _wait_for_job(r.json()["job_id"])
    p = job["partial"]
    assert p["summary"]["n_contracts"] > 0
    assert all("strike" in c and "expiry" in c for c in p["contracts"])


# --- blotter CRUD ---------------------------------------------------------

def test_blotter_full_lifecycle(tmp_path, monkeypatch):
    """The blotter is a lazy singleton; isolate by patching the getter."""
    import fcn.api.main as m
    from fcn.options.blotter import BlotterStore

    isolated = BlotterStore(tmp_path / "blotter.json")
    # Clear lru_cache and patch the getter to return our isolated store.
    m._get_blotter.cache_clear()
    monkeypatch.setattr(m, "_get_blotter", lambda: isolated)

    # Add
    add_r = client.post("/api/v1/blotter", json={
        "strategy": {
            "name": "long_call", "family": "directional", "ticker": "X", "spot": 100.0,
            "option_legs": [{"kind": "call", "expiry": "2026-09-18",
                             "strike": 100.0, "quantity": 1}],
        },
        "valuation": {
            "net_debit": 500.0, "greeks": {"delta": 50, "gamma": 0.05, "vega": 100,
                                            "theta": -0.5, "rho": 10, "vanna": 1, "vomma": -1},
            "breakevens": [105.0], "max_profit": None, "max_loss": 500.0,
            "prob_profit": 0.35, "margin_estimate": None, "days_to_expiry": 90,
            "underlying_price": 100.0, "valuation_date": "2026-06-18",
        },
        "notes": "API test",
    })
    entry_id = add_r.json()["entry"]["id"]
    assert entry_id

    # List
    listed = client.get("/api/v1/blotter").json()
    assert len(listed["entries"]) == 1

    # Greeks aggregation
    g = client.get("/api/v1/blotter/greeks").json()
    assert g["n_positions"] == 1
    assert g["delta"] == 50

    # Update
    upd = client.put(f"/api/v1/blotter/{entry_id}", json={"status": "closed"})
    assert upd.json()["entry"]["status"] == "closed"
    # Closed positions shouldn't count toward aggregate.
    g2 = client.get("/api/v1/blotter/greeks").json()
    assert g2["n_positions"] == 0

    # Delete
    del_r = client.delete(f"/api/v1/blotter/{entry_id}")
    assert del_r.json()["removed"] is True
    assert client.get("/api/v1/blotter").json()["entries"] == []
