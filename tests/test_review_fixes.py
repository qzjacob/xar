"""复评修复轮(2026-07-20)专项测试:approve 状态闸 / run 异常落 failed / 可选 API token。

对应 CODE_REVIEW §3.1(局部)/§3.7 与 ARCHITECTURE_REVIEW P1-5 —— 两份审核意见
逐条对照代码验证后仍开放、且与自用姿态兼容的修复项。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4


def test_approve_state_machine(seeded_db):
    """running/failed 不可批准;awaiting_approval 才发布;重复批准幂等。"""
    from xar.agents import graph
    from xar.agents.state import RunState
    from xar.storage import db

    rs = RunState(f"test-approve-{uuid4().hex[:8]}", "deep_report", {})
    rs.create()                                     # status='running'
    try:
        out = graph.approve(rs.run_id)
        assert "error" in out and out["status"] == "running"
        db.execute("UPDATE report_runs SET status='failed' WHERE id=%s", (rs.run_id,))
        assert "error" in graph.approve(rs.run_id)  # failed 同样拒绝
        db.execute("UPDATE report_runs SET status='awaiting_approval' WHERE id=%s", (rs.run_id,))
        out = graph.approve(rs.run_id)
        assert out["status"] == "published" and "error" not in out
        out2 = graph.approve(rs.run_id)             # 幂等:不报错,返回既有产物
        assert out2["status"] == "published" and "error" not in out2
    finally:
        db.execute("DELETE FROM report_runs WHERE id=%s", (rs.run_id,))


def test_run_report_unhandled_exception_marks_failed(seeded_db, monkeypatch):
    """任何非预算异常不再把 run 卡死在 'running'——落 failed 且错误入返回体。"""
    from xar.agents import graph, nodes
    from xar.storage import db

    def boom(rs):
        raise RuntimeError("boom")

    monkeypatch.setattr(nodes, "scope", boom)
    out = graph.run_report({"kind": "deep_report"})
    try:
        assert out["status"] == "failed" and "RuntimeError" in out["error"]
        row = db.query("SELECT status FROM report_runs WHERE id=%s", (out["run_id"],))[0]
        assert row["status"] == "failed"
    finally:
        db.execute("DELETE FROM report_runs WHERE id=%s", (out["run_id"],))


def test_api_token_gate(monkeypatch):
    """token 在位:变更类 /api/* 无凭证 401、X-API-Token/Bearer 放行、GET 不拦;
    token 未配置(默认):全放行 —— 零行为变化。"""
    import importlib

    appmod = importlib.import_module("xar.api.app")   # 包 __init__ 把 FastAPI 实例暴露为 app,须取模块本体
    from xar.config import get_settings

    async def passed(_req):
        return "PASSED"

    def req(method, path, headers=None):
        return SimpleNamespace(method=method, url=SimpleNamespace(path=path),
                               headers=headers or {})

    run = asyncio.run
    monkeypatch.setenv("XAR_API_TOKEN", "sekret")
    get_settings.cache_clear()
    try:
        r = run(appmod._api_token_gate(req("POST", "/api/report/x/approve"), passed))
        assert getattr(r, "status_code", None) == 401
        assert run(appmod._api_token_gate(
            req("POST", "/api/x", {"x-api-token": "sekret"}), passed)) == "PASSED"
        assert run(appmod._api_token_gate(
            req("DELETE", "/api/x", {"authorization": "Bearer sekret"}), passed)) == "PASSED"
        assert run(appmod._api_token_gate(req("GET", "/api/health"), passed)) == "PASSED"
        assert run(appmod._api_token_gate(req("POST", "/legacy"), passed)) == "PASSED"  # 非 /api/ 不拦
        monkeypatch.setenv("XAR_API_TOKEN", "")
        get_settings.cache_clear()
        assert run(appmod._api_token_gate(req("POST", "/api/x"), passed)) == "PASSED"
    finally:
        get_settings.cache_clear()
