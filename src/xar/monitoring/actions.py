"""面板上的处置动作。全部只经由 kvstate / GraphQL / 既有 API —— 不碰 docker socket。

动作 id 形如 `restart:glmworker` / `dagster:unstick` / `pull:wechat`,由 catalog 的
`Task.actions` 声明,前端据此渲染按钮 —— 这样「哪个任务能做什么」也是 code-as-truth。
"""
from __future__ import annotations

from ..logging import get_logger
from . import control

log = get_logger("xar.monitoring.actions")

RESTARTABLE = ("glmworker", "qwendrain", "subpool")


def restart(service: str) -> dict:
    if service not in RESTARTABLE:
        return {"error": f"not restartable: {service}", "allowed": list(RESTARTABLE)}
    out = control.request_restart(service)
    log.info("monitor: restart requested for %s", service)
    return {"status": "requested", **out,
            "note": "worker 将在下一轮循环检查时干净退出,由 docker restart 策略拉起;"
                    "若进程已卡到进不了循环,请人工 docker restart"}


def dagster_unstick(*, older_than_hours: int = 1) -> dict:
    """终止在飞的 dagster run,释放并发槽 —— 与 deploy/dagster/unstick_run_queue.py 同语义,
    但走 GraphQL,不需要 docker exec。

    ⚠️ 顺序与那份脚本一致的理由:先终止积压再谈恢复。这里一次性终止全部 in-flight,
    所以不存在「释放槽位后陈旧 run 雪崩启动」的窗口。"""
    from . import dagster_gql
    ids = dagster_gql.in_flight_run_ids()
    if not ids:
        return {"status": "noop", "terminated": 0, "note": "no QUEUED/STARTED runs"}
    res = dagster_gql.terminate_runs(ids)
    log.info("monitor: dagster unstick terminated=%d failed=%d",
             len(res["terminated"]), len(res["failed"]))
    return {"status": "done", "terminated": len(res["terminated"]),
            "failed": res["failed"], "runIds": res["terminated"]}


def trigger_pull(source: str) -> dict:
    """让某个 fetchy 源在**下一轮** worker 循环立即到期。零新增 worker 代码,
    也不会打断当前正在跑的那一轮。

    两个实现上的坑,都是实测踩出来的:

    ① **回拨而不是删除**。`_due()` 只比较「戳距今是否超过间隔」(glm_worker._due),
       回拨到间隔之外同样立即到期;而删除会让 `cadence[key]` 消失,于是监控的心跳探针
       读到「信号缺失」→ 判 unknown —— **处置动作把它要修的那个信号弄瞎了**。

    ② **必须在 SQL 里原子改单个字段,不可 read-modify-write 整个 blob**。
       `cadence` 是一个 JSONB blob,glmworker 的 `_stamp` 每轮都在读-改-写它。
       本函数若也读整份再写回,两边就会互相丢更新 —— 实测一次就抹掉了 7 个源的戳
       (它们随后被 worker 重新盖上,但那是白跑一轮拉取)。`jsonb_set` 在行级原子完成,
       不存在这个窗口。

    非 fetchy 源(ops.SOURCES 里的可运行源)走既有的 ops.run_source。"""
    from datetime import datetime, timedelta, timezone

    from ..orchestration import glm_worker as gw
    from ..storage import db
    if source in gw.FETCHY_SOURCES:
        hours = gw.FETCHY_SOURCES[source].get("hours") or 1
        back = (datetime.now(timezone.utc)
                - timedelta(hours=hours + 1)).isoformat(timespec="seconds")
        db.execute(
            "INSERT INTO glm_worker_state(key, value, updated_at) "
            "VALUES ('cadence', jsonb_build_object(%s::text, to_jsonb(%s::text)), now()) "
            "ON CONFLICT (key) DO UPDATE "
            "  SET value = jsonb_set(glm_worker_state.value, ARRAY[%s::text], "
            "                        to_jsonb(%s::text), true), "
            "      updated_at = now()",
            (source, back, source, back))
        return {"status": "due_now", "source": source, "stampedTo": back,
                "note": "cadence 戳已原子回拨到间隔之外,worker 下一轮即拉取"}
    try:
        from ..api import ops
        return {"status": "started", "source": source, "result": ops.run_source(source)}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:140]}", "source": source}


def dispatch(action_id: str) -> dict:
    """执行一个 catalog 声明的 action id。"""
    kind, _, arg = action_id.partition(":")
    if kind == "restart":
        return restart(arg)
    if kind == "dagster" and arg == "unstick":
        return dagster_unstick()
    if kind == "pull":
        return trigger_pull(arg)
    return {"error": f"unknown action: {action_id}"}
