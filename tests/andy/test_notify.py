"""状态跃迁告警（Phase 2.4b）单测：只挑可处置跃迁，无 webhook 时安静 no-op。"""
from __future__ import annotations

from slx.engine.notify import detect_transitions, post_slack


def test_detect_only_actionable_transitions():
    prev_new = [
        ("a", "inconclusive", "fixation_triggered"),  # 进入终局态 → 告警
        ("b", "open", "falsified"),                   # 进入终局态 → 告警
        ("c", "open", "open"),                        # 无变化 → 不告警
        ("d", "inconclusive", "inconclusive"),        # 无变化 → 不告警
        ("e", "falsified", "open"),                   # 离开终局态（被新数据推翻）→ 告警
        ("f", "open", "inconclusive"),                # 变化但非终局态 → 不告警
    ]
    keys = {t.claim_key for t in detect_transitions(prev_new)}
    assert keys == {"a", "b", "e"}


def test_post_slack_noop_without_webhook():
    """无 webhook → 安静 no-op 返回 False（不抛错、不阻断）。"""
    trans = detect_transitions([("a", "open", "falsified")])
    assert post_slack(trans, "2026-06-23", webhook="") is False
    assert post_slack(trans, "2026-06-23", webhook=None) is False


def test_post_slack_noop_without_transitions():
    assert post_slack([], "2026-06-23", webhook="https://example.invalid/hook") is False


def test_transition_line_formats_marks():
    (t,) = detect_transitions([("junior_jobs_minus67_is_ai", "inconclusive", "fixation_triggered")])
    line = t.line()
    assert "junior_jobs_minus67_is_ai" in line and "固化" in line
