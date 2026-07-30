"""停摆检测器回归(2026-07-29 审计产物)。

这一组测试的存在理由就是那三个陷阱。审计当时的实况:
  · Dagster 队列死锁,夜间 pull/extract 连续 7 天零执行,job_ticks 全程 SUCCESS;
  · wechat/futu/gangtise 静默哑火 6.5/24/4 天,而 cadence 戳**至今仍是绿的**;
  · quota/sub_quota 等 key 只在状态变化时才写,「行不存在」既可能健康也可能从未跑过。
检测器一旦退回「只看心跳」,这些测试必须失败。

detector 是纯函数,时钟由参数注入 —— 全组无需 DB、无需 sleep。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from xar.monitoring.detector import (DOWN, OK, STALE, UNCONFIGURED, UNKNOWN, Probe,
                                     confirm, evaluate, worse)

NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)
SLA = 3600.0                       # 1h


def _ago(seconds: float) -> Probe:
    return Probe(NOW - timedelta(seconds=seconds))


# ── 心跳边界 ──────────────────────────────────────────────────────────────────────
def test_fresh_heartbeat_is_ok():
    st, d = evaluate(now=NOW, hb=_ago(60), hb_sla_s=SLA)
    assert st == OK and d["hbAgeS"] == 60.0


def test_exactly_at_sla_is_still_ok():
    """阈值取 `>` 而非 `>=`:正好卡在 SLA 上不该报警,否则节拍与 SLA 相等的任务会长期抖动。"""
    assert evaluate(now=NOW, hb=_ago(SLA), hb_sla_s=SLA)[0] == OK


def test_just_over_sla_is_stale():
    assert evaluate(now=NOW, hb=_ago(SLA + 1), hb_sla_s=SLA)[0] == STALE


def test_beyond_down_multiplier_is_down():
    assert evaluate(now=NOW, hb=_ago(SLA * 3), hb_sla_s=SLA)[0] == STALE
    assert evaluate(now=NOW, hb=_ago(SLA * 3 + 1), hb_sla_s=SLA)[0] == DOWN


def test_clock_skew_does_not_false_positive():
    """探针时间戳略超前于 now(DB 与进程时钟微差)不得判成异常。"""
    assert evaluate(now=NOW, hb=Probe(NOW + timedelta(seconds=5)), hb_sla_s=SLA)[0] == OK


# ── 陷阱③:信号缺失是第三态,不是停摆 ────────────────────────────────────────────
def test_missing_signal_is_unknown_not_down():
    st, d = evaluate(now=NOW, hb=Probe(None), hb_sla_s=SLA)
    assert st == UNKNOWN, "信号缺失被判成 down 会在监控上线首日造成告警洪水"
    assert "no heartbeat" in d["reason"]


def test_unconfigured_short_circuits_before_everything():
    st, _ = evaluate(now=NOW, hb=Probe(None), hb_sla_s=SLA, unconfigured=True)
    assert st == UNCONFIGURED


# ── 陷阱①:「尝试过」≠「有产出」──────────────────────────────────────────────────
def test_green_heartbeat_with_stale_yield_is_not_ok():
    """这就是 wechat 的实况:cadence 戳 6 分钟前刚盖,文档已 6.5 天没进过一篇。"""
    st, d = evaluate(now=NOW, hb=_ago(360), hb_sla_s=2 * 3600,
                     yld=_ago(6.5 * 86400), yield_sla_s=48 * 3600)
    assert st == DOWN, "只看心跳就会复现 2026-07 那次静默哑火无人察觉"
    assert d["worstBy"] == "yield"


def test_yield_slightly_over_sla_is_stale_not_down():
    st, d = evaluate(now=NOW, hb=_ago(60), hb_sla_s=SLA,
                     yld=_ago(50 * 3600), yield_sla_s=48 * 3600)
    assert st == STALE and d["worstBy"] == "yield"


def test_idle_is_not_dead():
    """队列已清空 → 产出信号不参与判定。否则 qwendrain 抽完积压就会被误报成死亡。"""
    st, d = evaluate(now=NOW, hb=_ago(60), hb_sla_s=SLA,
                     yld=_ago(30 * 86400), yield_sla_s=6 * 3600, yield_needed=False)
    assert st == OK
    assert d["yieldSkipped"] == "no pending work"


def test_never_yielded_is_flagged_but_not_down():
    st, d = evaluate(now=NOW, hb=_ago(60), hb_sla_s=SLA,
                     yld=Probe(None), yield_sla_s=48 * 3600)
    assert st == STALE and d["yieldReason"] == "no yield signal ever"


def test_bad_heartbeat_wins_over_good_yield():
    """取较坏者,不是「产出好就放过」。"""
    st, _ = evaluate(now=NOW, hb=_ago(SLA * 10), hb_sla_s=SLA,
                     yld=_ago(10), yield_sla_s=48 * 3600)
    assert st == DOWN


def test_worse_ordering():
    assert worse(OK, DOWN) == DOWN
    assert worse(STALE, DOWN) == DOWN
    assert worse(UNKNOWN, STALE) == STALE
    # unconfigured 与 ok 同级:「没配的东西」不算坏,它只是不参与判定。
    assert worse(OK, UNCONFIGURED) == OK
    assert worse(UNCONFIGURED, DOWN) == DOWN


# ── 跃迁确认(恶化需 2 轮,恢复立即)──────────────────────────────────────────────
def test_first_observation_is_taken_at_face_value():
    st, changed, prev = confirm(None, DOWN, now=NOW)
    assert st == DOWN and changed and prev["state"] == DOWN


def test_worsening_needs_two_consecutive_observations():
    prev = {"state": OK, "since": NOW.isoformat(), "pending_state": None, "pending_count": 0}
    st, changed, prev = confirm(prev, DOWN, now=NOW)
    assert st == OK and not changed, "一次坏观测不应立即报警(抖动会训练人忽略告警)"
    st, changed, prev = confirm(prev, DOWN, now=NOW)
    assert st == DOWN and changed


def test_flapping_does_not_confirm():
    """坏—好—坏 不该攒够 2 次:计数只在**连续同向**时累加。"""
    prev = {"state": OK, "since": NOW.isoformat(), "pending_state": None, "pending_count": 0}
    _, _, prev = confirm(prev, DOWN, now=NOW)
    _, _, prev = confirm(prev, OK, now=NOW)
    st, changed, _ = confirm(prev, DOWN, now=NOW)
    assert st == OK and not changed


def test_recovery_is_immediate():
    prev = {"state": DOWN, "since": NOW.isoformat(), "pending_state": None, "pending_count": 0}
    st, changed, _ = confirm(prev, OK, now=NOW)
    assert st == OK and changed, "恢复晚 2 分钟无害,但会让人以为报警不准"


def test_unknown_needs_no_confirmation():
    prev = {"state": OK, "since": NOW.isoformat(), "pending_state": None, "pending_count": 0}
    st, changed, _ = confirm(prev, UNKNOWN, now=NOW)
    assert st == UNKNOWN and changed


def test_stale_then_down_escalates_after_confirmation():
    prev = {"state": STALE, "since": NOW.isoformat(), "pending_state": None, "pending_count": 0}
    st, _, prev = confirm(prev, DOWN, now=NOW)
    assert st == STALE
    st, changed, _ = confirm(prev, DOWN, now=NOW)
    assert st == DOWN and changed


def test_same_state_clears_pending_counter():
    prev = {"state": OK, "since": NOW.isoformat(), "pending_state": DOWN, "pending_count": 1}
    st, changed, new = confirm(prev, OK, now=NOW)
    assert st == OK and not changed
    assert new["pending_state"] is None and new["pending_count"] == 0
