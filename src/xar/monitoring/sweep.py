"""监控巡检:探测 → 判态 → 写历史 → 驱动告警 → 写自身心跳 → 惰性清理。

跑在 app 容器的后台线程里(与 `chathy.telegram.start_background` 同型)。选 app 而不是
新起容器的理由:app 已经握有 Postgres 连接池、同网络可达 dagster GraphQL、以及 Telegram
token —— 监控需要的三样东西它都有,而多一个容器只是多一个会死的东西。

Dagster **不能**充当看门狗:它正是那个死了 7 天的组件。
「谁监控监控者」由两层承担:① 本模块每轮写 `monitor_beat`,页面显示监控自身心跳;
② `deploy/monitor/deadman.sh` 由主机 cron 独立探活(app 整个挂掉时,进程内报警会一起陪葬)。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from ..logging import get_logger
from ..storage import db
from ..storage.kvstate import get_state, save_state
from . import catalog
from .detector import UNCONFIGURED, confirm, evaluate

log = get_logger("xar.monitoring.sweep")

STATES_KEY = "monitor_states"        # {task_id: {state, since, pending_state, pending_count}}
SNAPSHOT_KEY = "monitor_snapshot"    # 上一轮完整快照(GET 端点直接读,避免每次请求现探)
BEAT_KEY = "monitor_beat"            # 监控自身心跳
MUTE_KEY = "monitor_mute"            # {"until": iso, "tasks": ["*"]|[ids]}

_ANCHOR_EVERY = timedelta(hours=6)   # 时间线连续性锚点(否则只有跃迁行,长期 ok 段是空的)
_RETENTION_EVERY = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def muted(task_id: str, *, now: datetime | None = None) -> bool:
    m = get_state(MUTE_KEY)
    if not m:
        return False
    until = m.get("until")
    try:
        if not until or datetime.fromisoformat(str(until).replace("Z", "+00:00")) < (now or _now()):
            return False
    except ValueError:
        return False
    tasks = m.get("tasks") or ["*"]
    return "*" in tasks or task_id in tasks


def _last_history_at() -> dict[str, datetime]:
    rows = db.query("SELECT task_id, max(at) AS at FROM task_status_history GROUP BY task_id")
    return {r["task_id"]: r["at"] for r in rows if r.get("at")}


def _record(task_id: str, state: str, prev: str | None, detail: dict, kind: str) -> None:
    import json
    db.execute("INSERT INTO task_status_history(task_id, state, prev_state, kind, detail) "
               "VALUES (%s,%s,%s,%s,%s::jsonb)",
               (task_id, state, prev, kind,
                json.dumps(detail, ensure_ascii=False, default=str)))


def sweep(*, now: datetime | None = None, notify: bool = True) -> dict:
    """跑一轮巡检并返回快照。`notify=False` 用于 `?fresh=1` 调试与单测:只判态不发报警。"""
    t0 = time.monotonic()
    now = now or _now()
    states = get_state(STATES_KEY)
    last_hist = _last_history_at()
    tasks_out: list[dict] = []
    summary = {"ok": 0, "stale": 0, "down": 0, "unknown": 0, "unconfigured": 0}

    for task in catalog.all_tasks():
        hb, yld, needed = catalog.probe(task)
        observed, detail = evaluate(
            now=now, hb=hb, hb_sla_s=task.hb_sla_s, down_mult=task.down_mult,
            yld=yld, yield_sla_s=task.yield_sla_s, yield_needed=needed,
            unconfigured=catalog.is_unconfigured(task))
        prev_state = (states.get(task.id) or {}).get("state")
        state, changed, new_prev = confirm(states.get(task.id), observed, now=now)
        states[task.id] = new_prev

        if changed:
            _record(task.id, state, prev_state, detail, "transition")
        elif now - last_hist.get(task.id, now - _ANCHOR_EVERY * 2) >= _ANCHOR_EVERY:
            _record(task.id, state, None, detail, "anchor")

        summary[state] = summary.get(state, 0) + 1
        is_muted = muted(task.id, now=now)
        tasks_out.append({
            "id": task.id, "label": task.label, "labelCn": task.label_cn,
            "group": task.group, "severity": task.severity, "state": state,
            "since": new_prev.get("since"), "observed": observed,
            "hbAgeS": detail.get("hbAgeS"), "hbSlaS": task.hb_sla_s,
            "yieldAgeS": detail.get("yieldAgeS"), "yieldSlaS": task.yield_sla_s,
            "detail": detail, "actions": list(task.actions),
            "muted": is_muted, "note": task.note,
        })

        if notify and state != UNCONFIGURED:
            try:
                from . import alerts
                alerts.reconcile(task, state, detail, now=now, muted=is_muted)
            except Exception as e:  # noqa: BLE001 — 告警链路故障绝不能中断巡检
                log.warning("alert reconcile failed for %s: %s", task.id, str(e)[:140])

    save_state(STATES_KEY, states)
    sweep_ms = round((time.monotonic() - t0) * 1000)
    snap = {"tasks": tasks_out, "summary": summary, "ranAt": now.isoformat(),
            "sweepMs": sweep_ms, "monitor": _monitor_meta(now, sweep_ms)}
    save_state(SNAPSHOT_KEY, snap)
    save_state(BEAT_KEY, {"at": now.isoformat(), "sweepMs": sweep_ms,
                          "tasks": len(tasks_out), "down": summary.get("down", 0)})
    _maybe_retain(now)
    return snap


def _monitor_meta(now: datetime, sweep_ms: int) -> dict:
    from . import alerts
    m = get_state(MUTE_KEY)
    return {"lastSweepAt": now.isoformat(), "sweepMs": sweep_ms,
            "telegram": alerts.channel_status(),
            "muteUntil": (m or {}).get("until"),
            "openAlerts": alerts.open_count()}


def _maybe_retain(now: datetime) -> None:
    """惰性清理(每 24h 一次,记在 kvstate 里)。表无界增长本身就是一种停摆。"""
    beat = get_state("monitor_retention")
    last = beat.get("at")
    try:
        if last and (now - datetime.fromisoformat(str(last))) < _RETENTION_EVERY:
            return
    except ValueError:
        pass
    try:
        db.execute("DELETE FROM task_status_history "
                   "WHERE kind='anchor' AND at < now() - interval '30 days'")
        db.execute("DELETE FROM task_status_history "
                   "WHERE kind='transition' AND at < now() - interval '180 days'")
        db.execute("DELETE FROM monitor_alerts "
                   "WHERE state='resolved' AND resolved_at < now() - interval '90 days'")
        save_state("monitor_retention", {"at": now.isoformat()})
    except Exception as e:  # noqa: BLE001
        log.warning("monitor retention failed: %s", str(e)[:140])


def snapshot(*, fresh: bool = False) -> dict:
    """GET 端点的数据源。默认读上一轮持久化的快照 —— 这样接口 <10ms,而且**页面看到的
    与告警判定看到的是同一份数据**(现探会造成二者不一致,排障时最容易踩)。"""
    if fresh:
        return sweep(notify=False)
    snap = get_state(SNAPSHOT_KEY)
    if not snap:
        return {"tasks": [], "summary": {}, "ranAt": None, "sweepMs": None,
                "monitor": {"lastSweepAt": None, "telegram": "unknown", "openAlerts": 0},
                "note": "monitor has not swept yet"}
    return snap


def history(*, task_id: str | None = None, hours: int = 168, limit: int = 2000) -> list[dict]:
    sql = ("SELECT task_id, state, prev_state, kind, detail, at FROM task_status_history "
           "WHERE at > now() - (%s || ' hours')::interval")
    params: list = [str(int(hours))]
    if task_id:
        sql += " AND task_id=%s"
        params.append(task_id)
    sql += " ORDER BY at DESC LIMIT %s"
    params.append(int(limit))
    return db.query(sql, tuple(params))


# ── 常驻线程 ─────────────────────────────────────────────────────────────────────
def run_forever() -> None:
    from ..config import get_settings
    every = max(30, int(getattr(get_settings(), "monitor_sweep_seconds", 120)))
    log.info("monitor sweep up: every=%ss tasks=%d", every, len(catalog.all_tasks()))
    while True:
        try:
            snap = sweep()
            s = snap["summary"]
            if s.get("down") or s.get("stale"):
                log.info("monitor: down=%s stale=%s unknown=%s (%dms)",
                         s.get("down"), s.get("stale"), s.get("unknown"), snap["sweepMs"])
        except Exception as e:  # noqa: BLE001 — 巡检线程绝不退出,否则监控自己成了停摆源
            log.warning("monitor sweep error: %s: %s", type(e).__name__, str(e)[:160])
        time.sleep(every)


def start_background() -> None:
    """app 启动时武装。与 telegram.start_background 同型:未启用则安静跳过。"""
    from ..config import get_settings
    if not getattr(get_settings(), "monitor_enabled", True):
        log.info("monitor disabled (XAR_MONITOR_ENABLED=false)")
        return
    t = threading.Thread(target=run_forever, name="monitor-sweep", daemon=True)
    t.start()
