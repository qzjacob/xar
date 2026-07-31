"""告警生命周期回归。

核心不变量:**一个任务至多一条未解决告警**(靠部分唯一索引,不靠应用记账),
以及**推送必须节流**——sweep 每 2 分钟一轮,每轮都推会让人静音整个通道,
那就等于回到「7 天无人察觉」的起点。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from xar.monitoring import alerts, catalog
from xar.monitoring.detector import DOWN, OK, STALE, UNKNOWN

NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)


def _task(tid="t.demo", severity=catalog.CRITICAL) -> catalog.Task:
    return catalog.Task(id=tid, label=tid, label_cn="演示任务", group="platform",
                        severity=severity, heartbeat=lambda: catalog.Probe(None),
                        hb_sla_s=60, note="")


@pytest.fixture
def pushes(monkeypatch):
    """记录推送而不真发。返回的 list 即推送流水。"""
    sent: list[str] = []

    def fake_push(text: str, *, transport=None) -> str:
        sent.append(text)
        return "ok"

    monkeypatch.setattr(alerts, "push", fake_push)
    return sent


DETAIL = {"hbAgeS": 7200.0, "hbSlaS": 60.0}


def test_down_opens_critical_alert_and_pushes_once(isolated_db, pushes):
    t = _task()
    r1 = alerts.reconcile(t, DOWN, DETAIL, now=NOW)
    assert r1["action"] == "opened" and r1["severity"] == "critical"
    assert len(pushes) == 1 and "DOWN" in pushes[0]

    # 第二轮同态:不得重开、不得重推
    r2 = alerts.reconcile(t, DOWN, DETAIL, now=NOW + timedelta(minutes=2))
    assert r2["action"] == "unchanged"
    assert len(pushes) == 1, "同一停摆每轮都推 = 告警疲劳"


def test_only_one_open_alert_per_task(isolated_db, pushes):
    t = _task()
    alerts.reconcile(t, DOWN, DETAIL, now=NOW)
    alerts.reconcile(t, DOWN, DETAIL, now=NOW + timedelta(minutes=2))
    rows = isolated_db.query(
        "SELECT count(*) c FROM monitor_alerts WHERE task_id=%s AND state<>'resolved'", (t.id,))
    assert rows[0]["c"] == 1


def test_warn_severity_task_does_not_push(isolated_db, pushes):
    """warn 级任务停摆只进页内告警流,不打扰手机。"""
    t = _task("t.warn", severity=catalog.WARN)
    r = alerts.reconcile(t, DOWN, DETAIL, now=NOW)
    assert r["action"] == "opened" and r["severity"] == "warn"
    assert not pushes


def test_stale_opens_warn_then_escalates_to_critical_on_down(isolated_db, pushes):
    t = _task()
    r1 = alerts.reconcile(t, STALE, DETAIL, now=NOW)
    assert r1["severity"] == "warn" and not pushes

    r2 = alerts.reconcile(t, DOWN, DETAIL, now=NOW + timedelta(minutes=2))
    assert r2["action"] == "escalated"
    assert len(pushes) == 1, "warn→critical 升级必须推一次"


def test_recovery_resolves_and_notifies_with_duration(isolated_db, pushes):
    t = _task()
    alerts.reconcile(t, DOWN, DETAIL, now=NOW)
    r = alerts.reconcile(t, OK, {}, now=NOW + timedelta(hours=3))
    assert r["action"] == "resolved"
    assert r["downForS"] == pytest.approx(3 * 3600, abs=60)
    assert any("RECOVERED" in p for p in pushes)
    rows = isolated_db.query("SELECT state FROM monitor_alerts WHERE task_id=%s", (t.id,))
    assert rows[0]["state"] == "resolved"


def test_recovery_without_prior_push_stays_quiet(isolated_db, pushes):
    """warn 级从未推送过 → 恢复也不必推(否则只会制造噪音)。"""
    t = _task("t.warn2", severity=catalog.WARN)
    alerts.reconcile(t, DOWN, DETAIL, now=NOW)
    r = alerts.reconcile(t, OK, {}, now=NOW + timedelta(hours=1))
    assert r["action"] == "resolved" and r["push"] == "skipped"
    assert not pushes


def test_reminder_fires_after_remind_window(isolated_db, pushes):
    t = _task()
    alerts.reconcile(t, DOWN, DETAIL, now=NOW)
    assert len(pushes) == 1
    # 23h:未到提醒间隔
    alerts.reconcile(t, DOWN, DETAIL, now=NOW + timedelta(hours=23))
    assert len(pushes) == 1
    # 25h:提醒
    r = alerts.reconcile(t, DOWN, DETAIL, now=NOW + timedelta(hours=25))
    assert r["action"] == "reminded"
    assert any("STILL" in p for p in pushes)


def test_alert_opened_while_channel_down_is_delivered_once_channel_returns(isolated_db, pushes,
                                                                          monkeypatch):
    """回归(2026-07-31 实测踩到):告警在通道配通**之前**开出来,last_notified_at 为 NULL。
    若提醒逻辑要求「上次推送时间非空」,这条告警将**永远不会通知** —— 而它恰恰是最需要
    被通知的那一类(dagster.runs 的 critical 在配通 Telegram 前就已开启)。"""
    t = _task()
    # 通道未配:开告警但推不出去
    monkeypatch.setattr(alerts, "push", lambda text, *, transport=None: "no_chat")
    r = alerts.reconcile(t, DOWN, DETAIL, now=NOW)
    assert r["action"] == "opened" and r["push"] == "no_chat"
    rows = isolated_db.query("SELECT last_notified_at FROM monitor_alerts WHERE task_id=%s",
                             (t.id,))
    assert rows[0]["last_notified_at"] is None

    # 通道修好:下一轮必须补发首条,不必等 24h
    monkeypatch.setattr(alerts, "push", lambda text, *, transport=None:
                        (pushes.append(text) or "ok"))
    r2 = alerts.reconcile(t, DOWN, DETAIL, now=NOW + timedelta(minutes=2))
    assert r2["action"] == "first_delivery", "通道恢复后必须补发,否则永远静默"
    assert len(pushes) == 1 and "DOWN" in pushes[0]

    # 补发之后回归正常节流:不再每轮推
    r3 = alerts.reconcile(t, DOWN, DETAIL, now=NOW + timedelta(minutes=4))
    assert r3["action"] == "unchanged" and len(pushes) == 1


def test_ack_stops_reminders_but_keeps_alert_open(isolated_db, pushes):
    t = _task()
    r = alerts.reconcile(t, DOWN, DETAIL, now=NOW)
    alerts.ack(r["id"])
    r2 = alerts.reconcile(t, DOWN, DETAIL, now=NOW + timedelta(hours=25))
    assert r2["action"] == "unchanged", "ack 之后不应再提醒"
    assert len(pushes) == 1
    rows = isolated_db.query("SELECT state FROM monitor_alerts WHERE id=%s", (r["id"],))
    assert rows[0]["state"] == "acked", "ack 不等于关闭:告警仍需可见"


def test_mute_suppresses_push_but_still_records(isolated_db, pushes):
    """静音只压推送 —— 台账照记,否则静音期间的停摆会彻底消失在记录里。"""
    t = _task()
    r = alerts.reconcile(t, DOWN, DETAIL, now=NOW, muted=True)
    assert r["action"] == "opened" and r["push"] == "skipped"
    assert not pushes
    rows = isolated_db.query("SELECT count(*) c FROM monitor_alerts WHERE task_id=%s", (t.id,))
    assert rows[0]["c"] == 1


def test_unknown_state_does_not_open_alert(isolated_db, pushes):
    """「读不到信号」不报警:上线首日大量 key 尚未初始化,一开就是告警洪水。"""
    t = _task()
    r = alerts.reconcile(t, UNKNOWN, {"reason": "no heartbeat signal"}, now=NOW)
    assert r["action"] == "none"
    rows = isolated_db.query("SELECT count(*) c FROM monitor_alerts WHERE task_id=%s", (t.id,))
    assert rows[0]["c"] == 0
    assert not pushes


def test_ok_with_no_open_alert_is_a_noop(isolated_db, pushes):
    assert alerts.reconcile(_task(), OK, {}, now=NOW)["action"] == "none"


# ── 通道解析 ──────────────────────────────────────────────────────────────────────
def test_push_reports_unconfigured_instead_of_raising(monkeypatch):
    """未配 token/chat 时 push 必须安静返回状态码 —— 页内告警链路不能被它带崩。"""
    monkeypatch.setattr(alerts, "channel_status", lambda: "no_chat")
    assert alerts.push("hi") == "no_chat"


def test_explicit_chat_wins_over_allowed_list(monkeypatch):
    class S:
        monitor_telegram_chat = "999"
        telegram_allowed_chats = "42,43"
        telegram_bot_token = "tok"
    monkeypatch.setattr("xar.config.get_settings", lambda: S())
    assert alerts.resolve_chat() == "999"
    assert alerts.channel_status() == "ok"


def test_falls_back_to_first_allowed_chat(monkeypatch):
    class S:
        monitor_telegram_chat = ""
        telegram_allowed_chats = " 42 , 43 "
        telegram_bot_token = "tok"
    monkeypatch.setattr("xar.config.get_settings", lambda: S())
    assert alerts.resolve_chat() == "42"


def test_dedicated_alert_bot_token_wins(monkeypatch):
    """告警走**专用 bot**,不混进 Chathy 的对话 bot ——
    换掉其中一个不该连累另一个。"""
    class S:
        monitor_telegram_token = "alert-bot-token"
        telegram_bot_token = "chathy-bot-token"
        monitor_telegram_chat = "42"
        telegram_allowed_chats = ""
        enable_telegram = True
    monkeypatch.setattr("xar.config.get_settings", lambda: S())
    assert alerts.resolve_token() == "alert-bot-token"

    seen: list[str] = []

    def transport(method, payload, token, timeout):
        seen.append(token)
        return {"ok": True}

    assert alerts.push("hi", transport=transport) == "ok"
    assert seen == ["alert-bot-token"], "推送必须用告警 bot 的 token"


def test_falls_back_to_chathy_bot_when_no_dedicated_token(monkeypatch):
    class S:
        monitor_telegram_token = ""
        telegram_bot_token = "chathy-bot-token"
        monitor_telegram_chat = "42"
        telegram_allowed_chats = ""
    monkeypatch.setattr("xar.config.get_settings", lambda: S())
    assert alerts.resolve_token() == "chathy-bot-token"
    assert alerts.channel_status() == "ok"


def test_bot_username_is_not_a_valid_chat_id(monkeypatch):
    """回归:XAR_MONITOR_ID 填的是 bot 用户名(xar_alertbot)而不是数字 chat id。
    这条链路唯一不直观的一步就是「bot 无法主动发起会话」,所以 chat 必须是人先发消息后
    才拿得到的那个数字。这里只守住「用户名不会被当成 chat 用」这个最小契约。"""
    class S:
        monitor_telegram_token = "tok"
        telegram_bot_token = ""
        monitor_telegram_chat = ""          # 用户名不该被写进这里
        telegram_allowed_chats = ""
    monkeypatch.setattr("xar.config.get_settings", lambda: S())
    assert alerts.channel_status() == "no_chat"
    assert alerts.push("x") == "no_chat", "没有数字 chat id 就不该尝试发送"


def test_no_token_reported_distinctly(monkeypatch):
    class S:
        monitor_telegram_chat = "42"
        telegram_allowed_chats = ""
        telegram_bot_token = ""
    monkeypatch.setattr("xar.config.get_settings", lambda: S())
    assert alerts.channel_status() == "no_token"


def test_push_uses_injected_transport(monkeypatch):
    """真走一遍 TelegramBot.send,但用注入的 transport,不发网络请求。"""
    class S:
        monitor_telegram_chat = "42"
        telegram_allowed_chats = ""
        telegram_bot_token = "tok"
        enable_telegram = True
    monkeypatch.setattr("xar.config.get_settings", lambda: S())
    calls: list[dict] = []

    def transport(method, payload, token, timeout):
        calls.append({"method": method, "chat": payload.get("chat_id"),
                      "text": payload.get("text")})
        return {"ok": True}

    assert alerts.push("[XAR] DOWN test", transport=transport) == "ok"
    assert calls and calls[0]["method"] == "sendMessage"
    assert calls[0]["chat"] == "42" and "DOWN" in calls[0]["text"]
