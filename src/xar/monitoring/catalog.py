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
from dataclasses import dataclass
from datetime import datetime, timezone

from ..logging import get_logger
from .detector import DOWN, STALE, Probe

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


# ── 平台/配置 ────────────────────────────────────────────────────────────────────
def _config_coherence_hb() -> Probe:
    """同一个源的启用状态在**两处独立定义**,不一致时报异常(2026-08-02)。

    实测分歧:`x`(twitter)在 Fetchy 面板(`FETCHY_SOURCES` + kvstate `fetchy`,glmworker
    的 cadence 拉取路径)是**关**的,而 `config.daily_enabled_sources`(dagster 夜批
    `run_daily` 的清单)里**开着** —— 于是面板把 `fetchy.twitter` 判为 `unconfigured`,
    夜批却在 3 小时里拉了 600 篇。三件事互相矛盾,而没有任何信号说得出这一点。

    ⚠️ 这里**刻意不做「二选一」**:两条路径各有合理性(夜批批量 vs 日内 cadence),
    合并哪一个都是架构决定,不该由一个探针替人做。探针的职责是**把分歧本身变成可见的异常**
    —— 否则它会以「面板说没配置、数据却在进来」的形式无限期潜伏下去。
    另一面同样危险:哪天这个源真停了,面板仍会显示 unconfigured 而不是 down,**永不报警**;
    而它还是个 $20/月的付费闸,「以为关了其实在拉」是要花钱的。
    """
    try:
        from ..config import get_settings
        from ..orchestration import glm_worker as gw
        # ⚠️ 必须 strict=True:非 strict 时 fetchy_config 会**自己吞掉 DB 异常并返回
        # fetchy_defaults()**,于是探针拿一份默认配置冒充权威结论、还带着新鲜心跳戳 ——
        # 运营者刚在面板上关掉的源,这里报出的分歧完全是编造的。
        # 违反本模块头部那条「拿不到值就返回 Probe(None) → 判 unknown」。
        cfg = gw.fetchy_config(strict=True)
        nightly = {x.strip() for x in (get_settings().daily_enabled_sources or "").split(",")
                   if x.strip()}
    except Exception as e:  # noqa: BLE001 — 探针必须自兜异常
        return Probe(None, {"reason": f"配置读取失败: {type(e).__name__}"})

    # 两处命名不完全一致:fetchy 用 cadence key(twitter),夜批用源名(twitter/x)。
    _ALIAS = {"twitter": {"twitter", "x"}}
    # ⚠️ **只报一个方向**:面板上关着、夜批却开着 ——「以为关了,其实还在拉」。
    # 反方向(面板开、夜批清单里没有)是**设计如此**:夜批清单本就窄于常驻 cadence 路径,
    # 大多数源只走常驻拉取、不进夜批。把它也当分歧会一次报出 11 个源(实测),
    # 那种噪声会在几天内把人对这条告警练钝 —— 到时候真正危险的那一个也没人看。
    leaking = []
    for key, on in (cfg.get("sources") or {}).items():
        if on:
            continue                                  # 面板开着 → 不管夜批有没有,都不是危险方向
        if _ALIAS.get(key, {key}) & nightly:
            leaking.append(key)
    detail = {"checked": len(cfg.get("sources") or {}), "leaking": leaking}
    if leaking:
        detail["reason"] = (
            f"{len(leaking)} 个源在 Fetchy 面板上已关闭,但仍在夜批清单 "
            f"daily_enabled_sources 中,实际仍会被拉取:{leaking}。"
            "面板会把它判成 unconfigured(而非 down),故该源一旦真停也不会报警;"
            "若是计费源,还会持续产生费用。")
    return Probe(datetime.now(timezone.utc), detail, degrade=STALE if leaking else None)


# ── 硬件/资源(2026-08-01 补)────────────────────────────────────────────────────
# 补的是 07-31 夜那场事故:面板上 22 个任务全都只看得见**果**(qwendrain 停摆、db 慢),
# 没有一个看得见**因**。真实链条是:
#   subpool 无限额长到 14.85G → docker.slice 越过 memory.high(24G)
#   → 内核持续回收所有容器页缓存 → 人人 refault(实测磁盘读 834MB/s 全是 refault)
#   → IO 饱和 → Postgres unhealthy → qwendrain 拿不到连接 → GPU 空转 40 分钟。
# 只监控业务任务,这条链要等最下游的 qwendrain 超时才可能露头,而且报出来的是错的原因。
_HOST_CGROUP = "/host/cgroup"     # compose 只读挂载宿主 /sys/fs/cgroup;未挂则判 unknown


def _slice_mem_hb() -> Probe:
    """`docker.slice` 聚合内存水位 —— 全栈级事故的根因信号。

    ⚠️ 判据是 **current 相对 high(软闸)**,不是相对 max(硬闸)。越过 high 不会杀进程、
    `memory.pressure` 也只有个位数(回收是成功的),所以「内存看着没问题」——
    代价全部转移到 IO 上。这正是它极难被发现的原因:**没有任何内存指标会报警**。
    """
    import os
    base = os.path.join(_HOST_CGROUP, "docker.slice")
    try:
        cur = int(open(os.path.join(base, "memory.current")).read())
        high_raw = open(os.path.join(base, "memory.high")).read().strip()
        mx_raw = open(os.path.join(base, "memory.max")).read().strip()
    except OSError as e:
        return Probe(None, {"reason": f"读不到宿主 cgroup({e.__class__.__name__});"
                                      f"需在 compose 里只读挂载 /sys/fs/cgroup → {_HOST_CGROUP}"})
    high = None if high_raw == "max" else int(high_raw)
    mx = None if mx_raw == "max" else int(mx_raw)
    g = 2 ** 30
    detail = {"currentG": round(cur / g, 2),
              "highG": round(high / g, 2) if high else None,
              "maxG": round(mx / g, 2) if mx else None}
    degrade = None
    if high:
        ratio = cur / high
        detail["ratio"] = round(ratio, 3)
        if ratio >= 1.0:
            detail["reason"] = (f"聚合内存 {detail['currentG']}G 已越过软闸 {detail['highG']}G "
                                f"—— 内核正在持续回收页缓存,全栈会进入 refault 风暴")
            degrade = DOWN
        elif ratio >= 0.9:
            detail["reason"] = f"聚合内存 {detail['currentG']}G 达软闸 {detail['highG']}G 的 90%"
            degrade = STALE
    return Probe(datetime.now(timezone.utc), detail, degrade=degrade)


def _io_pressure_hb() -> Probe:
    """整机 IO 压力(PSI)。容器里也能读到**宿主全局**的 /proc/pressure/io。

    看 `avg300`(5 分钟均值)而不是 avg10:夜跑、备份这类正常重活会让瞬时值抖到很高,
    只有**持续**饱和才是问题。07-31 那晚 avg300 稳定在 98。
    """
    try:
        line = open("/proc/pressure/io").readline()      # some avg10=.. avg60=.. avg300=..
        vals = dict(kv.split("=") for kv in line.split()[1:] if "=" in kv)
        a300, a60 = float(vals.get("avg300", 0)), float(vals.get("avg60", 0))
    except (OSError, ValueError) as e:
        return Probe(None, {"reason": f"读不到 /proc/pressure/io: {e.__class__.__name__}"})
    detail = {"someAvg60": a60, "someAvg300": a300}
    degrade = None
    if a300 >= 80:
        detail["reason"] = f"IO 持续饱和(5 分钟均值 {a300:.0f}%)—— 任务会被普遍饿住"
        degrade = DOWN
    elif a300 >= 40:
        detail["reason"] = f"IO 压力偏高(5 分钟均值 {a300:.0f}%)"
        degrade = STALE
    return Probe(datetime.now(timezone.utc), detail, degrade=degrade)


# ── 接力链源(alphapai / aifinmarket)────────────────────────────────────────────
# 2026-08-01 补:这两个是**严格头部优先**的源,却一直不在 FETCHY_SOURCES 里、
# 没有 cadence 戳、面板上根本看不到 —— aifinmarket 停了 26.5 小时无人察觉,就是因为它
# 压根没被监控。注意它们与普通源的差别:它们是 `fetch_chain` 日内接力的**棒次**
# (alphapai → alphapai_backfill → gangtise → aifinmarket → alphapai_agents),
# 靠后的棒次**在没轮到时本来就该零产出**。所以产出信号必须用 `yield_needed` 门控,
# 否则「排队没轮到」会被误报成「源死了」—— 与 qwendrain 空闲不算死是同一条纪律。
def _chain_state() -> dict:
    from ..storage.kvstate import get_state
    try:
        return get_state("fetch_chain") or {}
    except Exception as e:  # noqa: BLE001 — 探针必须自兜异常
        log.warning("fetch_chain 状态读取失败: %s", str(e)[:120])
        return {}


def _chain_hb() -> Probe:
    """接力链的心跳:状态行的 updated_at。链每处理完一个 work-item 就持久化一次状态,
    所以这个戳能忠实反映「接力还在走」。注意它是**链级**信号,不是某一棒的 —— 某一棒
    有没有产出由 data_yield 单独判,两者取较坏者(检测器的双信号原则)。"""
    return kv_updated_at("fetch_chain")


def _chain_reached(name: str) -> Callable[[], bool]:
    """这一棒今天**轮到过**了吗 —— 作为 yield_needed。

    只有接力已经走到或走过这一棒,才有资格要求它拿出产出;还没轮到就要求产出,
    等于把「在排队」判成「已死亡」。`stage` 每天(Asia/Shanghai 日界)归零重排。
    """
    def needed() -> bool:
        st = _chain_state()
        order = st.get("order") or []
        if name not in order:
            return False
        try:
            return int(st.get("stage", 0) or 0) >= order.index(name)
        except (TypeError, ValueError):
            return False
    return needed


def _quota_ledger_hb() -> Probe:
    """额度账本自身是否活着(2026-08-02 审核补)。

    补的是一个**完全静默的失效面**:`quota.snapshot` 读失败会吞异常返回 {},三个写入点
    各自 try/except 只打日志,于是「表没建成 / 权限不对 / 连接持续失败」这类问题会让
    alphapai/aifinmarket 的行为 **100% 退回改造之前**(重启即失忆、双进程互不知情),
    而面板上没有任何一个任务看得见 —— 日志里只有每几秒一条 warning。
    这正是「静默哑火必须可判、可报」那条纪律要消灭的形态。

    判据取「今日有没有任何一行」:两个源全天都在调用,今日零行 = 写入链路断了。
    ⚠️ 沪日刚换日的头几分钟天然零行,所以那个窗口不判 —— 否则每天固定误报一次。
    """
    from ..storage import db
    try:
        rows = db.query(
            "SELECT count(*) AS n, coalesce(sum(calls), 0) AS calls, "
            "       count(*) FILTER (WHERE exhausted) AS exhausted_seats "
            "  FROM provider_quota "
            " WHERE cn_date = (now() AT TIME ZONE 'Asia/Shanghai')::date")
        cn_hour = db.query(
            "SELECT extract(hour from (now() AT TIME ZONE 'Asia/Shanghai')) AS h")[0]["h"]
    except Exception as e:  # noqa: BLE001
        return Probe(None, {"reason": f"账本不可读: {type(e).__name__}: {str(e)[:100]}"})
    r = rows[0] if rows else {"n": 0, "calls": 0, "exhausted_seats": 0}
    detail = {"rowsToday": int(r["n"]), "callsToday": int(r["calls"]),
              "exhaustedSeats": int(r["exhausted_seats"]), "cnHour": int(cn_hour)}
    if int(r["n"]) == 0 and int(cn_hour) >= 1:      # 换日头一小时不判(天然零行)
        detail["reason"] = ("provider_quota 今日零行 —— 额度记账链路可能已断,"
                            "alphapai/aifinmarket 会退回「重启即失忆」的旧行为")
        return Probe(datetime.now(timezone.utc), detail, degrade=STALE)
    return Probe(datetime.now(timezone.utc), detail)


# ── Dagster(只认 runs,不认 job_ticks —— 陷阱②)──────────────────────────────────
def _dagster_locations_hb() -> Probe:
    """代码位置能否加载。补的是 2026-08-01 那次**真实漏报**:06:00 夜跑零 run,
    而 daemons=ok、runs=unknown,没有任何信号说得出「今后永远不会再有 run」。
    真因是 gRPC code server 被 IO 饥饿拖死、`dagster dev` 不会重启它。
    守护线程与代码位置是两个独立的存活面,少查一个就会「全绿但什么都不会发生」。"""
    from .dagster_gql import code_locations
    r = code_locations()
    if not r.get("ok"):
        return Probe(None, {"reason": r.get("error", "graphql unreachable")})
    broken = r.get("broken") or []
    detail = {"locations": [x["name"] for x in r.get("locations", [])],
              "broken": [x["name"] for x in broken]}
    if broken:
        detail["reason"] = f"代码位置加载失败:{broken[0].get('error') or broken[0]['name']}"
    # 能答上话就说明 webserver 活着 —— 用 now 作心跳,坏与不坏交给 degrade 断言,
    # 不伪造陈旧时间戳(伪造会让 hbAgeS 变成假数据,排障时最误导)。
    return Probe(datetime.now(timezone.utc), detail, degrade=DOWN if broken else None)


def _dagster_daemons_hb() -> Probe:
    from .dagster_gql import daemon_health
    h = daemon_health()
    if not h.get("ok"):
        return Probe(None, {"reason": h.get("error", "graphql unreachable")})
    unhealthy = [d["daemonType"] for d in h["daemons"] if not d.get("healthy")]
    newest = max((_parse_ts(d.get("lastHeartbeatIso")) for d in h["daemons"]
                  if d.get("lastHeartbeatIso")), default=None)
    detail = {"daemons": [d["daemonType"] for d in h["daemons"]], "unhealthy": unhealthy}
    # 任一守护 unhealthy 立即判 down —— 用 degrade 断言,而不是伪造一个一年前的时间戳
    # (伪造会让 detail 里的 hbAgeS 变成假数据,排障时最误导人)。
    return Probe(newest, detail, degrade=DOWN if unhealthy else None)


# 部分失败的判定阈值(2026-07-30 定):夜间一次调度 = 8 个 pull_shard + 1 个 extract_all。
# 1 个失败还能靠其它分片覆盖大部分宇宙,算滞后;≥1/3 失败或一个都没成 = 这一夜废了。
_DAG_FAIL_STALE = 1        # 窗口内失败数 ≥ 此值 → 至少 stale
_DAG_FAIL_RATIO_DOWN = 1 / 3


def _dagster_runs_hb() -> Probe:
    """最近一次**真正成功**的 run + 这一夜的成败比。

    两个陷阱都在这里躲:
    ① job_ticks 在 7 天零执行期间全绿 → 只认 runs.status;
    ② **「有一个成功」不等于「跑好了」**。2026-07-30 夜里 9 个 run 死了 4 个(全部 memcg
       OOM),而监控当时显示 ok —— 因为 1.8h 前确实有过成功。所以窗口内的失败数/失败率
       必须作为独立信号参与判定,这正是双信号原则用到 dagster 自己身上。
    """
    from .dagster_gql import run_stats
    r = run_stats()
    if not r.get("ok"):
        return Probe(None, {"reason": r.get("error", "graphql unreachable")})
    # oldestInFlightH / deadlockMinAgeH 必须进 detail:金丝雀**为什么**叫或没叫,
    # 排障时要能一眼看见判据本身,否则只剩一个孤零零的布尔值,没法复核。
    detail = {"queued": r["queued"], "started": r["started"],
              "oldestInFlightH": r.get("oldestInFlightH"),
              "deadlockMinAgeH": r.get("deadlockMinAgeH"),
              "maxConcurrent": r.get("maxConcurrent"), "lastSuccessJob": r.get("lastSuccessJob"),
              "windowHours": r.get("windowHours"), "windowOk": r.get("windowOk"),
              "windowFailed": r.get("windowFailed"),
              "windowFailRatio": r.get("windowFailRatio")}
    ts = _parse_ts(r.get("lastSuccessAt"))

    if r.get("queueDeadlock"):
        detail["queueDeadlock"] = True     # 队列死锁金丝雀:in-flight 吃满并发槽且仍有排队
        return Probe(ts, detail, degrade=DOWN)

    failed, ok_n = int(r.get("windowFailed") or 0), int(r.get("windowOk") or 0)
    ratio = float(r.get("windowFailRatio") or 0.0)
    degrade = None
    if failed and ok_n == 0:
        detail["reason"] = f"本窗口 {failed} 个 run 全部失败"
        degrade = DOWN
    elif ratio >= _DAG_FAIL_RATIO_DOWN:
        detail["reason"] = f"本窗口失败率 {ratio:.0%}({failed}/{failed + ok_n})"
        degrade = DOWN
    elif failed >= _DAG_FAIL_STALE:
        detail["reason"] = f"本窗口有 {failed} 个 run 失败({failed}/{failed + ok_n})"
        degrade = STALE
    return Probe(ts, detail, degrade=degrade)


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
    bad = detail["failing"] > 3 or detail["orphanRunning"] > 0
    if bad:
        detail["reason"] = "too many failing connectors or an orphan running row"
    return Probe(_parse_ts(r["ts"]), detail, degrade=DOWN if bad else None)


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
        Task(id="platform.quota_ledger", label="quota ledger", label_cn="额度账本(付费源)",
             group="platform", severity=CRITICAL,
             heartbeat=_quota_ledger_hb, hb_sla_s=30 * 60,
             actions=("quota:clear:alphapai", "quota:clear:aifinmarket"),
             note="账本断了不会自己喊 —— 读写两侧都是 fail-open/只记日志,"
                  "断掉时两个付费源静默退回「重启即失忆」"),
        Task(id="platform.config_coherence", label="config coherence",
             label_cn="配置一致性(源开关双定义)", group="platform", severity=WARN,
             # 探针非异常路径恒返回 now() ⇒ 心跳年龄恒为 0 ⇒ 按龄判定永不触发,
             # 本任务的唯一有效信号是 degrade(读不到配置时是 Probe(None) → unknown)。
             # 这里的 SLA 只是个形式上的下限,别误以为「30 分钟没跑就会报」。
             heartbeat=_config_coherence_hb, hb_sla_s=30 * 60,
             note="同一个源的启用状态在 Fetchy 面板与夜批清单两处独立定义;"
                  "分歧本身就是异常 —— 不二选一,只把它变可见"),
        # ── 硬件/资源(2026-08-01 补):看的是**因**,不是果 ────────────────────
        Task(id="hw.docker_slice", label="docker.slice memory", label_cn="容器栈聚合内存",
             group="hardware", severity=CRITICAL,
             heartbeat=_slice_mem_hb, hb_sla_s=10 * 60,
             note="越过 memory.high 即触发全栈页缓存回收 → refault 风暴 → IO 饱和 → GPU 停摆;"
                  "内存类指标此时全部正常,只有这一条看得见"),
        Task(id="hw.io_pressure", label="io pressure", label_cn="整机 IO 压力(PSI)",
             group="hardware", severity=WARN,
             heartbeat=_io_pressure_hb, hb_sla_s=10 * 60,
             note="看 5 分钟均值:夜跑等正常重活会让瞬时值抖高,只有持续饱和才是问题"),
        # ── 接力链的两棒(2026-08-01 补;此前完全无监控)──────────────────────
        # alphapai 是第 0 棒、天天首发,48h 无产出就是真故障 ⇒ yield 无条件参与判定。
        # aifinmarket 是第 3 棒,**没轮到就该零产出** ⇒ 用 _chain_reached 门控,
        # 否则「在排队」会被误报成「已死亡」。两者共享链级心跳(状态行 updated_at)。
        Task(id="fetchy.alphapai", label="alphapai", label_cn="AlphaPai 纪要(接力第 1 棒)",
             group="fetchy", severity=CRITICAL,
             heartbeat=_chain_hb, hb_sla_s=2 * HOUR,
             data_yield=_doc_yield("alphapai"), yield_sla_s=48 * HOUR,
             note="严格头部优先源;链首发,48h 零产出即真故障"),
        Task(id="fetchy.aifinmarket", label="aifinmarket", label_cn="aifinmarket(接力第 4 棒)",
             group="fetchy", severity=WARN,
             heartbeat=_chain_hb, hb_sla_s=2 * HOUR,
             data_yield=_doc_yield("aifinmarket"), yield_sla_s=48 * HOUR,
             yield_needed=_chain_reached("aifinmarket"),
             note="链尾棒次:接力未走到时零产出属正常,故产出信号按 stage 门控"),
        Task(id="dagster.code_locations", label="dagster code location",
             label_cn="Dagster 代码位置加载", group="dagster", severity=CRITICAL,
             heartbeat=_dagster_locations_hb, hb_sla_s=10 * 60,
             note="加载失败 = 调度器手里没有可评估对象 → 零 tick、夜跑静默消失,"
                  "而守护心跳仍全绿(2026-08-01 实测漏报)"),
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
