"""任务注册表护栏。

最重要的一条:13 个 fetchy 源必须**自动**由 `glm_worker.FETCHY_SOURCES` 生成。
如果哪天有人往 FETCHY_SOURCES 加了源却要手动同步监控清单,那么「加了新源但没被监控」
就会成为新的静默盲区 —— 而这正是本模块要根除的那类问题。
"""
from __future__ import annotations

from xar.monitoring import catalog
from xar.orchestration import glm_worker as gw


def test_every_fetchy_source_is_monitored():
    ids = {t.id for t in catalog.all_tasks()}
    missing = [k for k in gw.FETCHY_SOURCES if f"fetchy.{k}" not in ids]
    assert not missing, f"FETCHY_SOURCES 里的源没被自动纳入监控: {missing}"


def test_ids_unique():
    ids = [t.id for t in catalog.all_tasks()]
    assert len(ids) == len(set(ids))


def test_all_slas_positive():
    for t in catalog.all_tasks():
        assert t.hb_sla_s > 0, t.id
        assert t.down_mult >= 1.0, t.id
        if t.data_yield is not None:
            assert t.yield_sla_s and t.yield_sla_s > 0, f"{t.id} 有产出探针却没有产出 SLA"


def test_yield_sla_is_looser_than_heartbeat_sla():
    """产出 SLA 必须比心跳 SLA 宽松:源本来就可能一整天没有新内容,
    拿心跳的尺度衡量产出会造成夜间必然误报。"""
    for t in catalog.all_tasks():
        if t.yield_sla_s:
            assert t.yield_sla_s >= t.hb_sla_s, t.id


def test_actions_are_well_formed():
    from xar.monitoring import actions as mon_actions
    for t in catalog.all_tasks():
        for a in t.actions:
            kind, _, arg = a.partition(":")
            assert kind in ("restart", "dagster", "pull"), f"{t.id}: {a}"
            if kind == "restart":
                assert arg in mon_actions.RESTARTABLE, f"{t.id}: {a} 不在可重启白名单"
            if kind == "pull":
                assert arg in gw.FETCHY_SOURCES, f"{t.id}: {a} 不是已知的 fetchy 源"


def test_critical_tasks_cover_the_audit_blind_spots():
    """2026-07-29 审计里真正伤到人的那几个必须是 critical(= 会推手机)。"""
    crit = {t.id for t in catalog.all_tasks() if t.severity == catalog.CRITICAL}
    for need in ("worker.glmworker", "worker.qwendrain", "dagster.runs", "dagster.daemons"):
        assert need in crit, f"{need} 应为 critical —— 它停摆过且当时无人察觉"


def test_dual_signal_covers_the_sources_that_went_silent():
    """wechat/futu/gangtise 当年就是「戳绿数据死」。它们必须有产出探针,否则检测无效。"""
    tasks = {t.id: t for t in catalog.all_tasks()}
    for key in ("wechat", "futu_news", "gangtise"):
        t = tasks[f"fetchy.{key}"]
        assert t.data_yield is not None and t.yield_sla_s, f"fetchy.{key} 缺产出探针(双信号失效)"


def test_probe_failure_degrades_to_unknown_not_exception():
    """坏探针不得带崩整轮巡检。"""
    def boom() -> catalog.Probe:
        raise RuntimeError("probe exploded")

    t = catalog.Task(id="t.boom", label="b", label_cn="炸", group="platform",
                     severity=catalog.WARN, heartbeat=boom, hb_sla_s=60)
    hb, yld, needed = catalog.probe(t)
    assert hb.ts is None and "probeError" in hb.detail
    assert yld is None and needed is True


def test_yield_needed_failure_defaults_to_checking():
    """`yield_needed` 判断本身失败时,默认**参与**判定 —— 宁可多看一眼,不可漏报。"""
    t = catalog.Task(id="t.y", label="y", label_cn="y", group="platform",
                     severity=catalog.WARN,
                     heartbeat=lambda: catalog.Probe(None), hb_sla_s=60,
                     data_yield=lambda: catalog.Probe(None), yield_sla_s=120,
                     yield_needed=lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    _, _, needed = catalog.probe(t)
    assert needed is True


def test_is_unconfigured_swallows_errors():
    t = catalog.Task(id="t.u", label="u", label_cn="u", group="platform",
                     severity=catalog.WARN,
                     heartbeat=lambda: catalog.Probe(None), hb_sla_s=60,
                     unconfigured=lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    assert catalog.is_unconfigured(t) is False
