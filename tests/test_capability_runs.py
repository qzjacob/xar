"""UA-P1:capability_runs 统一异步触发 —— schedule/execute/去重/stale/API 路由。"""
from __future__ import annotations

import pytest

from xar.capabilities import registry, runs
from xar.storage import db


@pytest.fixture()
def _fake_cap(seeded_db, monkeypatch):
    calls = {"n": 0}

    def _fn(x: int = 1):
        calls["n"] += 1
        return {"doubled": x * 2, "call": calls["n"]}

    spec = registry.CapabilitySpec("ua_test_cap", "test", registry._obj({"x": {"type": "integer"}}),
                                   _fn, kind="build", duration="slow", chathy=False)
    monkeypatch.setitem(registry._BY_NAME, "ua_test_cap", spec)
    db.execute("DELETE FROM capability_runs WHERE capability='ua_test_cap'")
    yield calls
    db.execute("DELETE FROM capability_runs WHERE capability='ua_test_cap'")


def test_schedule_execute_done(_fake_cap):
    s = runs.schedule("ua_test_cap", {"x": 5}, origin="cli")
    assert s["status"] == "queued" and "run_id" in s
    out = runs.execute_run(s["run_id"])
    assert out["status"] == "done" and out["result"]["doubled"] == 10
    st = runs.status(s["run_id"])
    assert st["status"] == "done" and st["result"]["doubled"] == 10 and st["origin"] == "cli"


def test_active_dedupe_same_args(_fake_cap):
    s1 = runs.schedule("ua_test_cap", {"x": 3})
    s2 = runs.schedule("ua_test_cap", {"x": 3})       # 活跃去重 → 同 run_id
    assert s2["run_id"] == s1["run_id"] and s2.get("dedup") is True
    s3 = runs.schedule("ua_test_cap", {"x": 9})       # 异参 → 新 run
    assert s3["run_id"] != s1["run_id"]


def test_execute_error_never_raises(_fake_cap, monkeypatch):
    def _boom(**kw):
        raise RuntimeError("kaboom")
    boom_spec = registry.CapabilitySpec("ua_test_cap", "t", registry._obj({}), _boom,
                                        kind="build", duration="slow", chathy=False)
    monkeypatch.setitem(registry._BY_NAME, "ua_test_cap", boom_spec)   # 冻结 dataclass → 换整条
    s = runs.schedule("ua_test_cap", {"x": 1})
    out = runs.execute_run(s["run_id"])              # 不抛
    assert out["status"] == "error" and "kaboom" in out["error"]
    assert runs.status(s["run_id"])["status"] == "error"


def test_stale_running_reaped(_fake_cap):
    s = runs.schedule("ua_test_cap", {"x": 1})
    # 手动把它变成 40min 前的 running(模拟进程死)
    db.execute("UPDATE capability_runs SET status='running', started_at=now() - interval '40 minutes' "
               "WHERE id=%s", (s["run_id"],))
    s2 = runs.schedule("ua_test_cap", {"x": 1})       # 陈旧被收割 → 新 run 起
    assert s2["run_id"] != s["run_id"]
    assert runs.status(s["run_id"])["status"] == "error"


def test_api_routes(_fake_cap):
    from fastapi.testclient import TestClient

    from xar.api.app import app
    client = TestClient(app)
    # 未知能力 404
    assert client.post("/api/run/does_not_exist").status_code == 404
    # build 能力 → run_id
    r = client.post("/api/run/ua_test_cap", json={"x": 7})
    assert r.status_code == 200 and "run_id" in r.json()
    rid = r.json()["run_id"]
    # BackgroundTasks 在 TestClient 内同步跑完 → done
    st = client.get(f"/api/run/{rid}")
    assert st.status_code == 200 and st.json()["status"] in ("done", "queued", "running")
    assert client.get("/api/run/nope").status_code == 404
    caps = client.get("/api/capabilities").json()
    assert any(c["name"] == "build_earnings_verdict" and c["kind"] == "build" for c in caps)


def test_read_fast_capability_inline(seeded_db):
    from fastapi.testclient import TestClient

    from xar.api.app import app
    client = TestClient(app)
    r = client.post("/api/run/coverage", json={"theme": "ai_optical"})
    assert r.status_code == 200 and r.json()["status"] == "done" and "result" in r.json()


def test_judge_shim_returns_run_id(seeded_db, monkeypatch):
    from fastapi.testclient import TestClient

    from xar.api.app import app
    # 桩 execute_run:TestClient 会同步跑 BackgroundTasks,否则会真调 build_verdict(LLM)
    monkeypatch.setattr(runs, "execute_run", lambda rid: {"status": "done"})
    client = TestClient(app)
    db.execute("DELETE FROM capability_runs WHERE capability='build_earnings_verdict'")
    r = client.post("/api/ops/earnings/now/judge?force=false")
    assert r.status_code == 200 and "run_id" in r.json() and r.json()["status"] == "scheduled"
    db.execute("DELETE FROM capability_runs WHERE capability='build_earnings_verdict'")
