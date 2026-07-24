"""另类语义抓取链:相关性 × 额度紧迫度的日内接力调度。

用户诉求:把 **AlphaPai 纪要抓取设为每天持续固定任务**(相关性高→低、新→旧),直到当日额度
耗尽(203/204),然后 **fallback → Gangtise**,再 **fallback → aifinmarket**,并预留未来源。

实现 = 一个**日内接力状态机**(状态存 kvstate `fetch_chain`),由 glm_worker 的 `alt_fetch_chain`
站点每 `fetch_chain_step_seconds` 驱动一步 `step()`;每步消耗一个 `fetch_chain_slice_seconds` 时间片
(item 之间检查预算,不抢占单个慢调用),不阻塞 worker 主循环。**进位条件 = 源当日额度耗尽
OR 当日清单跑完**(后者保证 alphapai 额度充裕的日子里 gangtise/aifinmarket 也不被饿死)。

链序(`fetch_chain_order` CSV,可配置、未来源追加):
  1. alphapai        —— 纪要 recall(roadShow*)逐公司(相关性序)→ 主题 recall → 头部公司其余类型
                        耗尽:alphapai.quota_exhausted()(203 当日;204 退避、3 连击弃权进位)
  2. gangtise        —— clues → 纪要全局窗扫 → core 分块券商研报 → MD&A → 评级(零信用;无额度信号)
                        耗尽:清单跑完即进位
  3. aifinmarket     —— 公司维(相关性序分块)→ 全局维(行业/策略/宏观,一次)
                        耗尽:aifinmarket.all_seats_exhausted()
  4. alphapai_agents —— 头部公司 agent 一页纸/投资逻辑(SSE 慢,放链尾;203 已触发则秒跳过)

日界 = Asia/Shanghai(alphapai 是国内厂商,UTC 日界会让额度刷新后闲置至多 8h)。相关性排序在
当日开盘定序并 pin 进状态(`pinned_ids`,避免 coverage 分数日内漂移导致跳/重公司)。
每处理一个 work-item 就持久化状态(崩溃精确续传)。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from ..config import get_settings
from ..logging import get_logger
from ..storage.kvstate import get_state, save_state

log = get_logger("xar.fetch_chain")

STATE_KEY = "fetch_chain"
_CN_TZ = ZoneInfo("Asia/Shanghai")
_B204_STRIKES = 3               # 连续 204 退避片数 → 放弃 alphapai 段(病态供应商不能拖死整天)
_STAGE_LOG_CAP = 40


def _cn_today() -> str:
    return datetime.now(_CN_TZ).date().isoformat()


def _cn_now_iso() -> str:
    return datetime.now(_CN_TZ).isoformat(timespec="seconds")


def _safe(fn: Callable[[], bool], default: bool) -> bool:
    """谓词求值容错(available/exhausted/backing_off 可能读 DB)——失败降级为 default,不炸链。"""
    try:
        return bool(fn())
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_chain predicate failed: %s", str(e)[:120])
        return default


# ── 相关性排序(种子辩题公司 → coverage 综合分降序 → 注册表序)────────────────────
def universe_priority_order() -> list[str]:
    """全宇宙按(种子辩题旗舰优先 → coverage360 综合分降序 → 注册表序)排序。
    与 gangtise.planner.cn_priority_order 同排序键但不过滤 CN(稳定排序保留同分注册表序)。"""
    from ..ingestion.registry import COMPANIES
    from ..ontology import coverage360
    from ..ontology.debates import seed_company_ids

    ids = [c["id"] for c in COMPANIES]
    seeds = seed_company_ids()
    try:
        cov = coverage360.coverage_all()
    except Exception:  # noqa: BLE001 — 覆盖度不可用时退化为(种子→注册表序)
        cov = {}

    def key(cid: str):
        return (cid not in seeds, -float((cov.get(cid) or {}).get("composite", 0.0)))
    return sorted(ids, key=key)


# ── 抓取阶段(provider 之间互不感知,编排层组合)──────────────────────────────────
@dataclass(frozen=True)
class Stage:
    name: str
    available: Callable[[], bool]
    build_worklist: Callable[[dict], list]   # state → JSON 可序列化 work-item 列表(确定性)
    run_item: Callable[[list, dict], int]    # (item, state) → 落库文档数
    exhausted: Callable[[], bool] = (lambda: False)      # 源当日额度耗尽
    backing_off: Callable[[], bool] = (lambda: False)    # 源短退避中(暂停不进位)


# --- alphapai(纪要首要 + 主题 + 头部其余类型)---
def _alphapai_companies(st: dict) -> list[str]:
    from ..providers import alphapai
    return [cid for cid in st.get("pinned_ids", []) if alphapai.has_cjk_name(cid)]


def _alphapai_worklist(st: dict) -> list:
    from ..ingestion.registry import THEMES
    s = get_settings()
    companies = _alphapai_companies(st)
    items: list = [["minutes", cid] for cid in companies]                    # ① 纪要(相关性序)
    items += [["theme", (THEMES[t].get("nameCn") or t)] for t in THEMES]     # ② 主题
    top = s.fetch_chain_alphapai_rest_top                                     # ③ 其余类型(0=全库,尽用额度)
    rest = companies if top <= 0 else companies[:top]
    items += [["rest", cid] for cid in rest]
    return items


def _alphapai_run(item: list, st: dict) -> int:
    from ..providers import alphapai
    kind = item[0]
    if kind == "minutes":
        return alphapai.pull_minutes(item[1], start=st.get("alphapai_start"))
    if kind == "theme":
        return alphapai.pull_theme(item[1])
    if kind == "rest":
        return alphapai.pull_company(item[1])
    return 0


def _alphapai_stage() -> Stage:
    from ..providers import alphapai
    return Stage(name="alphapai",
                 available=lambda: alphapai.available() and get_settings().enable_alphapai,
                 build_worklist=_alphapai_worklist, run_item=_alphapai_run,
                 exhausted=alphapai.quota_exhausted, backing_off=alphapai.quota_backing_off)


# --- alphapai_agents(SSE 合成,链尾)---
def _agents_worklist(st: dict) -> list:
    from ..providers import alphapai
    s = get_settings()
    modes = [int(m) for m in (s.alphapai_agent_modes or "").split(",") if m.strip().isdigit()]
    cn = [cid for cid in st.get("pinned_ids", []) if alphapai._cn_stock(cid)]
    cn = cn[: s.fetch_chain_agent_companies]
    return [["agent", cid, m] for cid in cn for m in modes]


def _agents_run(item: list, st: dict) -> int:
    from ..providers import alphapai
    return alphapai.pull_agent(item[1], item[2])


def _agents_stage() -> Stage:
    from ..providers import alphapai
    return Stage(name="alphapai_agents",
                 available=lambda: alphapai.available() and get_settings().enable_alphapai,
                 build_worklist=_agents_worklist, run_item=_agents_run,
                 exhausted=alphapai.quota_exhausted, backing_off=alphapai.quota_backing_off)


# --- gangtise(clues → 纪要 → 券商研报 → MD&A → 评级;零信用,无额度信号)---
def _gangtise_core(st: dict) -> list[str]:
    """gangtise 核心公司(CN,pinned 序 → 等价 cn_priority_order)前 N + CN 种子旗舰。"""
    from ..ingestion.registry import company_by_id
    from ..ontology.debates import seed_company_ids
    from ..providers.gangtise.planner import _is_cn

    cn = [cid for cid in st.get("pinned_ids", []) if _is_cn(company_by_id(cid) or {})]
    n = get_settings().gangtise_core_size
    core = cn[:n]
    seen = set(core)
    seeds = seed_company_ids()
    core += [cid for cid in cn if cid in seeds and cid not in seen]
    return core


def _gangtise_worklist(st: dict) -> list:
    core = _gangtise_core(st)
    chunk = max(1, get_settings().fetch_chain_gangtise_chunk)
    nchunks = (len(core) + chunk - 1) // chunk
    items: list = [["gts_clues"], ["gts_minutes"]]
    items += [["gts_broker", i] for i in range(nchunks)]
    items += [["gts_mdna", i] for i in range(nchunks)]
    items += [["gts_ratings"]]
    return items


def _gangtise_run(item: list, st: dict) -> int:
    from ..providers.gangtise import insight
    from ..storage import kvstate

    kind = item[0]
    start_ms, end_ms = insight.default_window(days=3)
    if kind == "gts_clues":                                  # 变更雷达(不落库)+ 记摘要
        clue = insight.pull_clues(start_ms=start_ms, end_ms=end_ms)
        stt = kvstate.get_state("gangtise_clue_state")
        stt["last"] = {"at": end_ms, "counts": clue["counts"], "targets": len(clue["targets"])}
        kvstate.save_state("gangtise_clue_state", stt)
        return len(clue["targets"])
    if kind == "gts_minutes":                                # 纪要全局日期窗扫
        pages = get_settings().gangtise_insight_pages
        return insight.pull_minutes(start_ms=start_ms, end_ms=end_ms, max_pages=pages).get("saved", 0)
    if kind in ("gts_broker", "gts_mdna"):
        core = _gangtise_core(st)
        chunk = max(1, get_settings().fetch_chain_gangtise_chunk)
        cids = core[item[1] * chunk:(item[1] + 1) * chunk]
        if kind == "gts_broker":                             # 券商研报按公司(真机须 keyword 过滤)
            pages = get_settings().gangtise_insight_pages
            return insight.pull_broker_reports_for(cids, start_ms=start_ms, end_ms=end_ms,
                                                   max_pages=pages).get("saved", 0)
        q = insight._quarter_ends(1)                         # 最新季度 MD&A
        if not q:
            return 0
        n = 0
        for cid in cids:
            try:
                n += insight.pull_mgmt_discussion(cid, q[0])
            except Exception as e:  # noqa: BLE001
                log.warning("gts mdna %s: %s", cid, str(e)[:120])
        return n
    if kind == "gts_ratings":                                # 零 LLM 评级第二遍 + 推水位线
        r = insight.parse_broker_ratings()
        cr = kvstate.get_state("gangtise_crawl")
        cr["last_fresh_at"] = end_ms
        kvstate.save_state("gangtise_crawl", cr)
        return int(r.get("companies_days", 0))
    return 0


def _gangtise_stage() -> Stage:
    from ..providers import gangtise
    return Stage(name="gangtise", available=gangtise.available,
                 build_worklist=_gangtise_worklist, run_item=_gangtise_run,
                 exhausted=(lambda: False))          # 无额度信号 → 清单跑完即进位


# --- aifinmarket(公司维相关性序分块 → 全局维一次)---
def _aifin_worklist(st: dict) -> list:
    ids = st.get("pinned_ids", [])
    chunk = max(1, get_settings().fetch_chain_aifin_chunk)
    nchunks = (len(ids) + chunk - 1) // chunk
    return [["company", i] for i in range(nchunks)] + [["global"]]


def _aifin_run(item: list, st: dict) -> int:
    from ..providers import aifinmarket
    if item[0] == "company":
        ids = st.get("pinned_ids", [])
        chunk = max(1, get_settings().fetch_chain_aifin_chunk)
        return sum(aifinmarket.pull_company_research(cid)
                   for cid in ids[item[1] * chunk:(item[1] + 1) * chunk])
    if item[0] == "global":
        return sum(int(v) for v in aifinmarket.pull_global_research().values())
    return 0


def _aifin_stage() -> Stage:
    from ..providers import aifinmarket
    return Stage(name="aifinmarket", available=aifinmarket.available,
                 build_worklist=_aifin_worklist, run_item=_aifin_run,
                 exhausted=aifinmarket.all_seats_exhausted)


def stages() -> dict[str, Stage]:
    """内置阶段注册表。未来新增源 = 加一项 + 在 fetch_chain_order 追加名字。"""
    return {"alphapai": _alphapai_stage(), "gangtise": _gangtise_stage(),
            "aifinmarket": _aifin_stage(), "alphapai_agents": _agents_stage()}


def _resolved_order() -> list[str]:
    reg = stages()
    csv = (get_settings().fetch_chain_order or "").strip()
    order = [x.strip() for x in csv.split(",") if x.strip() and x.strip() in reg]
    return order or list(reg)


# ── 状态机 ──────────────────────────────────────────────────────────────────────
def _load_state() -> dict:
    """读日状态;沪日滚动(或 order 配置变更)→ 重挂新的一天(定序 + pin + 计数清零)。"""
    raw = get_state(STATE_KEY)
    today = _cn_today()
    order = _resolved_order()
    if raw.get("date") == today and raw.get("order") == order:
        return raw
    s = get_settings()
    never = raw.get("last_done_date") is None            # 从未完成过一整天 → 首轮 30d 回看
    lookback = s.alphapai_lookback_days if never else s.fetch_chain_refetch_days
    start = (datetime.fromisoformat(today).date() - timedelta(days=lookback)).isoformat()
    st = {"date": today, "stage": 0, "cursor": 0, "b204": 0, "order": order,
          "pinned_ids": universe_priority_order(), "alphapai_start": start,
          "last_done_date": raw.get("last_done_date"), "passes": 1, "done_at_epoch": 0.0,
          "counts": {name: {} for name in order}, "stage_log": [], "done": False}
    save_state(STATE_KEY, st)
    return st


def _new_pass(st: dict) -> dict:
    """整轮跑完后开新一轮(日内滚动重跑):保留当日 pinned 相关性序,重置阶段/游标/计数,
    刷新 recall 窗口到近端(捕捉白天新发布的纪要)。alphapai 若已 203 耗尽,新轮里其 exhausted()
    仍为真 → 秒跳过,不浪费调用。"""
    lookback = get_settings().fetch_chain_refetch_days
    st = {**st, "stage": 0, "cursor": 0, "b204": 0, "done": False,
          "passes": int(st.get("passes", 1)) + 1,
          "alphapai_start": (datetime.fromisoformat(st["date"]).date()
                             - timedelta(days=lookback)).isoformat(),
          "counts": {name: {} for name in st["order"]}, "stage_log": []}
    save_state(STATE_KEY, st)
    return st


def _advance(st: dict, sname: str, ended: str) -> None:
    st["stage_log"] = (st.get("stage_log") or [])[-(_STAGE_LOG_CAP - 1):] + [
        {"stage": sname, "ended": ended, "at": _cn_now_iso()}]
    st["stage"] = int(st["stage"]) + 1
    st["cursor"] = 0
    st["b204"] = 0


def _merge_count(st: dict, sname: str, item: list, n: int) -> None:
    c = st["counts"].setdefault(sname, {})
    c[item[0]] = int(c.get(item[0], 0)) + int(n or 0)


def step(*, budget_seconds: float | None = None) -> dict:
    """站点入口:消耗一个时间片,推进接力状态机。never raise(返回 error/状态 dict)。"""
    if not get_settings().fetch_chain_enabled:
        return {"skipped": "fetch_chain disabled"}
    st = _load_state()
    if st.get("done"):
        # 日内滚动重跑:整条链跑完 → 冷却期内空转;冷却到期(且开启)→ 重开一轮抓白天新内容。
        repoll = get_settings().fetch_chain_repoll_seconds
        if repoll <= 0 or time.time() - float(st.get("done_at_epoch", 0)) < repoll:
            return {"idle": st["date"], "passes": st.get("passes", 1),
                    "counts": st.get("counts", {})}
        st = _new_pass(st)
    reg = stages()
    order = st["order"]
    budget = budget_seconds if budget_seconds is not None else get_settings().fetch_chain_slice_seconds
    t0 = time.monotonic()
    ran = 0
    advanced: list = []
    while time.monotonic() - t0 < budget:
        if int(st["stage"]) >= len(order):
            st["done"] = True
            st["done_at_epoch"] = time.time()            # 供日内滚动重跑冷却计时
            st["last_done_date"] = st["date"]
            save_state(STATE_KEY, st)
            break
        sname = order[int(st["stage"])]
        stage = reg.get(sname)
        if stage is None or not _safe(stage.available, False):
            _advance(st, sname, "unavailable")
            advanced.append({"stage": sname, "ended": "unavailable"})
            save_state(STATE_KEY, st)
            continue
        if _safe(stage.backing_off, False):                  # 204 退避:暂停不进位(3 连击弃权)
            st["b204"] = int(st.get("b204", 0)) + 1
            if st["b204"] >= _B204_STRIKES:
                _advance(st, sname, "backoff_giveup")
                advanced.append({"stage": sname, "ended": "backoff_giveup"})
                save_state(STATE_KEY, st)
                continue
            save_state(STATE_KEY, st)
            return {"date": st["date"], "stage": sname, "paused": "backoff",
                    "b204": st["b204"], "ran": ran, "advanced": advanced, "counts": st["counts"]}
        try:
            wl = stage.build_worklist(st)
        except Exception as e:  # noqa: BLE001 — step() 契约:never raise;清单构造失败结束本片,下片重试
            log.warning("fetch_chain %s build_worklist failed: %s", sname, str(e)[:120])
            break
        if int(st["cursor"]) >= len(wl):
            _advance(st, sname, "complete")
            advanced.append({"stage": sname, "ended": "complete"})
            save_state(STATE_KEY, st)
            continue
        item = wl[int(st["cursor"])]
        try:
            n = stage.run_item(item, st)
        except Exception as e:  # noqa: BLE001 — 单 item 失败不沉整片
            n = 0
            log.warning("fetch_chain %s item %s: %s", sname, item, str(e)[:140])
        if _safe(stage.backing_off, False):                  # 本 item 触发 204 短退避(transient)
            # 不吞该 item:cursor 保持 k,退避到期原地重试(honor 204 自动恢复语义,而非当日丢弃)。
            st["b204"] = int(st.get("b204", 0)) + 1
            if st["b204"] >= _B204_STRIKES:                  # 病态供应商:弃权进位
                _advance(st, sname, "backoff_giveup")
                advanced.append({"stage": sname, "ended": "backoff_giveup"})
                save_state(STATE_KEY, st)
                continue
            save_state(STATE_KEY, st)
            return {"date": st["date"], "stage": sname, "paused": "backoff",
                    "b204": st["b204"], "ran": ran, "advanced": advanced, "counts": st["counts"]}
        _merge_count(st, sname, item, n)
        st["cursor"] = int(st["cursor"]) + 1
        st["b204"] = 0
        ran += 1
        save_state(STATE_KEY, st)                            # 每 item 落盘 → 崩溃精确续传
        if _safe(stage.exhausted, False):                    # 源当日额度耗尽 → fallback 下一源
            _advance(st, sname, "quota")
            advanced.append({"stage": sname, "ended": "quota"})
            save_state(STATE_KEY, st)
    cur_stage = order[int(st["stage"])] if int(st["stage"]) < len(order) else "done"
    return {"date": st["date"], "stage": cur_stage, "cursor": st["cursor"], "ran": ran,
            "advanced": advanced, "done": bool(st.get("done")), "counts": st["counts"]}


def status() -> dict:
    """观测口径:当日接力状态(pinned_ids 只显示数量,避免刷屏)。"""
    st = get_state(STATE_KEY)
    if isinstance(st.get("pinned_ids"), list):
        st = {**st, "pinned_ids": len(st["pinned_ids"])}
    return st
