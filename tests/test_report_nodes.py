"""UA-P5:报告 DAG 补喂 —— graph_retrieve 吃论点/季报/宏观,_graph_brief 渲染三砖。"""
from __future__ import annotations

import pytest

from xar.agents import nodes
from xar.agents.state import RunState, new_run_id


@pytest.fixture()
def _rs(seeded_db, monkeypatch):
    rs = RunState(new_run_id(), "deep_report", {"company_id": "now"})
    rs.put("company_id", "now")
    rs.put("company_name", "ServiceNow")
    # 桩底层图检索,只测新增三块
    from xar.retrieval import graphrag
    monkeypatch.setattr(graphrag, "supply_chain", lambda cid: {
        "suppliers": [], "customers": [], "invests_in": [], "single_source_risks": []})
    monkeypatch.setattr(graphrag, "events", lambda cid, since=None, limit=40: [])
    monkeypatch.setattr(graphrag, "semantic", lambda cid, since=None, limit=30: [])
    return rs


def test_graph_retrieve_fills_thesis_earnings_macro(_rs, monkeypatch):
    from xar.api import dashboard
    from xar.research import thesis
    from xar.research import thesis_health
    from xar.macro import view as macro_view

    monkeypatch.setattr(thesis, "latest", lambda cid: {
        "stance": "bull", "conviction": 4, "one_liner": "AI 平台化"})
    monkeypatch.setattr(thesis_health, "health_v3", lambda cid: {
        "overall": "confirming_bull", "debates": [{"key": "d1", "status": "quiet", "lean_now": 0.2}]})
    monkeypatch.setattr(dashboard, "_earnings_block", lambda cid: {
        "event": {"date": "2099-06-30", "daysTo": 3}, "verdict": {"direction": "no_trade",
        "conviction": 0, "version": 1}, "impliedMove": 0.05, "beat": {"n": 4}})
    monkeypatch.setattr(macro_view, "theme_macro_view", lambda t, as_of=None: {"theme": t, "metrics": [
        {"metric_key": "capex.x", "value": 100, "slope": 0.5, "identification": {"watermark": "已识别"}}]})
    monkeypatch.setattr(macro_view, "compact_theme_macro", lambda v, max_metrics=8: v)

    nodes.graph_retrieve(_rs)
    g = _rs.get("graph")
    assert g["thesis"]["stance"] == "bull" and g["thesis"]["health"] == "confirming_bull"
    assert g["earnings"]["verdict"]["direction"] == "no_trade"
    assert g["macro"] and g["macro"][0]["metrics"][0]["value"] == 100

    brief = nodes._graph_brief(_rs)
    assert "投资论点" in brief and "confirming_bull" in brief
    assert "季报事件" in brief and "no_trade" in brief
    assert "宏观勾稽" in brief and "capex.x=100" in brief


def test_graph_retrieve_failsoft(_rs, monkeypatch):
    # 层抛错 → 不沉整轮(graph 基础块仍在)
    from xar.research import thesis
    monkeypatch.setattr(thesis, "latest", lambda cid: (_ for _ in ()).throw(RuntimeError("boom")))
    nodes.graph_retrieve(_rs)          # 不抛
    assert "events" in _rs.get("graph")


def test_report_capability_registered():
    from xar.capabilities import registry
    r = registry.by_name("report")
    assert r is not None and r.kind == "build" and r.duration == "slow"
