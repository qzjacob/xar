"""告警生命周期 + Telegram 推送。

去重靠**数据库结构**而非应用记账:`idx_alerts_one_open` 是 `(task_id) WHERE
state<>'resolved'` 的部分唯一索引,所以「每任务至多一条未解决告警」是硬约束 ——
sweep 每 2 分钟跑一轮,靠代码记「我是不是已经报过了」迟早会漏。

推送节流的取舍:转 down 推一次,之后**每 24h** 提醒一次(未 ack 时),恢复推一次。
不做「每轮都推」——告警疲劳会让人静音整个通道,那等于回到 7 天无人察觉的起点。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ..logging import get_logger
from ..storage import db
from .detector import DOWN, OK, STALE, UNKNOWN

log = get_logger("xar.monitoring.alerts")

_SEV_WARN = "warn"
_SEV_CRITICAL = "critical"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── 通道 ─────────────────────────────────────────────────────────────────────────
def resolve_chat() -> str:
    """推给谁:显式 `monitor_telegram_chat` 优先,否则回退 `telegram_allowed_chats` 首项。
    两者皆空 = 未配(页面提示,并列出 chat_channels 里已知的 chat id 供复制)。"""
    from ..config import get_settings
    s = get_settings()
    explicit = (getattr(s, "monitor_telegram_chat", "") or "").strip()
    if explicit:
        return explicit
    raw = (s.telegram_allowed_chats or "").strip()
    return next((c.strip() for c in raw.split(",") if c.strip()), "")


def channel_status() -> str:
    from ..config import get_settings
    if not get_settings().telegram_bot_token:
        return "no_token"
    return "ok" if resolve_chat() else "no_chat"


def known_chats() -> list[str]:
    """给未配置时的页面横幅用:库里已有的 telegram chat id,复制即可填进 env。"""
    try:
        rows = db.query("SELECT DISTINCT external_id FROM chat_channels "
                        "WHERE channel='telegram' ORDER BY external_id")
        return [str(r["external_id"]) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def push(text: str, *, transport=None) -> str:
    """发一条 Telegram。返回 ok|no_token|no_chat|error:<...>。transport 可注入用于单测。"""
    st = channel_status()
    if st != "ok":
        return st
    try:
        from ..chathy.telegram import TelegramBot
        bot = TelegramBot(transport=transport) if transport else TelegramBot()
        bot.send(resolve_chat(), text)
        return "ok"
    except Exception as e:  # noqa: BLE001 — 推送失败不得影响台账
        log.warning("monitor telegram push failed: %s: %s", type(e).__name__, str(e)[:140])
        return f"error:{type(e).__name__}"


# ── 台账 ─────────────────────────────────────────────────────────────────────────
def open_alert(task_id: str) -> dict | None:
    rows = db.query("SELECT * FROM monitor_alerts WHERE task_id=%s AND state<>'resolved'",
                    (task_id,))
    return rows[0] if rows else None


def open_count() -> int:
    try:
        rows = db.query("SELECT count(*) c, "
                        "count(*) FILTER (WHERE severity='critical') AS crit "
                        "FROM monitor_alerts WHERE state<>'resolved'")
        return int(rows[0]["c"]) if rows else 0
    except Exception:  # noqa: BLE001
        return 0


def summary() -> dict:
    try:
        rows = db.query("SELECT count(*) c, "
                        "count(*) FILTER (WHERE severity='critical') AS crit "
                        "FROM monitor_alerts WHERE state<>'resolved'")
        r = rows[0] if rows else {}
        return {"openAlerts": int(r.get("c") or 0), "openCritical": int(r.get("crit") or 0)}
    except Exception:  # noqa: BLE001
        return {"openAlerts": 0, "openCritical": 0}


def listing(*, scope: str = "open", limit: int = 50) -> list[dict]:
    if scope == "open":
        return db.query("SELECT * FROM monitor_alerts WHERE state<>'resolved' "
                        "ORDER BY severity DESC, opened_at DESC LIMIT %s", (limit,))
    return db.query("SELECT * FROM monitor_alerts ORDER BY opened_at DESC LIMIT %s", (limit,))


def ack(alert_id: int) -> dict:
    db.execute("UPDATE monitor_alerts SET state='acked', acked_at=now() "
               "WHERE id=%s AND state='open'", (alert_id,))
    rows = db.query("SELECT * FROM monitor_alerts WHERE id=%s", (alert_id,))
    return rows[0] if rows else {"error": "not found"}


def resolve(alert_id: int) -> dict:
    db.execute("UPDATE monitor_alerts SET state='resolved', resolved_at=now() "
               "WHERE id=%s AND state<>'resolved'", (alert_id,))
    rows = db.query("SELECT * FROM monitor_alerts WHERE id=%s", (alert_id,))
    return rows[0] if rows else {"error": "not found"}


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    s = int(seconds)
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60}m"
    return f"{s // 86400}d{(s % 86400) // 3600}h"


def _title(task, state: str, detail: dict) -> str:
    who = f"{task.id} — {task.label_cn}"
    if state == DOWN:
        if detail.get("worstBy") == "yield":
            return f"{who}:在尝试但零产出 {_fmt_age(detail.get('yieldAgeS'))}"
        if detail.get("hb", {}).get("queueDeadlock") or detail.get("queueDeadlock"):
            return f"{who}:队列死锁"
        return f"{who}:停摆 {_fmt_age(detail.get('hbAgeS'))}"
    if state == STALE:
        if detail.get("worstBy") == "yield":
            return f"{who}:产出滞后 {_fmt_age(detail.get('yieldAgeS'))}"
        return f"{who}:心跳滞后 {_fmt_age(detail.get('hbAgeS'))}"
    return f"{who}:{state}"


def _message(task, state: str, detail: dict, title: str) -> str:
    lines = [f"[XAR] {state.upper()} {title}"]
    if detail.get("hbAgeS") is not None:
        lines.append(f"心跳 {_fmt_age(detail['hbAgeS'])} 前 (SLA {_fmt_age(detail.get('hbSlaS'))})")
    if detail.get("yieldAgeS") is not None:
        lines.append(f"产出 {_fmt_age(detail['yieldAgeS'])} 前 "
                     f"(SLA {_fmt_age(detail.get('yieldSlaS'))})")
    if detail.get("worstBy") == "yield":
        lines.append("⚠️ 心跳绿但无产出 —— 「尝试过」不等于「有产出」")
    if task.note:
        lines.append(task.note)
    lines.append("http://localhost:8000/jarvy/monitor")
    return "\n".join(lines)


def reconcile(task, state: str, detail: dict, *, now: datetime | None = None,
              muted: bool = False, transport=None) -> dict:
    """把一个任务的当前状态与告警台账对齐。由 sweep 每轮对每个任务调用一次。"""
    now = now or _now()
    cur = open_alert(task.id)

    # ── 恢复 ──
    if state == OK:
        if not cur:
            return {"action": "none"}
        down_for = None
        if cur.get("opened_at"):
            down_for = (now - cur["opened_at"]).total_seconds()
        db.execute("UPDATE monitor_alerts SET state='resolved', resolved_at=%s WHERE id=%s",
                   (now, cur["id"]))
        pushed = "skipped"
        if cur.get("last_notified_at") and not muted:
            pushed = push(f"[XAR] RECOVERED {task.id} — {task.label_cn}\n"
                          f"已恢复(累计异常 {_fmt_age(down_for)})", transport=transport)
        return {"action": "resolved", "push": pushed, "downForS": down_for}

    if state == UNKNOWN:
        # 「读不到信号」不开告警:上线首日大量 key 尚未初始化,一开就是告警洪水。
        # 页面照常显示 unknown,由人判断是否需要关注。
        return {"action": "none", "reason": "unknown not alerted"}

    severity = _SEV_CRITICAL if (state == DOWN and task.severity == _SEV_CRITICAL) else _SEV_WARN
    title = _title(task, state, detail)

    # ── 新开 ──
    if not cur:
        # opened_at/last_notified_at 一律写**传入的 now**,而不是 SQL 的 now():
        # 判定逻辑用的是注入时钟,若时间戳来自 DB 时钟,二者就成了两个钟 —— 提醒节流会算错,
        # 而且整个模块变得无法用 fake clock 测试(这条正是被单测抓出来的)。
        rows = db.query(
            "INSERT INTO monitor_alerts(task_id, severity, title, detail, opened_at) "
            "VALUES (%s,%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING RETURNING id",
            (task.id, severity, title, json.dumps(detail, ensure_ascii=False, default=str), now))
        if not rows:                                   # 并发另一轮抢先插了
            return {"action": "raced"}
        aid = rows[0]["id"]
        pushed = "skipped"
        if severity == _SEV_CRITICAL and not muted:
            pushed = push(_message(task, state, detail, title), transport=transport)
            if pushed == "ok":
                db.execute("UPDATE monitor_alerts SET last_notified_at=%s WHERE id=%s", (now, aid))
        return {"action": "opened", "id": aid, "severity": severity, "push": pushed}

    # ── 升级(warn → critical,如 stale 恶化成 down)──
    escalated = severity == _SEV_CRITICAL and cur["severity"] != _SEV_CRITICAL
    if escalated:
        db.execute("UPDATE monitor_alerts SET severity=%s, title=%s, detail=%s::jsonb "
                   "WHERE id=%s",
                   (severity, title, json.dumps(detail, ensure_ascii=False, default=str),
                    cur["id"]))
        pushed = "skipped"
        if not muted:
            pushed = push(_message(task, state, detail, title), transport=transport)
            if pushed == "ok":
                db.execute("UPDATE monitor_alerts SET last_notified_at=%s WHERE id=%s",
                           (now, cur["id"]))
        return {"action": "escalated", "id": cur["id"], "push": pushed}

    # ── 持续异常:未 ack 且超过提醒间隔 → 每日提醒 ──
    from ..config import get_settings
    remind_h = int(getattr(get_settings(), "monitor_remind_hours", 24))
    last_note = cur.get("last_notified_at")
    due = (severity == _SEV_CRITICAL and cur["state"] == "open" and not muted
           and last_note is not None
           and (now - last_note) >= timedelta(hours=remind_h))
    if due:
        pushed = push(f"[XAR] STILL {state.upper()} {title}\n"
                      f"已持续 {_fmt_age((now - cur['opened_at']).total_seconds())}"
                      f"(ack 可停止提醒)", transport=transport)
        if pushed == "ok":
            db.execute("UPDATE monitor_alerts SET last_notified_at=%s WHERE id=%s",
                       (now, cur["id"]))
        return {"action": "reminded", "id": cur["id"], "push": pushed}
    return {"action": "unchanged", "id": cur["id"]}
