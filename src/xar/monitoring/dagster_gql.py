"""Dagster 只读探针 —— 走 GraphQL,不碰 sqlite 卷、不用 docker exec。

为什么是 GraphQL:run 状态与守护心跳都存在 dagster 容器内的 sqlite
(`/dagster/history/runs.db`),该文件在命名卷里,app 容器看不到。而 dagster 的 webserver
就在同一 compose 网络上,`http://dagster:3000/graphql` 直达(2026-07-29 实测 200)。
这样监控不需要任何额外权限,也不会与 dagster 自己的写入抢 sqlite 锁。

⚠️ **只认 `runs`,不认 `job_ticks`**:2026-07-22→07-29 队列死锁期间,ticks 每天照常
SUCCESS(调度确实触发了、RunRequest 确实产生了),而实际零 run 执行。拿 ticks 当健康信号
就是当初 7 天无人察觉的原因。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from ..logging import get_logger

log = get_logger("xar.monitoring.dagster")

# compose 服务名:app 与 dagster 在同一网络。端口是容器内的 3000(宿主映射为 3001)。
DEFAULT_URL = "http://dagster:3000/graphql"
_TIMEOUT = 8.0

_Q_DAEMONS = """
{ instance { daemonHealth { allDaemonStatuses {
      daemonType healthy lastHeartbeatTime } } } }
"""

# 队列深度 + 最近一次成功 run。分三段查而不是一次全量拉,避免把 199 条 run 全搬过来。
_Q_RUNS = """
{ q: runsOrError(filter: {statuses: [QUEUED]}, limit: 1) { ... on Runs { count } }
  s: runsOrError(filter: {statuses: [STARTED]}, limit: 1) { ... on Runs { count } }
  ok: runsOrError(filter: {statuses: [SUCCESS]}, limit: 1) {
        ... on Runs { results { runId jobName endTime } } } }
"""


def _endpoint() -> str:
    from ..config import get_settings
    return getattr(get_settings(), "dagster_graphql_url", "") or DEFAULT_URL


def _post(query: str) -> dict:
    req = urllib.request.Request(
        _endpoint(), data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:   # noqa: S310 — 固定内网端点
        body = json.loads(r.read())
    if body.get("errors"):
        raise RuntimeError(str(body["errors"])[:200])
    return body.get("data") or {}


def _epoch_iso(v) -> str | None:
    if v is None:
        return None
    try:
        return datetime.fromtimestamp(float(v), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def daemon_health() -> dict:
    """7 个守护的健康与最近心跳。任一 healthy=false 由调用方判 down。"""
    try:
        data = _post(_Q_DAEMONS)
    except (urllib.error.URLError, OSError, RuntimeError, ValueError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    rows = (((data.get("instance") or {}).get("daemonHealth") or {})
            .get("allDaemonStatuses") or [])
    return {"ok": True, "daemons": [
        {"daemonType": d.get("daemonType"), "healthy": bool(d.get("healthy")),
         "lastHeartbeatIso": _epoch_iso(d.get("lastHeartbeatTime"))} for d in rows]}


def run_stats(*, max_concurrent: int = 10) -> dict:
    """队列深度 + 最近成功 run。`queueDeadlock` 是 2026-07-22 那次锁死的金丝雀:
    in-flight(QUEUED+STARTED 中的 STARTED 部分)吃满并发槽且长期没有新的成功 ——
    单看「有 run 排队」不算异常,**排队且槽位被占满**才是。"""
    try:
        data = _post(_Q_RUNS)
    except (urllib.error.URLError, OSError, RuntimeError, ValueError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    queued = int(((data.get("q") or {}).get("count")) or 0)
    started = int(((data.get("s") or {}).get("count")) or 0)
    results = ((data.get("ok") or {}).get("results")) or []
    last = results[0] if results else {}
    return {"ok": True, "queued": queued, "started": started,
            "maxConcurrent": max_concurrent,
            "queueDeadlock": bool(queued > 0 and started >= max_concurrent),
            "lastSuccessAt": _epoch_iso(last.get("endTime")),
            "lastSuccessJob": last.get("jobName")}


def terminate_runs(run_ids: list[str]) -> dict:
    """处置动作:终止指定 run,释放并发槽(等价于 deploy/dagster/unstick_run_queue.py,
    但不需要 docker exec)。只由 actions.request 显式调用。"""
    done, failed = [], []
    for rid in run_ids:
        q = ('mutation { terminateRun(runId: "%s") { __typename } }' % rid)
        try:
            _post(q)
            done.append(rid)
        except (urllib.error.URLError, OSError, RuntimeError, ValueError) as e:
            failed.append({"runId": rid, "error": str(e)[:120]})
    return {"terminated": done, "failed": failed}


def in_flight_run_ids(limit: int = 100) -> list[str]:
    q = ("{ runsOrError(filter: {statuses: [QUEUED, STARTED]}, limit: %d) "
         "{ ... on Runs { results { runId } } } }" % limit)
    try:
        data = _post(q)
    except (urllib.error.URLError, OSError, RuntimeError, ValueError) as e:
        log.warning("dagster in_flight query failed: %s", str(e)[:120])
        return []
    return [r["runId"] for r in ((data.get("runsOrError") or {}).get("results") or [])]
