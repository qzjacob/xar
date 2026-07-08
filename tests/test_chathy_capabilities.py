"""UA-P3:Chathy 新工具 —— schema/读取/refresh 只 schedule/run_status/8k 预算。"""
from __future__ import annotations

import json

import pytest

from xar.capabilities import registry, runs


def _exec(name, args):
    return json.loads(registry.execute(name, args))


def test_new_tools_registered_and_chathy():
    names = {c.name for c in registry.chathy_specs()}
    for t in ("earnings_panel", "earnings_verdict", "run_status", "theme_debates",
              "exploration_frontier", "fenny_quote", "start_report"):
        assert t in names, f"{t} not a chathy capability"


def test_earnings_verdict_read(seeded_db, monkeypatch):
    import datetime as dt

    from xar.research import earnings
    ev = {"scheduled_for": dt.date(2099, 6, 30)}
    monkeypatch.setattr(earnings, "_next_earnings", lambda cid: ev)
    monkeypatch.setattr(earnings, "latest_verdict", lambda cid, d: {
        "version": 2, "direction": "long", "conviction": 7.5, "model": "codex-sub",
        "expected_move": 0.06, "content": {"expected_surprise_zh": "beat", "asymmetry_zh": "下行有限",
        "dimensions": [{"key": "consensus_setup", "score": 1.0}]}})
    out = _exec("earnings_verdict", {"company_id": "now"})
    assert out["direction"] == "long" and out["conviction"] == 7.5 and out["version"] == 2
    assert out["dimensions"][0]["key"] == "consensus_setup"


def test_earnings_verdict_refresh_schedules_not_inline(seeded_db, monkeypatch):
    import datetime as dt

    from xar.research import earnings
    monkeypatch.setattr(earnings, "_next_earnings", lambda cid: {"scheduled_for": dt.date(2099, 6, 30)})
    called = {"build": False}
    monkeypatch.setattr(earnings, "build_verdict",
                        lambda *a, **k: called.update(build=True) or {"status": "built"})
    monkeypatch.setattr(runs, "launch", lambda name, args, **kw: {"run_id": "r123", "status": "queued"})
    out = _exec("earnings_verdict", {"company_id": "now", "refresh": True})
    assert out["scheduled"] is True and out["run_id"] == "r123"
    assert called["build"] is False        # 不内联调 build_verdict


def test_run_status_roundtrip(seeded_db, monkeypatch):
    monkeypatch.setattr(runs, "status", lambda rid: {"run_id": rid, "status": "done", "result": {"x": 1}})
    out = _exec("run_status", {"run_id": "abc"})
    assert out["status"] == "done" and out["result"]["x"] == 1
    monkeypatch.setattr(runs, "status", lambda rid: None)
    assert "error" in _exec("run_status", {"run_id": "missing"})


def test_start_report_schedules(seeded_db, monkeypatch):
    monkeypatch.setattr(runs, "launch", lambda name, args, **kw: {"run_id": "rep1", "status": "queued"})
    out = _exec("start_report", {"company_id": "now"})
    assert out["scheduled"] is True and out["run_id"] == "rep1"


def test_build_capability_not_inline_via_execute(seeded_db):
    # 评审 #13:build 能力不得经 execute() 内联跑(会卡 SSE);返回错误提示走 /api/run
    out = _exec("build_earnings_verdict", {"company_id": "now"})
    assert "error" in out and "/api/run" in out["error"]


def test_theme_debates_caps_by_company(seeded_db, monkeypatch):
    from xar.research import thesis_health
    big = {"theme": "ai_optical", "debates": [
        {"key": "d1", "mean_lean": 0.2, "by_company": [{"company_id": f"c{i}"} for i in range(20)]}]}
    monkeypatch.setattr(thesis_health, "theme_debate_health", lambda t: big)
    out = _exec("theme_debates", {"theme": "ai_optical"})
    assert len(out["debates"][0]["by_company"]) == 8      # 截到 8


@pytest.mark.parametrize("name,args", [
    ("earnings_panel", {"company_id": "now"}),
    ("theme_debates", {"theme": "ai_optical"}),
])
def test_tool_output_within_8k_budget(seeded_db, name, args):
    out = registry.execute(name, args)          # 真跑(seeded_db);execute 保证 ≤ 8k
    assert len(out) <= registry._MAX_RESULT_CHARS
