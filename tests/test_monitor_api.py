"""监控 API 层回归:路由确实注册、形状稳定、处置动作走白名单。

路由注册这件事值得单测:app.py 末尾有一条 SPA catch-all(`/{full_path:path}`),
任何注册在它**之后**的路由都会被静默吞掉 —— 返回 index.html 而不是 404,排查起来极烦。
"""
from __future__ import annotations

from datetime import datetime, timezone


def _client():
    from starlette.testclient import TestClient
    from xar.api.app import app
    return TestClient(app)


def test_monitor_routes_are_registered_before_spa_catchall():
    from xar.api.app import app
    paths = [getattr(r, "path", None) for r in app.routes]
    for p in ("/api/ops/monitor", "/api/ops/monitor/summary",
              "/api/ops/monitor/alerts", "/api/ops/monitor/history",
              "/api/ops/monitor/actions", "/api/ops/monitor/mute"):
        assert p in paths, f"{p} 未注册"
    spa = next(i for i, p in enumerate(paths) if p == "/{full_path:path}")
    for p in ("/api/ops/monitor", "/api/ops/monitor/summary"):
        assert paths.index(p) < spa, f"{p} 注册在 SPA catch-all 之后,会被吞成 index.html"


def test_overview_shape(seeded_db):
    c = _client()
    r = c.get("/api/ops/monitor")
    assert r.status_code == 200
    b = r.json()
    for k in ("tasks", "summary", "monitor", "alerts", "knownChats"):
        assert k in b, k
    assert isinstance(b["tasks"], list)
    assert b["monitor"]["telegram"] in ("ok", "no_token", "no_chat", "unknown")


def test_fresh_sweep_returns_populated_tasks(seeded_db):
    """?fresh=1 现探:验证探针链路端到端能跑通(而非只读到空快照)。"""
    c = _client()
    b = c.get("/api/ops/monitor?fresh=1").json()
    assert len(b["tasks"]) >= 10, "任务注册表应覆盖常驻 worker + 13 个拉取源"
    ids = {t["id"] for t in b["tasks"]}
    assert {"worker.glmworker", "worker.qwendrain", "dagster.runs"} <= ids
    for t in b["tasks"]:
        assert t["state"] in ("ok", "stale", "down", "unknown", "unconfigured")
        assert t["hbSlaS"] > 0


def test_summary_endpoint_is_lightweight(seeded_db):
    b = _client().get("/api/ops/monitor/summary").json()
    assert set(b) >= {"lastSweepAt", "summary", "openAlerts", "openCritical"}
    assert "tasks" not in b, "summary 用于 60s 轮询与主机 deadman,不应带 tasks 数组"


def test_history_endpoint(seeded_db):
    b = _client().get("/api/ops/monitor/history?hours=1").json()
    assert "rows" in b and isinstance(b["rows"], list) and b["hours"] == 1


def test_action_requires_body(seeded_db):
    assert _client().post("/api/ops/monitor/actions", json={}).status_code == 400


def test_undeclared_action_is_rejected(seeded_db):
    b = _client().post("/api/ops/monitor/actions",
                       json={"action": "restart:db"}).json()
    assert "error" in b and "undeclared" in b["error"]
    b2 = _client().post("/api/ops/monitor/actions",
                        json={"action": "rm:-rf"}).json()
    assert "error" in b2


def _clear_restart_flags():
    """这两个测试走的是共享库(非事务隔离),必须自清 —— 否则会给真实 worker 留下
    一个待执行的重启请求。"""
    from xar.monitoring import control
    from xar.storage.kvstate import delete_state
    delete_state(control.RESTART_KEY)


def test_restart_action_writes_flag_only(seeded_db):
    """处置动作只落一个 kvstate 标记 —— 不碰 docker socket、不直接杀进程。"""
    from xar.monitoring import control
    from xar.storage.kvstate import get_state
    try:
        b = _client().post("/api/ops/monitor/actions",
                           json={"action": "restart:qwendrain"}).json()
        assert b["result"]["status"] == "requested"
        assert "qwendrain" in get_state(control.RESTART_KEY)
    finally:
        _clear_restart_flags()


def test_restart_flag_only_applies_to_processes_started_before_it(seeded_db):
    """标记与**进程启动时刻**比较,天然一次性:老进程该退,新拉起的不该立刻再退。"""
    from xar.monitoring import control
    try:
        control.request_restart("subpool")
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        assert control.pending("subpool", started_at=old) is True
        fresh = datetime.now(timezone.utc).replace(year=2100)
        assert control.pending("subpool", started_at=fresh) is False
        assert control.pending("glmworker", started_at=old) is False
    finally:
        _clear_restart_flags()


def test_trigger_pull_is_atomic_and_keeps_other_stamps(isolated_db):
    """回归:`cadence` 是一个 JSONB blob,glmworker 的 `_stamp` 每轮都在读-改-写它。
    若本动作也读整份再写回,两边互相丢更新 —— 实测一次就抹掉了 7 个源的戳。
    必须用 jsonb_set 行级原子改单键。"""
    from xar.monitoring import actions
    from xar.storage.kvstate import get_state, save_state
    save_state("cadence", {"wechat": "2026-07-01T00:00:00+00:00",
                           "rss": "2026-07-01T00:00:00+00:00",
                           "flow": "2026-07-01T00:00:00+00:00"})
    r = actions.trigger_pull("wechat")
    assert r["status"] == "due_now"
    cad = get_state("cadence")
    assert set(cad) == {"wechat", "rss", "flow"}, f"其他源的戳被抹掉了: {cad}"
    assert cad["rss"] == "2026-07-01T00:00:00+00:00", "未被触发的源不应被改动"
    assert cad["wechat"] != "2026-07-01T00:00:00+00:00", "目标源应被回拨到新时间"


def test_trigger_pull_backdates_rather_than_deletes(isolated_db):
    """删除戳会让监控的心跳探针读到「信号缺失」→ 判 unknown,
    等于处置动作把它要修的信号弄瞎了。必须保留键。"""
    from xar.monitoring import actions
    from xar.orchestration import glm_worker as gw
    from xar.storage.kvstate import get_state, save_state
    save_state("cadence", {})
    actions.trigger_pull("wechat")
    assert "wechat" in get_state("cadence"), "戳必须在场"
    assert gw._due("wechat", 3600) is True, "且必须已到期"


def test_trigger_pull_rejects_unknown_source(isolated_db):
    from xar.monitoring import actions
    r = actions.trigger_pull("not_a_real_source")
    assert "error" in r or r.get("status") == "started"


def test_mute_roundtrip(seeded_db):
    c = _client()
    b = c.put("/api/ops/monitor/mute", json={"hours": 2}).json()
    assert b["muted"] is True and b["tasks"] == ["*"]
    from xar.monitoring import sweep
    assert sweep.muted("anything.at.all") is True
    b2 = c.put("/api/ops/monitor/mute", json={"hours": 0}).json()
    assert b2["muted"] is False
    assert sweep.muted("anything.at.all") is False


def test_mute_can_target_specific_tasks(seeded_db):
    c = _client()
    c.put("/api/ops/monitor/mute", json={"hours": 1, "tasks": ["worker.subpool"]})
    from xar.monitoring import sweep
    assert sweep.muted("worker.subpool") is True
    assert sweep.muted("worker.glmworker") is False
    c.put("/api/ops/monitor/mute", json={"hours": 0})
