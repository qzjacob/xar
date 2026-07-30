"""任务注册表(code-as-truth)+ 探针实现。

加一个被监控的任务 = 在这里加一个 `Task` 条目。13 个 fetchy 源**自动生成**自
`glm_worker.FETCHY_SOURCES`,所以往那里加源即自动纳入监控,不必两处维护。

探针纪律(2026-07-29 审计教训):
- `heartbeat` 取「这个任务最近一次**动过**」的时间戳;
- `data_yield` 取「这个任务最近一次**真的产出了东西**」的时间戳。
  两者分开是因为 cadence 戳会在源死透之后继续绿着(见 detector 模块头部陷阱①)。
- 探针**只读、必须自兜异常**:任何一个探针炸掉都不该让整轮 sweep 失败,
  拿不到值就返回 `Probe(None)` → 判 unknown,由 UI 显式呈现「读不到」。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..logging import get_logger
from .detector import Probe

log = get_logger("xar.monitoring.catalog")

# 严重度:critical = 停摆推手机;warn = 只进页内告警流。
CRITICAL = "critical"
WARN = "warn"

HOUR = 3600


@dataclass(frozen=True)
class Task:
    id: str
    label: str
    label_cn: str
    group: str                       # workers | dagster | fetchy | slx | platform
    severity: str
    heartbeat: Callable[[], Probe]
    hb_sla_s: float
    down_mult: float = 3.0
    data_yield: Callable[[], Probe] | None = None
    yield_sla_s: float | None = None
    yield_needed: Callable[[], bool] | None = None
    unconfigured: Callable[[], bool] | None = None
    actions: tuple[str, ...] = ()
    note: str = ""


# ── 探针基础件 ────────────────────────────────────────────────────────────────────
def _safe(fn: Callable[[], Probe]) -> Probe:
    """探针围栏:任何异常都降级成「信号缺失」,绝不让一个坏探针带崩整轮 sweep。"""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log.warning("monitor probe failed: %s: %s", type(e).__name__, str(e)[:120])
        return Probe(None, {"probeError": f"{type(e).__name__}: {str(e)[:100]}"})


def _parse_ts(v) -> datetime | None:
    """容忍 kvstate 里混存的 ISO 字符串 / datetime / None,统一成 aware UTC。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def kv_field(key: str, *fields: str) -> Probe:
    """kvstate 某 key 的 JSON 里某个时间字段(如 counters.last_cycle_at)。"""
    from ..storage.kvstate import get_state
    st = get_state(key)
    if not st:
        return Probe(None, {"kv": key, "reason": "key absent"})
    cur = st
    for f in fields:
        if not isinstance(cur, dict):
            return Probe(None, {"kv": key, "reason": f"path {'.'.join(fields)} not a dict"})
        cur = cur.get(f)
    ts = _parse_ts(cur)
    return Probe(ts, {"kv": key, "path": ".".join(fields)})


def kv_updated_at(key: str) -> Probe:
    """`glm_worker_state.updated_at` —— 每次 save_state 都刷新,而 get_state 只读 value,
    所以这是全库最便宜、且当前**零读者**的 per-key 心跳(2026-07-29 审计发现)。"""
    from ..storage import db
    rows = db.query("SELECT updated_at FROM glm_worker_state WHERE key=%s", (key,))
    if not rows:
        return Probe(None, {"kv": key, "reason": "key absent"})
    return Probe(_parse_ts(rows[0]["updated_at"]), {"kv": key, "via": "updated_at"})


def sql_max_ts(sql: str, params: tuple = (), *, detail: dict | None = None) -> Probe:
    """一条返回单列 max(timestamp) 的只读 SQL。"""
    from ..storage import db
    rows = db.query(sql, params)
    ts = _parse_ts(rows[0].get("ts")) if rows else None
    return Probe(ts, detail or {})


def cadence_stamp(key: str) -> Probe:
    """glm_worker 的 cadence 戳 = 「上次**尝试**」。绝不可单独用来判健康(陷阱①)。"""
    return kv_field("cadence", key)


def _doc_yield(source: str) -> Callable[[], Probe]:
    def probe() -> Probe:
        return sql_max_ts(
            "SELECT max(ingested_at) AS ts FROM documents WHERE source=%s", (source,),
            detail={"table": "documents", "source": source})
    return probe


def _alt_yield(source: str) -> Callable[[], Probe]:
    def probe() -> Probe:
        return sql_max_ts(
            "SELECT max(observed_at) AS ts FROM alt_signals WHERE source=%s", (source,),
            detail={"table": "alt_signals", "source": source})
    return probe


def _llm_node_beat(*nodes: str) -> Callable[[], Probe]:
    def probe() -> Probe:
        return sql_max_ts(
            "SELECT max(created_at) AS ts FROM llm_usage WHERE node = ANY(%s)", (list(nodes),),
            detail={"table": "llm_usage", "nodes": list(nodes)})
    return probe


def _extract_backlog() -> int:
    from ..storage import db
    rows = db.query("SELECT count(*) c FROM documents "
                    "WHERE kg_extracted_at IS NULL AND permission <> 'red'")
    return int(rows[0]["c"]) if rows else 0


# ── 常驻 worker ───────────────────────────────────────────────────────────────────
def _glmworker_hb() -> Probe:
    """glmworker 心跳。`last_cycle_at` 只在 run_once **结尾**盖戳,所以一次 3.5h 的轮内卡死
    (2026-07-29 实测)看起来与真死完全一样。补丁后 run_once 开头会写 `cycle_started_at`,
    这里取两者较新者当心跳(证明进程活着),并把「开工却迟迟不收工」作为轮内卡死单独标出。"""
    from ..storage.kvstate import get_state
    c = get_state("counters")
    if not c:
        return Probe(None, {"kv": "counters", "reason": "key absent"})
    done = _parse_ts(c.get("last_cycle_at"))
    started = _parse_ts(c.get("cycle_started_at"))
    detail: dict = {"cycles": c.get("cycles"), "lastCycleAt": c.get("last_cycle_at")}
    if started and (done is None or started > done):
        detail["inCycleSinceS"] = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
        detail["hint"] = "cycle in progress"
    newest = max([t for t in (done, started) if t], default=None)
    return Probe(newest, detail)


def _qwendrain_hb() -> Probe:
    """qwendrain 自身心跳(补丁新增 qwen_drain_beat);缺失时回落到 LLM 调用痕迹 ——
    这样在镜像 rebuild 上线补丁**之前**监控就已经可用,不必等部署。"""
    p = kv_field("qwen_drain_beat", "at")
    if p.ts is not None:
        return p
    fallback = _llm_node_beat("kg_extract", "expert")()
    return Probe(fallback.ts, {**fallback.detail, "via": "llm_usage fallback (beat key absent)"})


def _subpool_hb() -> Probe:
    p = kv_field("subpool_beat", "at")
    if p.ts is not None:
        return p
    fallback = _llm_node_beat("thesis")()
    return Probe(fallback.ts, {**fallback.detail, "via": "llm_usage fallback (beat key absent)"})


def _telegram_hb() -> Probe:
    """Telegram 长轮询线程活性:线程名在不在。app 进程内检查,零 I/O。"""
    import threading
    alive = any(t.name == "chathy-telegram" and t.is_alive() for t in threading.enumerate())
    if not alive:
        return Probe(None, {"reason": "poller thread not running"})
    return Probe(datetime.now(timezone.utc), {"thread": "chathy-telegram"})


def _telegram_unconfigured() -> bool:
    from ..config import get_settings
    s = get_settings()
    return not (s.telegram_bot_token and s.enable_telegram)


def _werss_unconfigured() -> bool:
    from ..config import get_settings
    return not (getattr(get_settings(), "werss_base_url", "") or "")


def _monitor_hb() -> Probe:
    return kv_field("monitor_beat", "at")


# ── Dagster(只认 runs,不认 job_ticks —— 陷阱②)──────────────────────────────────
def _dagster_daemons_hb() -> Probe:
    from .dagster_gql import daemon_health
    h = daemon_health()
    if not h.get("ok"):
        return Probe(None, {"reason": h.get("error", "graphql unreachable")})
    unhealthy = [d["daemonType"] for d in h["daemons"] if not d.get("healthy")]
    newest = max((_parse_ts(d.get("lastHeartbeatIso")) for d in h["daemons"]
                  if d.get("lastHeartbeatIso")), default=None)
    detail = {"daemons": [d["daemonType"] for d in h["daemons"]], "unhealthy": unhealthy}
    if unhealthy:
        # 任一守护 unhealthy 立即判坏:把心跳当作「很久以前」交给 detector 判 down。
        return Probe(datetime.now(timezone.utc) - timedelta(days=365), detail)
    return Probe(newest, detail)


def _dagster_runs_hb() -> Probe:
    """最近一次**真正成功**的 run。job_ticks 在 7 天零执行期间全绿,只有 runs 说真话。"""
    from .dagster_gql import run_stats
    r = run_stats()
    if not r.get("ok"):
        return Probe(None, {"reason": r.get("error", "graphql unreachable")})
    detail = {"queued": r["queued"], "started": r["started"],
              "maxConcurrent": r.get("maxConcurrent"), "lastSuccessJob": r.get("lastSuccessJob")}
    if r.get("queueDeadlock"):
        detail["queueDeadlock"] = True     # 队列死锁金丝雀:in-flight 吃满并发槽
        return Probe(datetime.now(timezone.utc) - timedelta(days=365), detail)
    return Probe(_parse_ts(r.get("lastSuccessAt")), detail)


# ── slx 宏观连接器 ───────────────────────────────────────────────────────────────
def _slx_hb() -> Probe:
    from ..storage import db
    rows = db.query(
        "SELECT max(started_at) AS ts, "
        "  count(*) FILTER (WHERE status <> 'ok') AS bad, "
        "  count(*) FILTER (WHERE finished_at IS NULL "
        "                   AND started_at < now() - interval '12 hours') AS orphans "
        "FROM (SELECT DISTINCT ON (source_id) source_id, status, started_at, finished_at "
        "      FROM slx.audit_log ORDER BY source_id, started_at DESC) t")
    if not rows:
        return Probe(None, {"reason": "slx.audit_log unreadable"})
    r = rows[0]
    detail = {"failing": int(r["bad"] or 0), "orphanRunning": int(r["orphans"] or 0)}
    ts = _parse_ts(r["ts"])
    if detail["failing"] > 3 or detail["orphanRunning"] > 0:
        detail["reason"] = "too many failing connectors or an orphan running row"
        return Probe(datetime.now(timezone.utc) - timedelta(days=365), detail)
    return Probe(ts, detail)


# ── fetchy 源的产出探针(只列「产出可度量」的源)──────────────────────────────────
YIELD_PROBES: dict[str, tuple[Callable[[], Probe], float]] = {
    # cadence key → (产出探针, 产出 SLA 秒)。SLA 比 cadence 宽松得多:
    # 源本来就可能一整天没有新内容,判「哑火」要以天计,否则夜间必然误报。
    "wechat": (_doc_yield("wechat"), 48 * HOUR),
    "finnhub_news": (_doc_yield("finnhub"), 12 * HOUR),
    "rss": (_doc_yield("rss"), 24 * HOUR),
    "futu_news": (_doc_yield("futu"), 72 * HOUR),
    "gangtise": (_doc_yield("gangtise"), 72 * HOUR),
    "twitter": (_doc_yield("x"), 72 * HOUR),
    "wind_edb": (_alt_yield("wind_edb"), 72 * HOUR),
    "flow": (_alt_yield("flow"), 48 * HOUR),
}


def _fetchy_tasks() -> list[Task]:
    """13 个源自动生成。SLA = 2× 声明 cadence(留一轮容差),hours=None 的按 config 的
    fetch_chain 步进算。twitter 默认关 → 用 fetchy 配置判 unconfigured,不误报。"""
    from ..orchestration import glm_worker as gw
    out: list[Task] = []
    for key, meta in gw.FETCHY_SOURCES.items():
        hours = meta.get("hours")
        if hours:
            sla = hours * HOUR * 2
        else:
            from ..config import get_settings
            sla = max(2 * getattr(get_settings(), "fetch_chain_step_seconds", 300), 3600)
        yp = YIELD_PROBES.get(key)
        out.append(Task(
            id=f"fetchy.{key}", label=key, label_cn=meta.get("label", key),
            group="fetchy", severity=WARN,
            heartbeat=(lambda k=key: cadence_stamp(k)), hb_sla_s=sla,
            data_yield=(yp[0] if yp else None), yield_sla_s=(yp[1] if yp else None),
            unconfigured=(lambda k=key: not _source_enabled(k)),
            actions=(f"pull:{key}",),
            note="双信号:cadence 戳=上次尝试,数据表=上次产出" if yp else "仅心跳(产出不可度量)"))
    return out


def _source_enabled(key: str) -> bool:
    try:
        from ..orchestration import glm_worker as gw
        cfg = gw.fetchy_config()
        return bool((cfg.get("sources") or {}).get(key, True))
    except Exception:  # noqa: BLE001
        return True


# ── 注册表 ───────────────────────────────────────────────────────────────────────
def _static_tasks() -> list[Task]:
    return [
        Task(id="worker.glmworker", label="glmworker", label_cn="拉取/抽取工人",
             group="workers", severity=CRITICAL,
             heartbeat=_glmworker_hb, hb_sla_s=3 * HOUR,
             actions=("restart:glmworker",),
             note="run_once 单线程串行:拉取排第一、phanny 排最后,后段卡死会冻结下一轮拉取"),
        Task(id="worker.qwendrain", label="qwendrain", label_cn="本地 KG 抽取 drain",
             group="workers", severity=CRITICAL,
             heartbeat=_qwendrain_hb, hb_sla_s=30 * 60,
             data_yield=(lambda: sql_max_ts(
                 "SELECT max(kg_extracted_at) AS ts FROM documents",
                 detail={"table": "documents.kg_extracted_at"})),
             yield_sla_s=6 * HOUR,
             yield_needed=(lambda: _extract_backlog() > 0),
             actions=("restart:qwendrain",),
             note="队列空时产出信号不参与判定(idle≠dead)"),
        Task(id="worker.subpool", label="subpool", label_cn="云端订阅并行池",
             group="workers", severity=WARN,
             heartbeat=_subpool_hb, hb_sla_s=30 * 60,
             data_yield=(lambda: sql_max_ts(
                 "SELECT max(as_of) AS ts FROM company_thesis",
                 detail={"table": "company_thesis"})),
             yield_sla_s=48 * HOUR,
             actions=("restart:subpool",)),
        Task(id="dagster.daemons", label="dagster daemons", label_cn="Dagster 守护进程",
             group="dagster", severity=CRITICAL,
             heartbeat=_dagster_daemons_hb, hb_sla_s=10 * 60,
             note="任一守护 unhealthy 立即判 down"),
        Task(id="dagster.runs", label="dagster runs", label_cn="Dagster 夜间调度执行",
             group="dagster", severity=CRITICAL,
             heartbeat=_dagster_runs_hb, hb_sla_s=26 * HOUR,
             actions=("dagster:unstick",),
             note="只认 runs.status;job_ticks 在 7 天零执行期间全绿,不可采信"),
        Task(id="slx.connectors", label="slx macro", label_cn="宏观连接器(17 个)",
             group="slx", severity=CRITICAL,
             heartbeat=_slx_hb, hb_sla_s=48 * HOUR,
             note=">3 个连接器失败或出现 12h+ 孤儿 running 行即判 down"),
        Task(id="platform.telegram", label="telegram poller", label_cn="Telegram 长轮询",
             group="platform", severity=WARN,
             heartbeat=_telegram_hb, hb_sla_s=5 * 60,
             unconfigured=_telegram_unconfigured),
        Task(id="platform.werss", label="werss", label_cn="微信 RSS 抓取服务",
             group="platform", severity=WARN,
             heartbeat=(lambda: _doc_yield("wechat")()), hb_sla_s=72 * HOUR,
             unconfigured=_werss_unconfigured,
             note="外部容器无内部心跳,以 wechat 文档产出代理"),
        Task(id="platform.monitor", label="monitor sweep", label_cn="监控自身",
             group="platform", severity=WARN,
             heartbeat=_monitor_hb, hb_sla_s=10 * 60,
             note="谁监控监控者:页面显示此心跳;进程级兜底由 deploy/monitor/deadman.sh 承担"),
    ]


def all_tasks() -> list[Task]:
    return _static_tasks() + _fetchy_tasks()


def by_id() -> dict[str, Task]:
    return {t.id: t for t in all_tasks()}


def probe(task: Task) -> tuple[Probe, Probe | None, bool]:
    """跑完一个任务的全部探针。返回 (心跳, 产出|None, 产出是否该参与判定)。"""
    hb = _safe(task.heartbeat)
    yld = _safe(task.data_yield) if task.data_yield else None
    needed = True
    if task.yield_needed is not None:
        try:
            needed = bool(task.yield_needed())
        except Exception:  # noqa: BLE001
            needed = True
    return hb, yld, needed


def is_unconfigured(task: Task) -> bool:
    if task.unconfigured is None:
        return False
    try:
        return bool(task.unconfigured())
    except Exception:  # noqa: BLE001
        return False
