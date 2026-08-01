"""任务健康判定 —— **纯函数,零 I/O**(时钟由调用方注入,便于 fake-clock 单测)。

判定语义刻意绕开 2026-07-29 审计发现的三个陷阱:

① **「尝试过」≠「有产出」**。glm_worker 的 `_stamp` 在 `fn()` 不抛异常时就盖绿戳,
   于是 wechat/futu/gangtise 在静默哑火 6.5/24/4 天后 cadence 戳**至今仍是绿的**。
   所以本模块支持**双信号**:心跳(attempt)与产出(yield)各自判态,取**较坏**者。
   只看心跳的检测器会精确复现当初那场 7 天无人察觉。

② **「有 running 行」≠「在跑」**。ingest_runs 有 87 行永久 running;dagster 的
   job_ticks 在 7 天零执行期间全绿。所以探针必须取「真实产出的时间戳」,
   而不是「有没有一条声称在跑的记录」—— 这个纪律在 catalog 的探针里落实。

③ **「状态只在变化时写」⇒ 缺失是第三态**。quota/sub_quota/fetchy 等 key 只在状态跃迁时
   才 save_state,所以「行不存在」既可能是「健康且安静」也可能是「从未跑过」。
   `Probe(ts=None)` 一律判 `unknown`,**绝不等同于 down** —— 否则监控上线第一天就会
   对着一堆从未初始化的 key 狂发报警,然后被永久静音(告警疲劳即监控之死)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

OK = "ok"
STALE = "stale"
DOWN = "down"
UNKNOWN = "unknown"
UNCONFIGURED = "unconfigured"

# 严重度序:用于「取较坏者」与「是否恶化」。unconfigured 与 ok 同级 —— 没配的东西不算坏,
# 它只是不参与判定(werss 未配 URL、telegram 未配 token 都属此类)。
RANK: dict[str, int] = {OK: 0, UNCONFIGURED: 0, UNKNOWN: 1, STALE: 2, DOWN: 3}


@dataclass(frozen=True)
class Probe:
    """一次探测结果。ts=None 表示**信号缺失**(第三态),不是「很旧」。

    `degrade`:探针可以断言「不管时间戳多新,状态至少坏到这一档」。给的是那些**不是
    时间新鲜度**的坏消息:dagster 守护 unhealthy、队列死锁、部分 run 失败、连接器批量报错。
    没有它就只能靠「把时间戳伪造成一年前」来逼出 down —— 那样 detail 里的 hbAgeS 会是假的
    (排障时最误导人),而且只能表达 down、无法表达「部分失败 = stale」这种中间档。
    """
    ts: datetime | None = None
    detail: dict = field(default_factory=dict)
    degrade: str | None = None


def _age_s(ts: datetime, now: datetime) -> float:
    return (now - ts).total_seconds()


def _by_age(age: float, sla: float, down_mult: float) -> str:
    if age > sla * down_mult:
        return DOWN
    if age > sla:
        return STALE
    return OK


def worse(a: str, b: str) -> str:
    return a if RANK.get(a, 0) >= RANK.get(b, 0) else b


def evaluate(*, now: datetime,
             hb: Probe,
             hb_sla_s: float,
             down_mult: float = 3.0,
             yld: Probe | None = None,
             yield_sla_s: float | None = None,
             yield_needed: bool = True,
             unconfigured: bool = False) -> tuple[str, dict]:
    """判定单个任务的状态,返回 (state, detail)。

    `yield_needed=False` = 「此刻本来就没活可干」(如 qwendrain 队列已清空),
    产出信号**不参与判定** —— 否则空闲会被误报成死亡,这是 idle≠dead 的关键区分。
    """
    detail: dict = {"hb": hb.detail, "hbSlaS": hb_sla_s}

    if unconfigured:
        return UNCONFIGURED, {**detail, "reason": "unconfigured"}

    if hb.ts is None:
        # 信号缺失 ≠ 停摆。可能从未初始化(只在变化时写的 key),也可能表/端点不可读。
        # ⚠️ 但**显式的坏消息断言优先于「没有时间戳」**(2026-08-01 补):
        # 探针可能既拿不到「上次成功」的戳、又明确知道「这一窗全失败了」——
        # 迁 dagster 存储到 Postgres 后正是这个形态:新库里从未有过 SUCCESS ⇒ ts 恒为 None,
        # 于是哪怕每一个 run 都失败,这里也只会报 unknown,**永远不会翻 down**。
        # 「没数据」和「有数据且是坏的」是两回事;后者必须压过前者,否则一个
        # 永久失败的调度可以无限期躲在 unknown 后面(告警一次都不会发)。
        if hb.degrade:
            return hb.degrade, {**detail, "reason": (hb.detail or {}).get("reason")
                                or "no heartbeat signal, but probe asserts failure",
                                "worstBy": "signal"}
        return UNKNOWN, {**detail, "reason": "no heartbeat signal"}

    hb_age = _age_s(hb.ts, now)
    state = _by_age(hb_age, hb_sla_s, down_mult)
    detail["hbAgeS"] = round(hb_age, 1)
    detail["hbAt"] = hb.ts.isoformat()
    if hb.degrade:
        # 非新鲜度类的坏消息(守护 unhealthy / 队列死锁 / 部分失败 …)
        if RANK.get(hb.degrade, 0) > RANK[state]:
            detail["worstBy"] = "signal"
        state = worse(state, hb.degrade)

    if yld is not None and yield_sla_s:
        detail["yieldSlaS"] = yield_sla_s
        detail["yieldNeeded"] = yield_needed
        if yld.detail:
            detail["yield"] = yld.detail
        if not yield_needed:
            detail["yieldSkipped"] = "no pending work"
        elif yld.ts is None:
            # 有心跳、却从来没有任何产出:值得关注但无法量化「多久没产出」。
            state = worse(state, STALE)
            detail["yieldReason"] = "no yield signal ever"
        else:
            y_age = _age_s(yld.ts, now)
            detail["yieldAgeS"] = round(y_age, 1)
            detail["yieldAt"] = yld.ts.isoformat()
            # 产出用固定 2× 而非 down_mult:产出 SLA 本就宽松(按天算),
            # 再乘 3 会把「一周没产出」也判成 stale,失去意义。
            y_state = _by_age(y_age, yield_sla_s, 2.0)
            if yld.degrade:
                y_state = worse(y_state, yld.degrade)
            if RANK[y_state] > RANK[state]:
                detail["worstBy"] = "yield"    # 心跳绿但产出坏 = 陷阱①,显式标出
            state = worse(state, y_state)

    return state, detail


# ── 跃迁确认(抗抖动)────────────────────────────────────────────────────────────────
def confirm(prev: dict | None, observed: str, *, now: datetime,
            needed: int = 2) -> tuple[str, bool, dict]:
    """把「本轮观测」并入持久状态,返回 (生效状态, 是否发生跃迁, 新的持久状态)。

    **恶化需连续 `needed` 轮观测确认,恢复立即生效**。理由不对称是刻意的:
    误报一次会训练人忽略报警(比漏报更致命),而漏一轮恢复只是晚 2 分钟变绿。
    `unknown` 不需要确认 —— 它本身就是「不知道」,再等两轮也不会更知道。

    prev/返回的持久状态形状:{state, since, pending_state, pending_count}
    (存 kvstate,进程重启后语义不丢)。
    """
    cur = (prev or {}).get("state")
    if cur is None:                                  # 首次见到这个任务:直接采信
        return observed, True, {"state": observed, "since": now.isoformat(),
                                "pending_state": None, "pending_count": 0}
    if observed == cur:
        return cur, False, {**prev, "pending_state": None, "pending_count": 0}

    improving = RANK.get(observed, 0) < RANK.get(cur, 0)
    if improving or observed == UNKNOWN or needed <= 1:
        return observed, True, {"state": observed, "since": now.isoformat(),
                                "pending_state": None, "pending_count": 0}

    # 恶化:累计连续同向观测
    n = int((prev or {}).get("pending_count", 0)) + 1 if prev.get("pending_state") == observed else 1
    if n >= needed:
        return observed, True, {"state": observed, "since": now.isoformat(),
                                "pending_state": None, "pending_count": 0}
    return cur, False, {**prev, "pending_state": observed, "pending_count": n}
