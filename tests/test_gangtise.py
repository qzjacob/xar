"""Gangtise provider — offline unit tests (no network; client.post is monkeypatched).

Grounds the field maps in the live API's real (short) field names + response shapes,
and locks the two data quirks we defend against: the raw-token header and the
balance-sheet companyType/currency positional swap.
"""
from __future__ import annotations

import pytest

from xar.providers import gangtise as g
from xar.providers.gangtise import client


# ── pure helpers ───────────────────────────────────────────────────────────────
def test_rows_zips_positional_arrays():
    data = {"fieldList": ["a", "b", "c"], "list": [[1, 2, 3], [4, 5, 6]]}
    assert client.rows(data) == [{"a": 1, "b": 2, "c": 3}, {"a": 4, "b": 5, "c": 6}]


def test_rows_tolerates_dict_rows_and_empty():
    assert client.rows({"fieldList": ["a"], "list": [{"a": 9}]}) == [{"a": 9}]
    assert client.rows(None) == []
    assert client.rows({"list": []}) == []


def test_period_end_from_yyyymmdd():
    assert g._period_end("20260331") == "2026-03-31"
    assert g._period_end("2026-03-31") == "2026-03-31"
    assert g._period_end("") is None


def test_freq_from_category():
    assert g._freq("2026年一季报") == "quarter"
    assert g._freq("2025年年报") == "annual"
    assert g._freq("2025年中报") == "semi"


def test_num_rejects_junk():
    assert g._num("N/A") is None and g._num("") is None and g._num(None) is None
    assert g._num("12.5") == 12.5 and g._num(3) == 3.0


# ── structured mapping (monkeypatched client + capture upserts) ─────────────────
@pytest.fixture
def captured(monkeypatch):
    funds, ests, docs = [], [], []
    monkeypatch.setattr(g.structured, "upsert_fundamental",
                        lambda cid, m, v, **k: funds.append((m, v, k.get("unit"), k.get("freq"))))
    monkeypatch.setattr(g.structured, "upsert_estimate",
                        lambda cid, m, v, as_of, **k: ests.append((m, v, k.get("period"), k.get("unit"))))
    g._CODE_CACHE["z"] = "600519.SH"
    return {"funds": funds, "ests": ests, "docs": docs}


def test_pull_financials_maps_income_and_defends_currency_swap(monkeypatch, captured):
    # income currency correct; BALANCE has companyType/currency SWAPPED (real Gangtise quirk)
    income = {"fieldList": ["endDate", "category", "currency", "opRev", "opCost", "opProfit",
                            "netProfitAttrParent", "dilutedEPS", "rdExp", "salesExp", "totalAdminExp"],
              "list": [["20260331", "2026年一季报", "人民币", 100.0, 60.0, 30.0, 25.0, 5.0, 8.0, 4.0, 6.0]]}
    balance = {"fieldList": ["endDate", "category", "companyType", "currency", "totalAssets",
                             "totalLiab", "totalEquity", "monetaryAssets", "inventory"],
               "list": [["20260331", "2026年一季报", "人民币", "一般企业", 500.0, 200.0, 300.0, 50.0, 40.0]]}
    cashflow = {"fieldList": ["endDate", "category", "currency", "netOpCashFlows", "cashPaidAcqConstructAssets"],
                "list": [["20260331", "2026年一季报", "人民币", 70.0, 20.0]]}
    posts = {client.INCOME_URL: income, client.BALANCE_URL: balance, client.CASHFLOW_URL: cashflow}
    monkeypatch.setattr(client, "post", lambda url, payload, **k: posts.get(url))

    g.pull_financials("z")
    m = {name: (val, unit, freq) for name, val, unit, freq in captured["funds"]}
    assert m["revenue"][0] == 100.0 and m["cost_of_revenue"][0] == 60.0
    assert m["operating_income"][0] == 30.0 and m["net_income"][0] == 25.0
    assert m["gross_profit"][0] == 40.0                      # opRev-opCost computed
    assert m["gross_margin"] == (0.4, "ratio", "quarter")
    assert m["sga_expense"][0] == 10.0                       # salesExp+totalAdminExp
    assert m["free_cash_flow"][0] == 50.0                    # ocf-capex
    # currency-swap defense: balance 'currency' position holds '一般企业' → still CNY
    assert m["total_assets"][1] == "CNY" and m["cash_and_equivalents"][1] == "CNY"
    assert m["revenue"][2] == "quarter"


def test_pull_valuation_writes_multiple_and_percentile(monkeypatch, captured):
    def fake_post(url, payload, **k):
        ind = payload.get("indicator")
        return {"fieldList": ["tradeDate", "value", "percentileRank"],
                "list": [["2026-07-03", {"peTtm": 18.0, "psTtm": 8.6, "pbMrq": 5.5}[ind],
                          {"peTtm": 75.0, "psTtm": 60.0, "pbMrq": 40.0}[ind]]]}
    monkeypatch.setattr(client, "post", fake_post)
    g.pull_valuation("z")
    m = {name: val for name, val, _u, _f in captured["funds"]}
    assert m["pe_ratio"] == 18.0 and m["pe_percentile"] == 75.0
    assert m["pb_ratio"] == 5.5 and m["ps_percentile"] == 60.0


def test_pull_forecasts_estimates_scaled(monkeypatch, captured):
    data = {"updateList": [{"date": "2026-07-01", "fieldList": [
        {"forecastYear": "2026E", "netIncome": 800.0, "eps": 5.0, "netIncomeYoy": 10.0,
         "pe": 20.0, "roe": 25.0}]}]}
    monkeypatch.setattr(client, "post", lambda url, payload, **k: data)
    g.pull_forecasts("z")
    m = {name: (val, period, unit) for name, val, period, unit in captured["ests"]}
    assert m["net_income"] == (800.0 * 1_000_000, "2026E", "CNY")   # 百万 → CNY
    assert m["eps_diluted"][0] == 5.0
    assert m["earnings_growth"] == (0.10, "2026E", "ratio")         # percent → fraction
    assert m["roe"][0] == 0.25 and m["pe_ratio"][0] == 20.0


def test_pull_research_saves_docs(monkeypatch):
    saved = []
    monkeypatch.setattr("xar.ingestion.base.save", lambda doc: saved.append(doc))
    monkeypatch.setattr(g, "company_by_id", lambda cid: {"name": "测试 Co"})
    monkeypatch.setattr(client, "post",
                        lambda url, payload, **k: {"content": "投研正文", "date": "2026-07-01"})
    g._CODE_CACHE["z"] = "600519.SH"
    n = g.pull_research("z")
    assert n == 3 and {d.doc_type for d in saved} == {"one_pager", "investment_logic", "peer_comparison"}
    assert all(d.source == "gangtise" and d.permission == "grey" for d in saved)


def test_available_false_when_disabled(monkeypatch):
    monkeypatch.setattr(g.client, "_auth", lambda force=False: None)
    assert g.available() is False
    assert g.pull("z") == {}
