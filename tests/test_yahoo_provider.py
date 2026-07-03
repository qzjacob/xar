"""Yahoo deep-pull provider tests — fixture DataFrames only: no network, no DB.

structured.upsert_* are monkeypatched to capture rows and the yfinance Ticker is
replaced with a stub, so these exercise exactly the provider's mapping logic."""
from __future__ import annotations

import types
from datetime import date, timedelta

import pandas as pd
import pytest

from xar.providers import yahoo
from xar.storage import structured


class _StubTicker:
    """Duck-typed yf.Ticker: attributes come from the fixture kwargs; anything
    not supplied raises AttributeError (which the provider must swallow)."""

    def __init__(self, **kw):
        self._earnings_dates = kw.pop("earnings_dates", None)
        self.__dict__.update(kw)

    def get_earnings_dates(self, limit=12, offset=0):
        return self._earnings_dates


class _BoomTicker:
    """Every attribute access explodes — per-section failures must be non-fatal."""

    def __getattr__(self, name):
        raise RuntimeError("boom")


@pytest.fixture
def captured(monkeypatch):
    calls = {"fundamental": [], "estimate": [], "rating": [], "calendar": [], "prices": []}
    monkeypatch.setattr(structured, "upsert_fundamental",
                        lambda cid, m, v, **kw: calls["fundamental"].append((cid, m, v, kw)))
    monkeypatch.setattr(structured, "upsert_estimate",
                        lambda cid, m, v, as_of, **kw: calls["estimate"].append(
                            (cid, m, v, as_of, kw)))
    monkeypatch.setattr(structured, "upsert_rating",
                        lambda cid, as_of, **kw: calls["rating"].append((cid, as_of, kw)))
    monkeypatch.setattr(structured, "upsert_calendar",
                        lambda cid, et, d, **kw: calls["calendar"].append((cid, et, d, kw)) or True)
    monkeypatch.setattr(structured, "upsert_prices",
                        lambda cid, sym, bars, **kw: calls["prices"].append(bars) or len(bars))
    monkeypatch.setattr(yahoo, "_ticker", lambda cid: "TEST")
    return calls


def test_pull_analyst_ratings_and_estimates(captured):
    rec = pd.DataFrame([
        {"period": "0m", "strongBuy": 8, "buy": 20, "hold": 5, "sell": 1, "strongSell": 0},
        {"period": "-1m", "strongBuy": 7, "buy": 19, "hold": 6, "sell": 1, "strongSell": 0},
    ])
    pt = {"current": 100.0, "mean": 120.5, "high": 150.0, "low": 90.0, "median": 118.0}
    eps = pd.DataFrame({"avg": [1.5, 6.0], "low": [1.2, 5.5], "high": [1.8, 6.5],
                        "numberOfAnalysts": [12, 14], "growth": [0.25, float("nan")]},
                       index=["0q", "0y"])
    rev = pd.DataFrame({"avg": [2.0e9], "low": [1.8e9], "high": [2.2e9],
                        "numberOfAnalysts": [10], "growth": [0.4]}, index=["+1q"])
    tk = _StubTicker(recommendations=rec, analyst_price_targets=pt,
                     earnings_estimate=eps, revenue_estimate=rev)
    n = yahoo.pull_analyst("c-test", tk=tk)
    assert n == 5  # 2 rating rows + 3 estimate rows

    today = date.today()
    cur = [r for r in captured["rating"] if r[1] == today]
    assert len(cur) == 1
    kw = cur[0][2]
    # current month merges recommendation counts with the price-target snapshot
    assert kw["strong_buy"] == 8 and kw["buy"] == 20 and kw["strong_sell"] == 0
    assert kw["pt_mean"] == 120.5 and kw["pt_high"] == 150.0 and kw["pt_low"] == 90.0
    assert kw["meta"]["pt_median"] == 118.0
    # history months anchor to the 1st (stable as_of -> idempotent re-pulls)
    hist = [r for r in captured["rating"] if r[1] != today]
    assert len(hist) == 1 and hist[0][1].day == 1 and hist[0][2]["strong_buy"] == 7

    by_period = {(r[1], r[4]["period"]): r for r in captured["estimate"]}
    assert ("eps_diluted", "0q") in by_period and ("revenue", "+1q") in by_period
    eps0q = by_period[("eps_diluted", "0q")]
    assert eps0q[2] == 1.5 and eps0q[3] == today
    assert eps0q[4]["n_analysts"] == 12 and eps0q[4]["unit"] == "ratio"
    assert by_period[("eps_diluted", "0y")][4]["meta"]["growth"] is None  # NaN scrubbed
    assert by_period[("revenue", "+1q")][4]["unit"] == "USD"


def test_pull_analyst_price_targets_without_recommendations(captured):
    tk = _StubTicker(analyst_price_targets={"mean": 42.0, "high": 50.0, "low": 30.0})
    assert yahoo.pull_analyst("c-test", tk=tk) == 1
    (_, as_of, kw), = captured["rating"]
    assert as_of == date.today() and kw["pt_mean"] == 42.0 and kw.get("strong_buy") is None


def test_pull_fundamentals_short_interest_and_float(captured):
    info = {"totalRevenue": 5.0e9, "grossMargins": 0.44,
            "sharesShort": 3_000_000, "shortRatio": 2.5,
            "shortPercentOfFloat": 0.031, "floatShares": 95_000_000,
            "sharesShortPriorMonth": 2_500_000, "dateShortInterest": 1_750_000_000}
    tk = _StubTicker(info=info)
    n = yahoo.pull_fundamentals("c-test", tk=tk)
    rows = {m: (v, kw) for _, m, v, kw in captured["fundamental"]}
    assert n == len(rows) == 6
    # canonical short/float keys with the right units, as 'latest' snapshots
    assert rows["short_interest_shares"][0] == 3_000_000
    assert rows["short_interest_shares"][1]["meta"]["prior_month_shares_short"] == 2_500_000
    assert rows["short_ratio"][1]["unit"] == "days"
    assert rows["short_pct_float"][1]["unit"] == "ratio"
    assert rows["float_shares"][1]["unit"] == "count"
    assert rows["float_shares"][1]["period"] == "latest"
    assert rows["float_shares"][1]["freq"] == "snapshot"
    # legacy .info TTM mapping still writes
    assert rows["revenue"][1]["period"] == "TTM" and rows["gross_margin"][1]["unit"] == "ratio"


def test_short_interest_keys_are_canonical_core_metrics():
    from xar.ontology import metric_packs
    from xar.ontology.standards import FIN_METRICS
    for k in ("short_interest_shares", "short_ratio", "short_pct_float", "float_shares"):
        s = metric_packs.spec(k)
        assert s is not None and s.classifiers == ("*",)
        assert k in FIN_METRICS


def test_pull_calendar_actions_and_earnings(captured):
    today = date.today()
    actions = pd.DataFrame(
        {"Dividends": [0.25, 0.0], "Stock Splits": [0.0, 10.0]},
        index=pd.DatetimeIndex([pd.Timestamp(today - timedelta(days=30)),
                                pd.Timestamp(today - timedelta(days=200))]))
    ed = pd.DataFrame(
        {"EPS Estimate": [1.1, 0.9], "Reported EPS": [float("nan"), 0.95],
         "Surprise(%)": [float("nan"), 5.6]},
        index=pd.DatetimeIndex([pd.Timestamp(today + timedelta(days=20)),
                                pd.Timestamp(today - timedelta(days=70))]))
    tk = _StubTicker(actions=actions, earnings_dates=ed)
    assert yahoo.pull_calendar("c-test", tk=tk) == 4
    by_type: dict = {}
    for _, et, d, kw in captured["calendar"]:
        by_type.setdefault(et, []).append((d, kw))
    assert set(by_type) == {"dividend", "split", "earnings"}
    (dd, dkw), = by_type["dividend"]
    assert dkw["meta"]["amount"] == 0.25 and dkw["status"] == "occurred"
    assert dkw["title"] == "TEST dividend"  # stable per type -> dedup company|type|date
    (_, skw), = by_type["split"]
    assert skw["meta"]["ratio"] == 10.0
    fut = next(x for x in by_type["earnings"] if x[0] > today)
    assert fut[1]["status"] == "scheduled" and fut[1]["importance"] == 3
    assert fut[1]["meta"]["eps_estimate"] == 1.1
    assert fut[1]["meta"]["reported_eps"] is None  # NaN scrubbed (json-safe)
    past = next(x for x in by_type["earnings"] if x[0] < today)
    assert past[1]["status"] == "occurred" and past[1]["meta"]["surprise_pct"] == 5.6


def test_pull_calendar_lookback_filters_old_actions(captured):
    old = pd.Timestamp(date.today() - timedelta(days=10 * 365))
    actions = pd.DataFrame({"Dividends": [0.10], "Stock Splits": [0.0]}, index=[old])
    tk = _StubTicker(actions=actions)
    assert yahoo.pull_calendar("c-test", tk=tk) == 0
    assert captured["calendar"] == []


def test_pull_statements_quarterly_series(captured):
    cols = pd.DatetimeIndex(["2026-03-31", "2025-12-31"])
    inc = pd.DataFrame(
        [[100.0, 90.0], [40.0, 36.0], [15.0, 13.0], [10.0, 9.0]],
        index=["Total Revenue", "Gross Profit", "Operating Income", "Net Income"], columns=cols)
    bal = pd.DataFrame([[50.0, 48.0], [20.0, 22.0]],
                       index=["Cash And Cash Equivalents", "Total Debt"], columns=cols)
    cf = pd.DataFrame([[-8.0, -7.0], [12.0, float("nan")]],
                      index=["Capital Expenditure", "Free Cash Flow"], columns=cols)
    tk = _StubTicker(quarterly_income_stmt=inc, quarterly_balance_sheet=bal,
                     quarterly_cashflow=cf)
    assert yahoo.pull_statements("c-test", tk=tk) == 15  # 16 cells minus one NaN
    rows = [(m, kw["period"], kw["period_end"], v) for _, m, v, kw in captured["fundamental"]]
    assert ("revenue", "Q1-2026", date(2026, 3, 31), 100.0) in rows
    assert ("net_income", "Q4-2025", date(2025, 12, 31), 9.0) in rows
    assert ("cash_and_equivalents", "Q1-2026", date(2026, 3, 31), 50.0) in rows
    assert ("total_debt", "Q4-2025", date(2025, 12, 31), 22.0) in rows
    assert ("capex", "Q4-2025", date(2025, 12, 31), -7.0) in rows  # sign kept (outflow)
    assert ("free_cash_flow", "Q1-2026", date(2026, 3, 31), 12.0) in rows
    assert all(kw["freq"] == "quarter" and kw["unit"] == "USD"
               for _, _, _, kw in captured["fundamental"])


def test_sections_survive_broken_ticker(captured):
    tk = _BoomTicker()
    assert yahoo.pull_prices("c-test", tk=tk) == 0
    assert yahoo.pull_fundamentals("c-test", tk=tk) == 0
    assert yahoo.pull_analyst("c-test", tk=tk) == 0
    assert yahoo.pull_calendar("c-test", tk=tk) == 0
    assert yahoo.pull_statements("c-test", tk=tk) == 0


def test_pull_runs_all_sections_non_fatally(captured, monkeypatch):
    stub = _StubTicker(info={"totalRevenue": 1.0})  # everything else missing
    monkeypatch.setattr(yahoo, "_yf", lambda: types.SimpleNamespace(Ticker=lambda sym: stub))
    out = yahoo.pull("c-test")
    assert set(out) == {"prices", "fundamentals", "analyst", "calendar", "statements"}
    assert out["fundamentals"] == 1
    assert out["prices"] == 0 and out["analyst"] == 0 and out["statements"] == 0
