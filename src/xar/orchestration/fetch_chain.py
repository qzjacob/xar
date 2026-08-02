"""另类语义抓取链:相关性 × 额度紧迫度的日内接力调度。

用户诉求:把 **AlphaPai 纪要抓取设为每天持续固定任务**(相关性高→低、新→旧),直到当日额度
耗尽(203/204),然后 **fallback → Gangtise**,再 **fallback → aifinmarket**,并预留未来源。

实现 = 一个**日内接力状态机**(状态存 kvstate `fetch_chain`),由 glm_worker 的 `alt_fetch_chain`
站点每 `fetch_chain_step_seconds` 驱动一步 `step()`;每步消耗一个 `fetch_chain_slice_seconds` 时间片
(item 之间检查预算,不抢占单个慢调用),不阻塞 worker 主循环。

⚠️ **进位条件已改(2026-08-02 用户裁定)**:标了 `drain_first` 的源(alphapai 三棒 +
aifinmarket)**只有当日额度真正耗尽才准交棒**;「清单跑完」不再算数 —— 清单跑完但额度还在,
改为把回看窗往前推、让清单重新长出来继续榨(`_deepen`);短退避也不再累计弃权。
起因是审计实测:alphapai 的 203 在 2.7 天日志里出现 **0 次**,却天天早上 05:00–08:46 沪时
就以 `complete`/`backoff_giveup` 交棒,之后 15–18 小时零产出 —— 付费额度大量剩在桌上。
代价是下游可能被饿久一点,由墙钟安全阀 `fetch_chain_drain_max_hours` 兜底(设 0 则永不放行)。
未标 drain_first 的源(gangtise 等无额度信号)仍是「清单跑完即进位」。

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
_B204_STRIKES_DEFAULT = 6      # 连续退避片数 → 放弃该段(病态供应商不能拖死整天);可由 config 覆盖
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
    # ── 榨干优先(2026-08-02 用户裁定)────────────────────────────────────────
    # True = **只有 exhausted() 为真才准进位**;清单跑完 / 短退避连击都不再让位。
    # 起因是审计实测:alphapai 的 203(当日额度耗尽)在 2.7 天日志里出现 **0 次**,
    # 每天早上 05:00–08:46 沪时就因 `complete` 或 `backoff_giveup` 交棒,
    # 之后 15–18 小时零产出 —— 付费额度大量剩在桌上没用。
    # 清单跑完但额度仍在 ⇒ 不交棒,改为**加深回看窗**继续榨(见 _deepen);
    # 短退避 ⇒ 不计连击、原地等,42900 是瞬时节流,不该升级成当天弃权。
    drain_first: bool = False


# --- alphapai(纪要首要 + 主题 + 头部其余类型)---
def _alphapai_companies(st: dict) -> list[str]:
    from ..providers import alphapai
    return [cid for cid in st.get("pinned_ids", []) if alphapai.has_cjk_name(cid)]


def theme_queries() -> list[tuple[str, str]]:
    """主题维查询 [(scope, query)]:行业 + 策略 + 宏观 + 资金流(复用 aifin_catalog 词表,
    与 THEMES 的 nameCn 合并)。scope 落进 documents.meta 供分轨观测。"""
    from ..ingestion.registry import THEMES
    from ..providers import aifin_catalog as cat

    dims = {d.strip() for d in (get_settings().alphapai_theme_dims or "").split(",") if d.strip()}
    out: list[tuple[str, str]] = []
    if "industry" in dims:
        out += [("industry", (THEMES[t].get("nameCn") or t)) for t in THEMES]
        out += [("industry", q) for q in cat.INDUSTRY_QUERIES]
    if "strategy" in dims:
        out += [("strategy", q) for q in cat.STRATEGY_QUERIES]
    if "macro" in dims:
        out += [("macro", q) for q in cat.MACRO_QUERIES]
    if "moneyflow" in dims:
        out += [("moneyflow", q) for q in cat.MONEYFLOW_QUERIES]
    return out


def _alphapai_worklist(st: dict) -> list:
    s = get_settings()
    companies = _alphapai_companies(st)
    minutes = [["minutes", cid] for cid in companies]                        # 纪要(相关性序)
    themes = [["theme", scope, q] for scope, q in theme_queries()]           # 主题(行业/策略/宏观/资金流)
    top = s.fetch_chain_alphapai_rest_top                                     # 其余类型(0=全库)
    rest = [["rest", cid] for cid in (companies if top <= 0 else companies[:top])]
    # 主题前置(默认):主题只有 ~76 项却是唯一 0 产出的维度,先跑让宏观/策略/资金流立刻落库;
    # 纪要仍排在 rest 与回溯段之前,不影响「当日新发布纪要优先」的原则。
    head = themes + minutes if s.fetch_chain_alphapai_theme_first else minutes + themes
    return head + rest


def _alphapai_run(item: list, st: dict) -> int:
    from ..providers import alphapai
    kind = item[0]
    if kind == "minutes":
        return alphapai.pull_minutes(item[1], start=st.get("alphapai_start"))
    if kind == "theme":
        return alphapai.pull_theme_window(item[2], scope=item[1], start=st.get("alphapai_start"))
    if kind == "rest":
        return alphapai.pull_company(item[1])
    return 0


def _alphapai_stage() -> Stage:
    from ..providers import alphapai
    return Stage(name="alphapai",
                 available=lambda: alphapai.available() and get_settings().enable_alphapai,
                 build_worklist=_alphapai_worklist, run_item=_alphapai_run,
                 exhausted=alphapai.quota_exhausted, backing_off=alphapai.quota_backing_off,
                 drain_first=True)


# --- alphapai_backfill(过去一年逐窗回溯,新→旧;量的主杠杆)---
BF_KEY = "alphapai_bf"


def _bf_windows() -> list[tuple[str, str]]:
    """过去 backfill_days 切成 window_days 宽的窗,**新→旧**排列 → [(start, end)]。"""
    s = get_settings()
    w = max(1, s.alphapai_backfill_window_days)
    n = max(1, (max(1, s.alphapai_backfill_days) + w - 1) // w)
    today = datetime.now(_CN_TZ).date()
    out = []
    for i in range(n):
        end = today - timedelta(days=i * w)
        out.append(((end - timedelta(days=w)).isoformat(), end.isoformat()))
    return out


def _bf_state() -> dict:
    return get_state(BF_KEY, {"win": 0})


def _bf_worklist(st: dict) -> list:
    """当前窗的工作单元:公司维(相关性序)+ 主题维;末尾一个 advance 标记推进到更旧的窗。
    每次 chain pass 走完一窗 —— 窗内 cursor 由 chain 持久化,崩溃可续。"""
    wins = _bf_windows()
    win = int(_bf_state().get("win", 0))
    if win >= len(wins):
        return []                                   # 一年已回完 → 该段自然空转(fresh 段维持日增)
    start, end = wins[win]
    items: list = [["bf_co", cid, start, end] for cid in _alphapai_companies(st)]
    items += [["bf_theme", scope, q, start, end] for scope, q in theme_queries()]
    items.append(["bf_advance", win])
    return items


def _bf_run(item: list, st: dict) -> int:
    from ..providers import alphapai
    kind = item[0]
    if kind == "bf_co":
        return alphapai.pull_company_window(item[1], start=item[2], end=item[3])
    if kind == "bf_theme":
        return alphapai.pull_theme_window(item[2], scope=item[1], start=item[3], end=item[4])
    if kind == "bf_advance":
        nxt = int(item[1]) + 1
        save_state(BF_KEY, {"win": nxt, "advanced_at": _cn_now_iso()})
        log.info("alphapai backfill 窗口推进 → %d/%d", nxt, len(_bf_windows()))
        return 0
    return 0


def _bf_stage() -> Stage:
    from ..providers import alphapai
    return Stage(name="alphapai_backfill",
                 available=lambda: (alphapai.available() and get_settings().enable_alphapai
                                    and get_settings().alphapai_backfill_enabled),
                 build_worklist=_bf_worklist, run_item=_bf_run,
                 exhausted=alphapai.quota_exhausted, backing_off=alphapai.quota_backing_off,
                 drain_first=True)


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
                 exhausted=alphapai.quota_exhausted, backing_off=alphapai.quota_backing_off,
                 drain_first=True)


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
                 # 多账号:all_seats_exhausted() 要求**每个席位**都到当日上限或冷却中,
                 # 所以「榨干」在这里天然是「榨干全部账号」,不是任一账号触顶就走。
                 exhausted=aifinmarket.all_seats_exhausted, drain_first=True)


def stages() -> dict[str, Stage]:
    """内置阶段注册表。未来新增源 = 加一项 + 在 fetch_chain_order 追加名字。"""
    return {"alphapai": _alphapai_stage(), "alphapai_backfill": _bf_stage(),
            "gangtise": _gangtise_stage(), "aifinmarket": _aifin_stage(),
            "alphapai_agents": _agents_stage()}


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
          "counts": {name: {} for name in order}, "stage_log": [], "done": False,
          "drain_rounds": 0, "stage_since": {order[0]: _cn_now_iso()} if order else {}}
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
          "counts": {name: {} for name in st["order"]}, "stage_log": [],
          "drain_rounds": 0,
          "stage_since": {st["order"][0]: _cn_now_iso()} if st.get("order") else {}}
    save_state(STATE_KEY, st)
    return st


def _drain_valve_expired(st: dict, sname: str) -> bool:
    """榨干模式的**墙钟安全阀**:这一棒已经霸着链多久了。

    为什么必须有:用户要的是「额度不耗尽不许交棒」,而这条规则的字面执行有一个尖锐后果 ——
    只要供应商坏掉(持续 42900、或额度谓词永远不为真),这一棒就会把整条链**吊死一整天**,
    下游 gangtise / aifinmarket / agents 一粒米都吃不到。那比"额度没榨干"更糟。
    所以设一个上限:超过 `fetch_chain_drain_max_hours` 仍未耗尽,放行并大声记一笔。
    设成 0 = 关闭安全阀 = 纯粹的硬阻塞(真想要「除非耗尽否则永不交棒」就配 0)。
    """
    hours = float(getattr(get_settings(), "fetch_chain_drain_max_hours", 10.0) or 0)
    if hours <= 0:
        return False                                  # 显式关阀:永不放行
    since = (st.get("stage_since") or {}).get(sname)
    if not since:
        return False
    try:
        held = (datetime.now(_CN_TZ) - datetime.fromisoformat(since)).total_seconds() / 3600
    except ValueError:
        return False
    return held >= hours


def _deepen(st: dict, sname: str) -> None:
    """清单跑完但额度还在 → 把回看窗往前推一段,让清单重新长出来继续榨。

    只对 alphapai 家族有意义(它的清单长度由 `alphapai_start` 决定);其余段没有这个旋钮,
    退化成「把游标归零、重扫一遍」——对 aifinmarket 这类轮询源同样能继续消耗额度。
    """
    st["cursor"] = 0
    st["drain_rounds"] = int(st.get("drain_rounds", 0)) + 1
    if sname.startswith("alphapai"):
        step_days = max(1, int(getattr(get_settings(), "alphapai_lookback_days", 30) or 30))
        try:
            cur = datetime.fromisoformat(st["alphapai_start"]).date()
        except (KeyError, TypeError, ValueError):
            cur = datetime.now(_CN_TZ).date()
        st["alphapai_start"] = (cur - timedelta(days=step_days)).isoformat()
        log.info("fetch_chain %s 清单跑完但额度未耗尽 → 回看窗推到 %s(第 %d 轮加深)",
                 sname, st["alphapai_start"], st["drain_rounds"])
    else:
        log.info("fetch_chain %s 清单跑完但额度未耗尽 → 游标归零重扫(第 %d 轮)",
                 sname, st["drain_rounds"])


def _advance(st: dict, sname: str, ended: str) -> None:
    st["stage_log"] = (st.get("stage_log") or [])[-(_STAGE_LOG_CAP - 1):] + [
        {"stage": sname, "ended": ended, "at": _cn_now_iso()}]
    st["stage"] = int(st["stage"]) + 1
    st["cursor"] = 0
    st["b204"] = 0
    st["drain_rounds"] = 0
    # 给下一棒起表 —— 榨干模式的安全阀按「这一棒霸着链多久」计时,没有起点就没有阀。
    order = st.get("order") or []
    if int(st["stage"]) < len(order):
        st.setdefault("stage_since", {})[order[int(st["stage"])]] = _cn_now_iso()


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
        if _safe(stage.backing_off, False):                  # 短退避:暂停不进位
            st["b204"] = int(st.get("b204", 0)) + 1
            # drain_first 段**不因退避连击弃权**(2026-08-02):42900/204 是瞬时节流,
            # 把它升级成「当天放弃这一棒」正是额度剩在桌上的主因之一 —— 实测 alphapai
            # 天天以 backoff_giveup 收场,而 203 从未出现过。原地等,下一片再来。
            # 唯一例外是墙钟安全阀(见 _drain_valve_expired):否则一个病态供应商
            # 能把整条链吊死一整天,下游 gangtise/aifinmarket 一粒米都吃不到。
            if stage.drain_first and not _drain_valve_expired(st, sname):
                save_state(STATE_KEY, st)
                return {"date": st["date"], "stage": sname, "paused": "backoff_drain",
                        "b204": st["b204"], "ran": ran, "advanced": advanced,
                        "counts": st["counts"]}
            if st["b204"] >= getattr(get_settings(), "fetch_chain_backoff_strikes", _B204_STRIKES_DEFAULT):
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
            # 清单跑完 —— 但 drain_first 段**额度没耗尽就不准交棒**(2026-08-02 用户裁定)。
            # 「跑完」不等于「榨干」:清单是由 pinned 公司 × 类型 × 回看窗生成的,
            # 窗越深清单越长。所以这里不进位,而是**把回看窗往前推**再来一轮 —— 既继续
            # 消耗额度,拿到的又是有用的历史纪要,不是空转重复。
            if stage.drain_first and not _safe(stage.exhausted, False):
                if _drain_valve_expired(st, sname):
                    _advance(st, sname, "drain_timeout")     # 安全阀:见 _drain_valve_expired
                    advanced.append({"stage": sname, "ended": "drain_timeout"})
                    log.warning("fetch_chain %s 到达榨干安全阀仍未耗尽额度 → 放行下一棒", sname)
                    save_state(STATE_KEY, st)
                    continue
                _deepen(st, sname)
                save_state(STATE_KEY, st)
                continue
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
            if st["b204"] >= getattr(get_settings(), "fetch_chain_backoff_strikes", _B204_STRIKES_DEFAULT):                  # 病态供应商:弃权进位
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
