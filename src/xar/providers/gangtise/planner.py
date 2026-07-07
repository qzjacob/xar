"""Gangtise 非标语义抓取规划器:核心公司优先 + 最新优先 + 自适应历史回填。

- cn_priority_order():CN 名单按(种子∩CN → 覆盖度 composite → 注册表序)排序;
- fresh_sweep():每日——security_clue 变更雷达 + 全局研报/纪要日期窗扫 + 核心公司新季度
  MD&A + 零 LLM 评级第二遍 → 推每 doc_type 水位线(kvstate 'gangtise_crawl');
- backfill_step():(doc_type, 月窗) 单元最新月先行向旧行走;**连续 2 空窗盖 exhausted 戳**
  (对付试用账户 ~1 月历史上限);MD&A 走 reportDate 季度序(不受此限);毒单元 retry-once-then-skip;
  游标每单元后落 kvstate 'gangtise_backfill'(crash-safe),复刻 ingestion/history.py 语义。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ...config import get_settings
from ...logging import get_logger
from ...ontology import coverage360
from ...ontology.debates import seed_company_ids
from ...storage import kvstate
from . import insight

log = get_logger("xar.gangtise.planner")

# 回填覆盖的 list doc_type(MD&A 单独走季度序)。
_BACKFILL_DOCTYPES = ("broker_report", "meeting_minutes")
_EMPTY_STOP = 2                          # 连续空窗数 → 判定账户可见深度到底


def _is_cn(c: dict) -> bool:
    return c.get("region") == "CN" or any(
        str(t).endswith((".SS", ".SH", ".SZ", ".BJ")) for t in (c.get("tickers") or []))


def cn_priority_order() -> list[str]:
    from ...ingestion.registry import COMPANIES
    cn = [c["id"] for c in COMPANIES if _is_cn(c)]
    seeds = seed_company_ids()
    try:
        cov = coverage360.coverage_all()
    except Exception:  # noqa: BLE001 —— 覆盖度不可用时退化为(种子→注册表序)
        cov = {}

    def key(cid: str):
        return (cid not in seeds, -float((cov.get(cid) or {}).get("composite", 0.0)))
    return sorted(cn, key=key)


def core_list() -> list[str]:
    """核心公司(优先序,确定性)——前 N 名 + 未入前 N 的 CN 种子旗舰(仍按优先序)。"""
    order = cn_priority_order()
    n = get_settings().gangtise_core_size
    top = order[:n]
    seen = set(top)
    extra = [c for c in order if c in seed_company_ids() and c not in seen]
    return top + extra


def core_set() -> set[str]:
    return set(core_list())


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


# ── 每日增量 ─────────────────────────────────────────────────────────────────────
def fresh_sweep() -> dict:
    if not insight.client.available():
        return {"skipped": "gangtise disabled"}
    s = get_settings()
    out: dict = {}
    start_ms, end_ms = insight.default_window(days=3)
    # ① 变更雷达(不落库)—— 记摘要供审计对账
    try:
        clue = insight.pull_clues(start_ms=start_ms, end_ms=end_ms)
        st = kvstate.get_state("gangtise_clue_state")
        st["last"] = {"at": end_ms, "counts": clue["counts"], "targets": len(clue["targets"])}
        kvstate.save_state("gangtise_clue_state", st)
        out["clues"] = {"targets": len(clue["targets"]), "counts": clue["counts"]}
    except Exception as e:  # noqa: BLE001
        out["clues"] = {"error": str(e)[:120]}
    # ② 全局研报 + 纪要日期窗扫
    out["broker"] = insight.pull_broker_reports(start_ms=start_ms, end_ms=end_ms,
                                                max_pages=s.gangtise_insight_pages)
    out["minutes"] = insight.pull_minutes(start_ms=start_ms, end_ms=end_ms,
                                          max_pages=s.gangtise_insight_pages)
    # ③ 核心公司最新季度 MD&A
    md = 0
    latest_q = insight._quarter_ends(1)[0] if insight._quarter_ends(1) else None
    if latest_q:
        for cid in core_list():
            try:
                md += insight.pull_mgmt_discussion(cid, latest_q)
            except Exception as e:  # noqa: BLE001
                log.warning("mgmt_discussion %s failed: %s", cid, e)
    out["mgmt_discussion"] = md
    # ④ 零 LLM 评级第二遍
    out["ratings"] = insight.parse_broker_ratings()
    # ⑤ 推水位线
    cr = kvstate.get_state("gangtise_crawl")
    cr["last_fresh_at"] = end_ms
    kvstate.save_state("gangtise_crawl", cr)
    log.info("gangtise fresh_sweep: %s", {k: v for k, v in out.items() if k != "clues"})
    return out


# ── 历史回填(自适应深度)────────────────────────────────────────────────────────
def _month_window(months_back: int) -> tuple[int, int]:
    end = date.today().replace(day=1) - timedelta(days=1)          # 上月末
    # 向前推 months_back 个月得到窗口起点
    y, m = end.year, end.month
    for _ in range(months_back):
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    start = date(y, m, 1)
    win_end = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y, 12, 31)
    to_ms = lambda d: int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
    return to_ms(start), to_ms(win_end)


def backfill_step(units: int = 2) -> dict:
    if not insight.client.available():
        return {"skipped": "gangtise disabled"}
    st = kvstate.get_state("gangtise_backfill")
    # 偏移 0 = 上一个完整月(_month_window(0));从 0 起,否则最近的完整月永远扫不到(评审 #1/#7/#8)。
    st.setdefault("months", {dt: 0 for dt in _BACKFILL_DOCTYPES})
    st.setdefault("empty", {dt: 0 for dt in _BACKFILL_DOCTYPES})
    st.setdefault("exhausted", {})
    st.setdefault("mdq", 0)                                          # MD&A 季度游标
    s = get_settings()
    done = {"list_units": 0, "md_units": 0}
    active = [dt for dt in _BACKFILL_DOCTYPES if not st["exhausted"].get(dt)]
    for _ in range(units):
        if not active:
            break
        dt = active[done["list_units"] % len(active)]
        mo = st["months"][dt]
        if mo > s.gangtise_history_months:                          # 到目标深度
            st["exhausted"][dt] = "depth"
            active = [x for x in active if not st["exhausted"].get(x)]
            continue
        start_ms, end_ms = _month_window(mo)
        pull = insight.pull_broker_reports if dt == "broker_report" else insight.pull_minutes
        errored = False
        try:
            r = pull(start_ms=start_ms, end_ms=end_ms, max_pages=s.gangtise_insight_pages)
            got = r.get("saved", 0)
        except Exception as e:  # noqa: BLE001 —— 毒单元:记一次重试计数,不炸
            st.setdefault("retries", {})[f"{dt}:{mo}"] = st.get("retries", {}).get(f"{dt}:{mo}", 0) + 1
            got, errored = 0, True
            log.warning("backfill %s m-%d failed: %s", dt, mo, e)
        st["months"][dt] = mo + 1
        # 空窗判定只对**真实空结果**计数;瞬时错误不算空窗(评审 #5:否则 API 抖动会误判耗尽)。
        if errored:
            pass
        elif got == 0:
            st["empty"][dt] = st["empty"].get(dt, 0) + 1
            if st["empty"][dt] >= _EMPTY_STOP:                      # 连续空窗 → 账户可见深度到底
                st["exhausted"][dt] = "empty_window"
                active = [x for x in active if not st["exhausted"].get(x)]
        else:
            st["empty"][dt] = 0
        done["list_units"] += 1
        kvstate.save_state("gangtise_backfill", st)                 # crash-safe:每单元后落
    # MD&A 季度回填(不受账户历史窗限制),对核心公司走
    q_all = insight._quarter_ends(s.gangtise_history_quarters)
    qi = st["mdq"]
    if qi < len(q_all):
        rd = q_all[qi]
        for cid in core_list():
            try:
                insight.pull_mgmt_discussion(cid, rd)
            except Exception as e:  # noqa: BLE001
                log.warning("md backfill %s %s: %s", cid, rd, e)
        st["mdq"] = qi + 1
        done["md_units"] = 1
        kvstate.save_state("gangtise_backfill", st)
    return done


def backfill_status() -> dict:
    st = kvstate.get_state("gangtise_backfill")
    return {"months": st.get("months"), "exhausted": st.get("exhausted"),
            "mdq": st.get("mdq"), "empty": st.get("empty")}


def reset_backfill() -> None:
    kvstate.save_state("gangtise_backfill", {})
