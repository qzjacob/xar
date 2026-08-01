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

# 队列深度 + 最近一次成功 run + **最近一个调度窗内的成败计数**。
# 分段 count 查询而不是一次全量拉,避免把几百条 run 全搬过来。
# `%(after)f` 由 run_stats 填入窗口起点(epoch 秒);createdAfter 是 RunsFilter 的合法字段
# (已用 GraphQL introspection 确认)。
_Q_RUNS = """
{ instance { runQueueConfig { maxConcurrentRuns } }
  q: runsOrError(filter: {statuses: [QUEUED]}, limit: 1) { ... on Runs { count } }
  s: runsOrError(filter: {statuses: [STARTED]}, limit: 50) {
        ... on Runs { count results { startTime } } }
  ok: runsOrError(filter: {statuses: [SUCCESS]}, limit: 1) {
        ... on Runs { results { runId jobName endTime } } }
  winOk: runsOrError(filter: {statuses: [SUCCESS], createdAfter: %(after)f}, limit: 1) {
        ... on Runs { count } }
  winFail: runsOrError(filter: {statuses: [FAILURE], createdAfter: %(after)f}, limit: 1) {
        ... on Runs { count } }
  winCancel: runsOrError(filter: {statuses: [CANCELED], createdAfter: %(after)f}, limit: 1) {
        ... on Runs { count } } }
"""


def _endpoint() -> str:
    from ..config import get_settings
    return getattr(get_settings(), "dagster_graphql_url", "") or DEFAULT_URL


def _post(query: str, variables: dict | None = None) -> dict:
    """GraphQL POST。**带变量的调用一律走 `variables`,不要往 query 里拼字符串**
    (2026-07-31 审核 P3-1):即便当前的 runId 全部源自 Dagster 自身响应、可信,
    拼接也是一种「迟早会变成注入面」的写法 —— 只要哪天有调用方把外部输入传进来就成立。
    参数化的成本是零,所以没有理由留着那个形状。"""
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables
    req = urllib.request.Request(
        _endpoint(), data=json.dumps(payload).encode(),
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


def run_stats(*, max_concurrent: int | None = None, window_hours: float = 26.0) -> dict:
    """队列深度 + 最近成功 run + 最近一个调度窗内的成败计数。

    `queueDeadlock` 是 2026-07-22 那次锁死的金丝雀:in-flight 吃满并发槽且仍有排队 ——
    单看「有 run 排队」不算异常,**排队且槽位被占满**才是。

    `winFail/winOk` 是 2026-07-30 补的那个漏洞:那天夜里 9 个 run 死了 4 个,而监控显示
    `dagster.runs = ok` —— 因为它只看「距上次 SUCCESS 多久」,而确实 1.8h 前有过成功。
    「有一个成功」不等于「跑好了」。窗口默认 26h,覆盖一个夜间调度周期(cron 每日一次)。

    `max_concurrent` 默认从 **dagster 实例现读**(GraphQL 的 runQueueConfig),不写死:
    写死过 10,而 2026-07-30 把 max_concurrent_runs 调成了 7 —— 那之后 `started >= 10`
    永不成立,死锁金丝雀会静默失效。监控自己的阈值跟着被监控方的配置漂移,是这类工具
    最典型的烂法。
    """
    import time as _time
    after = _time.time() - window_hours * 3600
    try:
        data = _post(_Q_RUNS % {"after": after})
    except (urllib.error.URLError, OSError, RuntimeError, ValueError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    queued = int(((data.get("q") or {}).get("count")) or 0)
    started = int(((data.get("s") or {}).get("count")) or 0)
    # 在飞 run 里最老的那个跑了多久 —— 死锁金丝雀的**限定词**(2026-08-01 加)。
    # 没有它,`queued>0 且 started>=cap` 在**每晚夜跑刚起的那一刻**都成立
    # (6 pull + 1 extract 占满 7 槽、第二波 2 片排队),于是每天误报一次。
    # 告警每天狼来一次,真出事那次就没人信了 —— 这比漏报更难修。
    st_rows = ((data.get("s") or {}).get("results")) or []
    st_times = [float(x["startTime"]) for x in st_rows if x.get("startTime")]
    oldest_h = round((_time.time() - min(st_times)) / 3600, 2) if st_times else 0.0
    live_max = (((data.get("instance") or {}).get("runQueueConfig") or {})
                .get("maxConcurrentRuns"))
    cap = int(max_concurrent if max_concurrent is not None
              else (live_max if live_max else 10))
    results = ((data.get("ok") or {}).get("results")) or []
    last = results[0] if results else {}
    win_ok = int(((data.get("winOk") or {}).get("count")) or 0)
    win_fail = int(((data.get("winFail") or {}).get("count")) or 0)
    win_cancel = int(((data.get("winCancel") or {}).get("count")) or 0)
    total = win_ok + win_fail
    # 死锁判据 = 队列占满 **且** 最老的在飞 run 已经跑过头。
    # 阈值取自 settings(默认 6h),必须**小于** run_monitoring.max_runtime_seconds(8h)——
    # 否则回收器先动手,金丝雀永远等不到自己该叫的那一刻。
    from ..config import get_settings
    min_age_h = float(getattr(get_settings(), "monitor_deadlock_min_age_h", 6.0))
    return {"ok": True, "queued": queued, "started": started,
            "maxConcurrent": cap, "maxConcurrentSource": "live" if live_max else "fallback",
            "oldestInFlightH": oldest_h, "deadlockMinAgeH": min_age_h,
            "queueDeadlock": bool(queued > 0 and started >= cap and oldest_h >= min_age_h),
            "lastSuccessAt": _epoch_iso(last.get("endTime")),
            "lastSuccessJob": last.get("jobName"),
            "windowHours": window_hours,
            "windowOk": win_ok, "windowFailed": win_fail, "windowCanceled": win_cancel,
            "windowFailRatio": round(win_fail / total, 3) if total else 0.0}


_Q_LOCATIONS = """
{ workspaceOrError { __typename ... on Workspace { locationEntries {
      name loadStatus
      locationOrLoadError { __typename ... on PythonError { message } } } } } }
"""


def code_locations() -> dict:
    """代码位置(code location)能否加载 —— 2026-08-01 补的探针,补的是一个**真实漏报**。

    那天 06:00 UTC 的夜跑一个 run 都没创建,`job_ticks` 全空。而当时:
      · `dagster.daemons` = ok(7 个守护确实都在心跳)
      · `dagster.runs`    = unknown(窗内确实没有终态 run)
    **没有任何一个信号说得出「今后永远不会再有 run」**。真因是 dagster 的 gRPC code server
    在夜里被 IO 饥饿拖死(心跳超时 30–45s),而 `dagster dev` 不会把它拉起来:
    日志里每 50 秒刷一次 `Error loading repository location xar.orchestration.definitions`,
    调度器手里没有任何可评估的对象 → 零 tick → 夜跑静默消失。

    这就是「守护活着 ≠ 调度活着」:守护线程与代码位置是**两个独立的存活面**,
    少查一个就会出现「全绿但什么都不会发生」。任一位置加载失败 ⇒ 直接判 down。
    """
    try:
        data = _post(_Q_LOCATIONS)
    except (urllib.error.URLError, OSError, RuntimeError, ValueError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    w = data.get("workspaceOrError") or {}
    if w.get("__typename") != "Workspace":
        return {"ok": False, "error": f"workspaceOrError={w.get('__typename')}"}
    out, bad = [], []
    for e in w.get("locationEntries") or []:
        err = e.get("locationOrLoadError") or {}
        broken = err.get("__typename") not in ("RepositoryLocation",)
        row = {"name": e.get("name"), "loadStatus": e.get("loadStatus"),
               "error": (err.get("message") or "")[:200] if broken else None}
        out.append(row)
        # LOADING 是过渡态(容器刚起来那几十秒),不算故障;只有明确的加载错误才算。
        if broken and e.get("loadStatus") != "LOADING":
            bad.append(row)
    return {"ok": True, "locations": out, "broken": bad}


def terminate_runs(run_ids: list[str]) -> dict:
    """处置动作:终止指定 run,释放并发槽(等价于 deploy/dagster/unstick_run_queue.py,
    但不需要 docker exec)。只由 actions.request 显式调用。"""
    done, failed = [], []
    q = "mutation($rid: String!) { terminateRun(runId: $rid) { __typename } }"
    for rid in run_ids:
        try:
            _post(q, {"rid": rid})
            done.append(rid)
        except (urllib.error.URLError, OSError, RuntimeError, ValueError) as e:
            failed.append({"runId": rid, "error": str(e)[:120]})
    return {"terminated": done, "failed": failed}


def in_flight_run_ids(limit: int = 100) -> list[str]:
    q = ("query($n: Int!) { runsOrError(filter: {statuses: [QUEUED, STARTED]}, limit: $n) "
         "{ ... on Runs { results { runId } } } }")
    try:
        data = _post(q, {"n": int(limit)})
    except (urllib.error.URLError, OSError, RuntimeError, ValueError) as e:
        log.warning("dagster in_flight query failed: %s", str(e)[:120])
        return []
    return [r["runId"] for r in ((data.get("runsOrError") or {}).get("results") or [])]
