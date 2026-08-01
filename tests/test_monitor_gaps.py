"""2026-08-01 补的三处监控缺口的回归。

三处都不是「想到了没做」,而是**已经造成过实际漏报/误报**:
  ① `alphapai` / `aifinmarket` 是严格头部优先源,却不在 FETCHY_SOURCES 里、没有 cadence 戳,
     面板上根本不存在 —— aifinmarket 停 26.5 小时无人察觉;
  ② dagster 的 **code location** 加载失败时,`daemons`=ok、`runs`=unknown,
     **没有任何信号说得出「今后永远不会再有 run」** —— 06:00 夜跑静默消失;
  ③ 死锁金丝雀 `queued>0 且 started>=cap` 在**每晚夜跑刚起的那一刻**都成立
     (6 pull + 1 extract 占满 7 槽、第二波排队),每天误报一次。

这些测试守的是「判据本身」,不是「代码跑得通」。
"""
from __future__ import annotations

import datetime as dt

import pytest

from xar.monitoring import catalog, dagster_gql
from xar.monitoring.detector import DOWN


# ── ③ 死锁金丝雀:满队列 ≠ 死锁 ────────────────────────────────────────────────
def _runs_payload(*, queued: int, started: int, oldest_age_h: float, cap: int = 7) -> dict:
    import time
    t0 = time.time() - oldest_age_h * 3600
    return {
        "instance": {"runQueueConfig": {"maxConcurrentRuns": cap}},
        "q": {"count": queued},
        "s": {"count": started, "results": [{"startTime": t0} for _ in range(max(started, 0))]},
        "ok": {"results": []},
        "winOk": {"count": 0}, "winFail": {"count": 0}, "winCancel": {"count": 0},
    }


@pytest.mark.parametrize("age_h,expect", [(0.5, False), (2.0, False), (6.5, True), (12.0, True)])
def test_deadlock_needs_the_age_qualifier(monkeypatch, age_h, expect):
    """队列**占满且还有排队**是夜跑刚起的正常形态;只有最老的在飞 run 跑过头才算死锁。

    没有这个限定词,每晚 06:00 都会报一次 —— 告警每天狼来一次,真出事那次就没人信了。
    """
    monkeypatch.setattr(dagster_gql, "_post",
                        lambda q, v=None: _runs_payload(queued=2, started=7, oldest_age_h=age_h))
    r = dagster_gql.run_stats()
    assert r["queueDeadlock"] is expect, (
        f"最老在飞 {age_h}h、阈值 {r['deadlockMinAgeH']}h 时判定应为 {expect}:{r}")


def test_deadlock_false_when_queue_not_full(monkeypatch):
    """没有排队 = 不可能是死锁,哪怕某个 run 已经跑了很久(长任务是合法的)。"""
    monkeypatch.setattr(dagster_gql, "_post",
                        lambda q, v=None: _runs_payload(queued=0, started=7, oldest_age_h=99))
    assert dagster_gql.run_stats()["queueDeadlock"] is False


def test_deadlock_threshold_below_reaper_timeout():
    """阈值必须小于 run_monitoring.max_runtime_seconds(8h),否则回收器先动手,
    金丝雀永远等不到自己该叫的那一刻 —— 这是一个「配置之间的不变量」,值得钉住。"""
    from xar.config import get_settings
    assert 0 < get_settings().monitor_deadlock_min_age_h < 8.0


# ── ② 代码位置探针 ────────────────────────────────────────────────────────────
def _loc_payload(typename: str, msg: str = "", load_status: str = "LOADED") -> dict:
    return {"workspaceOrError": {"__typename": "Workspace", "locationEntries": [
        {"name": "xar.orchestration.definitions", "loadStatus": load_status,
         "locationOrLoadError": {"__typename": typename, **({"message": msg} if msg else {})}}]}}


def test_code_location_healthy(monkeypatch):
    monkeypatch.setattr(dagster_gql, "_post", lambda q, v=None: _loc_payload("RepositoryLocation"))
    p = catalog._dagster_locations_hb()
    assert p.degrade is None and p.detail["broken"] == []


def test_code_location_load_error_is_down(monkeypatch):
    """核心回归:加载失败必须判 down。这正是 2026-08-01 那次漏报 ——
    守护心跳全绿、runs 只是 unknown,而调度器手里已经没有任何可评估对象。"""
    monkeypatch.setattr(dagster_gql, "_post",
                        lambda q, v=None: _loc_payload("PythonError", "ModuleNotFoundError: xar"))
    p = catalog._dagster_locations_hb()
    assert p.degrade == DOWN
    assert "xar.orchestration.definitions" in p.detail["broken"]
    assert "ModuleNotFoundError" in p.detail["reason"]


def test_code_location_loading_is_not_a_failure(monkeypatch):
    """LOADING 是容器刚起来那几十秒的过渡态,不能判故障(否则每次重启都报一次)。"""
    monkeypatch.setattr(dagster_gql, "_post",
                        lambda q, v=None: _loc_payload("PythonError", "starting", "LOADING"))
    assert catalog._dagster_locations_hb().degrade is None


def test_code_location_unreachable_is_unknown(monkeypatch):
    """探针读不到 ⇒ Probe(None) ⇒ unknown,而不是伪造一个坏消息。"""
    def boom(q, v=None):
        raise OSError("connection refused")
    monkeypatch.setattr(dagster_gql, "_post", boom)
    p = catalog._dagster_locations_hb()
    assert p.ts is None and p.degrade is None


# ── ① 接力链两棒 ──────────────────────────────────────────────────────────────
_ORDER = ["alphapai", "alphapai_backfill", "gangtise", "aifinmarket", "alphapai_agents"]


@pytest.mark.parametrize("stage,expect", [(0, False), (2, False), (3, True), (4, True)])
def test_aifinmarket_yield_gated_by_relay_stage(monkeypatch, stage, expect):
    """链尾棒次**没轮到时零产出属正常**。不门控的话,「在排队」会被误报成「已死亡」——
    与 qwendrain 空闲不算死是同一条纪律。"""
    monkeypatch.setattr(catalog, "_chain_state", lambda: {"order": _ORDER, "stage": stage})
    assert catalog._chain_reached("aifinmarket")() is expect


def test_chain_reached_survives_broken_state(monkeypatch):
    """状态读不到/字段畸形时不得抛 —— 观测面 never-raise 契约。"""
    monkeypatch.setattr(catalog, "_chain_state", lambda: {})
    assert catalog._chain_reached("aifinmarket")() is False
    monkeypatch.setattr(catalog, "_chain_state", lambda: {"order": _ORDER, "stage": "x"})
    assert catalog._chain_reached("aifinmarket")() is False


def test_head_sources_are_registered():
    """护栏:这两个源必须在注册表里。它们曾经**完全不在监控中**,
    aifinmarket 停 26.5 小时无人察觉就是这么来的。"""
    ids = {t.id for t in catalog.all_tasks()}
    assert {"fetchy.alphapai", "fetchy.aifinmarket", "dagster.code_locations"} <= ids


def test_alphapai_yield_is_unconditional():
    """alphapai 是第 0 棒、天天首发,产出信号不该被门控 —— 它零产出就是真故障。"""
    t = next(x for x in catalog.all_tasks() if x.id == "fetchy.alphapai")
    assert t.yield_needed is None and t.data_yield is not None and t.yield_sla_s


def test_task_ids_unique():
    ids = [t.id for t in catalog.all_tasks()]
    assert len(ids) == len(set(ids)), "任务 id 重复"


def test_chain_hb_is_shared_but_yield_is_per_source():
    """两棒共享链级心跳(接力在不在走),但各自有独立的产出探针 ——
    心跳相同、产出不同,正是双信号能分开「链在走」与「这一棒有没有货」的原因。"""
    a = next(x for x in catalog.all_tasks() if x.id == "fetchy.alphapai")
    b = next(x for x in catalog.all_tasks() if x.id == "fetchy.aifinmarket")
    assert a.heartbeat is b.heartbeat
    assert a.data_yield is not b.data_yield


def test_probe_timestamps_are_timezone_aware(monkeypatch):
    """探针返回的时间戳必须带时区 —— 裸 datetime 会在与 now(utc) 相减时抛 TypeError,
    而探针一抛就是整轮 sweep 失败。"""
    monkeypatch.setattr(dagster_gql, "_post", lambda q, v=None: _loc_payload("RepositoryLocation"))
    ts = catalog._dagster_locations_hb().ts
    assert ts is not None and ts.tzinfo is not None
    assert (dt.datetime.now(dt.timezone.utc) - ts).total_seconds() < 60


# ── ④ 「没数据」不得掩盖「有数据且是坏的」────────────────────────────────────
def test_degrade_beats_missing_heartbeat():
    """探针拿不到时间戳、但明确断言失败时,必须按断言判,而不是躲进 unknown。

    迁 dagster 存储到 Postgres 后正是这个形态:新库从未有过 SUCCESS ⇒ lastSuccessAt 恒为
    None,于是哪怕每个 run 都失败,旧逻辑也只报 unknown、**永远不会翻 down**,
    一次告警都不会发。「没数据」和「有数据且是坏的」必须分开。
    """
    from xar.monitoring.detector import UNKNOWN, Probe, evaluate
    now = dt.datetime.now(dt.timezone.utc)

    st, detail = evaluate(now=now, hb_sla_s=3600,
                          hb=Probe(None, {"reason": "本窗口 3 个 run 全部失败"}, degrade=DOWN))
    assert st == DOWN and detail["worstBy"] == "signal"
    assert "全部失败" in (detail.get("reason") or "")

    # 对照:没有断言时,信号缺失仍然只判 unknown(第三态不能被这次改动吃掉)
    st2, _ = evaluate(now=now, hb_sla_s=3600, hb=Probe(None, {}))
    assert st2 == UNKNOWN


def test_deadlock_reason_is_visible_in_detail(monkeypatch):
    """判据本身要能被看见 —— 只给一个布尔值没法复核金丝雀该不该叫。"""
    monkeypatch.setattr(dagster_gql, "_post",
                        lambda q, v=None: _runs_payload(queued=2, started=7, oldest_age_h=0.5))
    d = catalog._dagster_runs_hb().detail
    assert d["oldestInFlightH"] == pytest.approx(0.5, abs=0.05)
    assert d["deadlockMinAgeH"] > 0


# ── ⑤ 硬件/资源探针:看「因」而不是「果」──────────────────────────────────────
def _write_slice(tmp_path, current: int, high, mx="max"):
    d = tmp_path / "docker.slice"
    d.mkdir(parents=True, exist_ok=True)
    (d / "memory.current").write_text(str(current))
    (d / "memory.high").write_text(str(high))
    (d / "memory.max").write_text(str(mx))
    return tmp_path


G = 2 ** 30


@pytest.mark.parametrize("cur_g,expect", [(10, None), (21.8, "stale"), (24.1, "down")])
def test_slice_memory_thresholds(monkeypatch, tmp_path, cur_g, expect):
    """判据是 current 相对 **high(软闸)** —— 越过 high 不杀进程、内存压力也只有个位数,
    代价全转移到 IO。没有任何内存指标会报警,这就是它极难被发现的原因。"""
    _write_slice(tmp_path, int(cur_g * G), 24 * G, 28 * G)
    monkeypatch.setattr(catalog, "_HOST_CGROUP", str(tmp_path))
    p = catalog._slice_mem_hb()
    assert p.degrade == expect
    assert p.detail["currentG"] == pytest.approx(cur_g, abs=0.05)


def test_slice_memory_unmounted_is_unknown(monkeypatch, tmp_path):
    """没挂宿主 cgroup 时必须判 unknown 并说清怎么修,而不是静默变绿 ——
    一个「读不到就当没事」的探针比没有探针更危险。"""
    monkeypatch.setattr(catalog, "_HOST_CGROUP", str(tmp_path / "nope"))
    p = catalog._slice_mem_hb()
    assert p.ts is None and "只读挂载" in p.detail["reason"]


def test_slice_memory_high_unset(monkeypatch, tmp_path):
    """high=max(未设软闸)时不该拿 None 去做除法炸掉探针。"""
    _write_slice(tmp_path, 10 * G, "max")
    monkeypatch.setattr(catalog, "_HOST_CGROUP", str(tmp_path))
    p = catalog._slice_mem_hb()
    assert p.degrade is None and p.detail["highG"] is None


@pytest.mark.parametrize("a300,expect", [(1.0, None), (55.0, "stale"), (98.0, "down")])
def test_io_pressure_uses_5min_average(monkeypatch, tmp_path, a300, expect):
    """看 avg300 而不是 avg10:夜跑这类正常重活会让瞬时值抖到很高,
    只有**持续**饱和才是问题(07-31 那晚 avg300 稳定在 98)。"""
    f = tmp_path / "pressure"
    f.write_text(f"some avg10=99.00 avg60=99.00 avg300={a300} total=1\n"
                 f"full avg10=90.00 avg60=90.00 avg300={a300} total=1\n")
    real_open = open

    def fake_open(path, *a, **k):
        return real_open(f if path == "/proc/pressure/io" else path, *a, **k)
    monkeypatch.setattr("builtins.open", fake_open)
    p = catalog._io_pressure_hb()
    assert p.degrade == expect
    assert p.detail["someAvg300"] == a300


def test_hardware_tasks_registered():
    """护栏:07-31 那场事故里,面板 22 个任务全都只看得见果。这两条是唯一看得见因的。"""
    ids = {t.id for t in catalog.all_tasks()}
    assert {"hw.docker_slice", "hw.io_pressure"} <= ids
    t = next(x for x in catalog.all_tasks() if x.id == "hw.docker_slice")
    assert t.severity == "critical", "根因信号必须能推手机"
