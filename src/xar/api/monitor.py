"""任务监控的 API 层(纯函数,ops.py 风格;路由在 app.py 内联注册)。

读端点一律从 sweep 持久化的快照取数,不现探 —— 这样接口 <10ms,而且**页面看到的与
告警判定看到的是同一份数据**。现探会让二者在排障时不一致,那是最难查的一类问题。
需要现探时用 `?fresh=1`。
"""
from __future__ import annotations

from ..logging import get_logger
from ..monitoring import actions as mon_actions
from ..monitoring import alerts as mon_alerts
from ..monitoring import catalog as mon_catalog
from ..monitoring import sweep as mon_sweep

log = get_logger("xar.api.monitor")


def overview(*, fresh: bool = False) -> dict:
    snap = mon_sweep.snapshot(fresh=fresh)
    return {**snap, "alerts": mon_alerts.listing(scope="open", limit=20),
            "knownChats": mon_alerts.known_chats() if
            mon_alerts.channel_status() != "ok" else []}


def summary() -> dict:
    """侧栏徽章 + 主机 deadman 脚本的轻量端点(不带 tasks 数组)。"""
    snap = mon_sweep.snapshot()
    return {"lastSweepAt": (snap.get("monitor") or {}).get("lastSweepAt"),
            "summary": snap.get("summary") or {},
            **mon_alerts.summary()}


def alerts(*, scope: str = "open", limit: int = 50) -> dict:
    return {"alerts": mon_alerts.listing(scope=scope, limit=limit), **mon_alerts.summary()}


def history(*, task: str | None = None, hours: int = 168) -> dict:
    rows = mon_sweep.history(task_id=task, hours=hours)
    return {"rows": rows, "task": task, "hours": hours}


def ack(alert_id: int) -> dict:
    return {"alert": mon_alerts.ack(alert_id)}


def resolve(alert_id: int) -> dict:
    return {"alert": mon_alerts.resolve(alert_id)}


def act(action_id: str) -> dict:
    """执行 catalog 声明的处置动作(restart:<svc> / dagster:unstick / pull:<source>)。
    白名单校验放在这里:只有某个任务显式声明过的动作才可执行,避免端点变成任意开关。"""
    allowed = {a for t in mon_catalog.all_tasks() for a in t.actions}
    if action_id not in allowed:
        return {"error": f"unknown or undeclared action: {action_id}",
                "allowed": sorted(allowed)}
    return {"action": action_id, "result": mon_actions.dispatch(action_id)}


def set_mute(*, hours: int, tasks: list[str] | None = None) -> dict:
    """维护静音:只压推送,历史与告警台账照记(否则静音期间的停摆会彻底消失在记录里)。"""
    from datetime import datetime, timedelta, timezone

    from ..storage.kvstate import save_state
    if hours <= 0:
        save_state(mon_sweep.MUTE_KEY, {})
        return {"muted": False}
    until = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    payload = {"until": until, "tasks": tasks or ["*"]}
    save_state(mon_sweep.MUTE_KEY, payload)
    return {"muted": True, **payload}
