"""GLM 订阅额度感知的常驻抽取工人 —— 对 Coding Plan 的机制化利用。

前提:GLM Coding Plan(glm-5.2-sub)按订阅计费,**没有账单超额风险**,但受
5 小时窗/周额度限制;额度耗尽表现为 RateLimitError(余额不足/无可用资源包),
窗口滚动后自动恢复。

因此正确的形状是一个**常驻循环**,而非固定 cron:
  1. 每轮先做零 LLM 的工作(语义源增量拉取 Twitter/微信/Finnhub、10 年历史回填
     推进、本地嵌入解析)——额度耗尽也不停;
  2. LLM 抽取阶段前先发一次极小探针;通过 → 在 llm.pinned(GLM_PIN) 内跑
     build_kg + expert 批次(**钉扎到订阅模型,链外零回退**——额度耗尽=等待,
     绝不落到按 token 计费的模型);失败 → 记录 exhausted,进入探针节奏
     (默认 15 分钟)直至额度恢复,随即自动重启抽取;
  3. 全部阶段幂等(pending 游标 / 内容哈希去重 / ON CONFLICT),崩溃即续。

状态持久化在 glm_worker_state(key/value JSONB);`xar glm-worker status` 可视;
docker compose 的 `glmworker` 常驻服务(restart: unless-stopped)即"自动定时任务"。
"""
from __future__ import annotations

import json
import signal
import time
from datetime import datetime, timezone

from ..logging import get_logger
from ..models import llm
from ..storage import db
from ..storage.kvstate import get_state, save_state

log = get_logger("xar.glm_worker")

# 钉扎链:只允许 GLM 订阅池(5.2 优先,4.6 兜底)。z.ai 国际版月度订阅**支持 GLM-5.2**;
# 它是推理模型,极小 token 预算会把额度耗在 reasoning 上、content 为空 —— 见 probe() 的
# max_tokens 说明。没有 kimi/deepseek —— 那是夜批的回退;本工人"额度内白嫖到底,额度外分文不花"。
GLM_PIN: tuple[str, ...] = ("glm-5.2-sub", "glm-4.6-sub")

# 本地优先头(minis 3090/ollama,算力调度方案 §9):XAR_GLM_WORKER_LOCAL_FIRST=true 时
# _fetchy_pin 把它前插到钉扎链首 —— 前插而非替换:本地零成本,端点不可达(含 mlrun
# --exclusive 独占停机)由 llm.complete 的候选轮转自动回落云 GLM(抢占协议的消费端)。
LOCAL_MODEL_ID = "glm4-local"

# 精确额度识别:类型优先(litellm.RateLimitError),文案兜底。刻意不含 'exceed'/'429'
# —— llm.BudgetExceeded("run kg-x exceeded $N")与 ContextWindowExceededError 不是额度耗尽。
_QUOTA_MARKERS = ("余额不足", "无可用资源包", "rate limit", "ratelimit",
                  "too many requests", "quota")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_quota_error(e: Exception) -> bool:
    if isinstance(e, llm.BudgetExceeded):     # token 预算帽,不是订阅额度
        return False
    if type(e).__name__ == "RateLimitError":  # litellm 的额度/限流类型
        return True
    msg = str(e).lower()
    return any(m in msg or m in str(e) for m in _QUOTA_MARKERS)


def _sub_ready() -> bool:
    """零账单保证的前置:订阅 key 必须在位。缺失时 _endpoint 会静默回退到按 token
    计费的 GLM key —— 工人宁可拒绝抽取也不允许这种回退。"""
    from ..config import get_settings

    return bool(get_settings().glm_sub_api_key)


# ── 额度治理器 ─────────────────────────────────────────────────────────────────
def probe() -> bool:
    """探针(订阅计费=0 成本):GLM 池当前是否可用。max_tokens 必须够大 —— GLM-5.2 是推理
    模型,极小预算(如 8)会把额度全耗在 reasoning 上、content 为空,llm.complete 判空后会
    误判失败并轮转,导致对主模型的探测永远假阴。256 足够越过 reasoning 吐出可见内容。"""
    try:
        with llm.pinned(GLM_PIN):
            llm.complete("Reply with exactly: ok", task="adhoc_fast", node="glm_worker",
                         max_tokens=256)
        return True
    except Exception as e:  # noqa: BLE001
        if is_quota_error(e):
            log.info("glm quota probe: exhausted (%s)", str(e)[:100])
        else:
            log.warning("glm quota probe non-quota failure: %s", str(e)[:160])
        return False


def _mark_exhausted(q: dict, reason: str) -> dict:
    if q.get("status") != "exhausted":
        q.update({"status": "exhausted", "exhausted_at": _now(),
                  "exhaust_count": int(q.get("exhaust_count", 0)) + 1,
                  "last_reason": reason[:200]})
        log.warning("GLM quota EXHAUSTED — worker enters probe mode (%s)", reason[:120])
    q["last_probe_at"] = _now()
    save_state("quota", q)
    return q


def _mark_ok(q: dict) -> dict:
    if q.get("status") == "exhausted":
        q["resumed_at"] = _now()
        q["resume_count"] = int(q.get("resume_count", 0)) + 1
        log.info("GLM quota RECOVERED — extraction resumes automatically")
    q.update({"status": "ok", "last_probe_at": _now()})
    save_state("quota", q)
    return q


# ── Fetchy 管理面(Jarvy 前端 ↔ 共享 DB;工人每轮读取,app 容器写入)───────────
# 控制面走 glm_worker_state(key="fetchy")—— 两个容器共享同一 Postgres,无需 RPC。
FETCHY_SOURCES: dict[str, dict] = {   # cadence key → 标签/节拍(小时;None=按 config)
    "twitter": {"label": "Twitter 专家声音(计量 API·默认关·月度限额)", "hours": 1},
    "wechat": {"label": "微信公众号", "hours": 1},
    "finnhub_news": {"label": "Finnhub 新闻", "hours": 4},
    "rss": {"label": "RSS 源", "hours": 2},
    "alt": {"label": "另类数据追踪器", "hours": 6},
    "futu_news": {"label": "富途资讯", "hours": 3},
    "gangtise": {"label": "Gangtise 结构化(财务/预期)", "hours": 12},
    "gangtise_insight": {"label": "Gangtise 投研文本(研报/纪要)", "hours": None},
    "gangtise_backfill": {"label": "Gangtise 历史回填", "hours": 6},
    "wind_edb": {"label": "万得 EDB 数据追踪", "hours": 24},
    "aifinmarket_theme": {"label": "万得主题资讯", "hours": 24},
    "earnings_watch": {"label": "季报观察窗(日历/预期/隐波)", "hours": 6},
    "flow": {"label": "资金流(ETF/风格/空头/期权)", "hours": 24},
    "andy_macro": {"label": "Andy 宏观库(FRED vintage/识别/登记簿/勾稽)", "hours": 24},
}
FETCHY_STAGES: dict[str, str] = {     # run_once 阶段 → 标签
    "parse": "解析 + 本地嵌入(零 LLM)",
    "backfill": "10 年历史回填(零 LLM)",
    "indicators": "衍生追踪指标(零 LLM)",
    "extract": "LLM 语义抽取(triage/KG/专家/证据链接)",
    "alt_correction": "信号校正 + 论点重建",
    "research_audit": "独立抓取审计(每日)",
    "earnings": "季报裁决 + 盘后回验",
}


def fetchy_defaults() -> dict:
    sources = dict.fromkeys(FETCHY_SOURCES, True)
    # twitter = 计量外部 API(twitterapi.io):默认关,需运营在 Jarvy 显式开启;开启后仍受
    # providers/twitter.py 的月度总限额闸(x_monthly_budget_usd,2026-07-20 裁定 $20/月)封顶。
    sources["twitter"] = False
    return {"enabled": True, "model": GLM_PIN[0],
            "sources": sources,
            "stages": dict.fromkeys(FETCHY_STAGES, True)}


def fetchy_config(*, strict: bool = False) -> dict:
    """生效配置 = 默认 ⊕ 已保存(未知键忽略,新增源/阶段自动继承默认开)。

    ``strict=True``(工人循环用):配置读取失败时**上抛** —— 总开关必须 fail-closed,
    不能因一次 DB 抖动把显式关掉的工人悄悄全量拉起。默认(API/CLI 展示)容错返回默认。
    """
    cfg = fetchy_defaults()
    try:
        saved = get_state("fetchy")
    except Exception:  # noqa: BLE001 — DB 未就绪:展示用途按默认;工人循环 fail-closed
        if strict:
            raise
        return cfg
    if isinstance(saved.get("enabled"), bool):
        cfg["enabled"] = saved["enabled"]
    if isinstance(saved.get("model"), str) and saved["model"]:
        cfg["model"] = saved["model"]
    for group in ("sources", "stages"):
        for k, v in (saved.get(group) or {}).items():
            if k in cfg[group] and isinstance(v, bool):
                cfg[group][k] = v
    return cfg


def model_usable(model_id: str) -> str | None:
    """该模型能否在 glmworker 容器内实际服务:None=可用,否则返回原因(中文)。

    工人是 docker 常驻批量任务 —— host-only 执行器(agent_sdk/codex_cli)在容器内
    不可用;token/订阅模型需要对应 provider key 在 env 中在场。"""
    import os

    from ..models import registry as reg

    llm._ensure_keys()  # Settings(.env)→os.environ 懒同步;首个 LLM 调用前也要看得见 key

    spec = reg.get(model_id)
    if spec is None:
        return f"未知模型 {model_id!r}"
    if spec.status != reg.Status.ACTIVE:
        return f"{model_id} 已退役({spec.status.value})"
    if spec.executor != "litellm":
        return f"{model_id} 是 host-only 执行器({spec.executor}),工人容器内不可用"
    prov = reg.PROVIDERS.get(spec.provider)
    if prov is None:
        return f"{model_id} 无 provider 配置"
    key_env = (prov.sub_key_env if spec.billing == reg.Billing.SUBSCRIPTION and prov.sub_key_env
               else prov.key_env)
    if not os.environ.get(key_env or ""):
        return f"{model_id} 的密钥({key_env})未配置"
    return None


def save_fetchy(cfg: dict) -> dict:
    """校验并保存;与已保存文档**合并**(部分 PUT 不得抹掉先前的开关);返回生效配置。"""
    try:
        clean = dict(get_state("fetchy"))
    except Exception:  # noqa: BLE001
        clean = {}
    if isinstance(cfg.get("enabled"), bool):
        clean["enabled"] = cfg["enabled"]
    model = cfg.get("model")
    if isinstance(model, str) and model:
        reason = model_usable(model)
        if reason:
            raise ValueError(reason)
        clean["model"] = model
    for group, catalog in (("sources", FETCHY_SOURCES), ("stages", FETCHY_STAGES)):
        vals = cfg.get(group)
        if isinstance(vals, dict):
            merged = dict(clean.get(group) or {})
            merged.update({k: bool(v) for k, v in vals.items() if k in catalog})
            clean[group] = merged
    save_state("fetchy", clean)
    return fetchy_config()


def _fetchy_pin(cfg: dict) -> tuple[str, ...]:
    """选中的模型放链首,GLM_PIN 其余保持为回退链;Fetchy 未显式选型时,本地优先开关
    (glm_worker_local_first)再往前插本地头(glm_worker_local_model,默认 glm4-local——
    换代/回滚改 env 即可,零代码)。显式选型 = 操作员意图,压过本地优先。
    仅在云订阅 key 在位(_sub_ready)时前插 —— 本地头会让 run_once 的订阅门(链首非
    GLM 则放行)失效,故零计量回退不变量必须在此保住:回退尾必须是订阅 GLM。
    配置的本地模型不可用(拼错/PREVIEW/无 key)= 安全降级回纯云钉扎链,绝不炸工人。"""
    m = cfg.get("model")
    if m and m != GLM_PIN[0]:
        return (m, *tuple(x for x in GLM_PIN if x != m))
    from ..config import get_settings

    s = get_settings()
    if s.glm_worker_local_first and _sub_ready():
        mid = s.glm_worker_local_model or LOCAL_MODEL_ID
        reason = model_usable(mid)
        if reason is None:
            return (mid, *GLM_PIN)
        log.warning("本地优先开启但 %s 不可用:%s —— 本周期回落纯云钉扎链", mid, reason)
    return GLM_PIN


# ── 工作阶段 ──────────────────────────────────────────────────────────────────
def _due(key: str, every_seconds: int) -> bool:
    """只读检查(不落盘);成功/失败后由 _stamp 记录,失败按 1/4 间隔提前重试。"""
    last = get_state("cadence").get(key)
    if last:
        try:
            prev = datetime.fromisoformat(last)
            if (datetime.now(timezone.utc) - prev).total_seconds() < every_seconds:
                return False
        except ValueError:
            pass
    return True


def _stamp(key: str, every_seconds: int, *, ok: bool) -> None:
    from datetime import timedelta

    st = get_state("cadence")
    ts = datetime.now(timezone.utc)
    if not ok:                                  # 失败:回退 3/4 间隔 → 1/4 间隔后重试
        ts -= timedelta(seconds=every_seconds * 0.75)
    st[key] = ts.isoformat(timespec="seconds")
    save_state("cadence", st)


def _pull_fresh(cfg: dict | None = None) -> dict:
    """语义源增量拉取(零 LLM):Twitter 专家声音 / 微信公众号 / Finnhub 新闻。
    各自带节拍(不逐轮硬打源);单源失败不沉轮;Fetchy 勾掉的源直接跳过。"""
    out: dict = {}
    src_on = (cfg or fetchy_config())["sources"]

    def _run(key: str, every: int, fn) -> None:
        if not src_on.get(key, True):   # Fetchy 关闭该源(不 stamp,重开即恢复节拍)
            return
        if not _due(key, every):
            return
        ok = True
        try:
            out[key] = fn()
        except Exception as e:  # noqa: BLE001
            ok = False
            out[key] = {"error": str(e)[:120]}
        _stamp(key, every, ok=ok)

    def _twitter():
        from ..providers import twitter

        return twitter.pull()

    def _wechat():
        from ..ingestion import ingest_wechat, wechat
        from ..mining import roster

        if not wechat.available():
            return {"skipped": "no werss"}
        feeds = roster.active_feeds()
        if not feeds:                       # 名册空 → 退回聚合 /rss
            return {"docs": len(ingest_wechat()), "mode": "aggregated"}
        n = 0
        for f in feeds:                     # 策展名册:逐号拉,带公司绑定
            try:
                n += len(wechat.ingest_feed(f["feed_id"], company_id=f.get("company_id")))
            except Exception as e:          # noqa: BLE001 — 单号失败不沉整轮
                from ..logging import get_logger
                get_logger("xar.glm_worker").warning("wechat feed %s: %s", f["feed_id"], e)
        return {"docs": n, "mode": "roster", "feeds": len(feeds)}

    def _finnhub():
        from datetime import date, timedelta

        from ..providers import finnhub

        return finnhub.pull_news_basket(since=date.today() - timedelta(days=2))

    def _rss():
        from ..providers import rss

        return {"docs": rss.pull()}

    def _alt():
        from ..config import get_settings
        from ..ingestion import alt

        return alt.pull_all(limit=get_settings().glm_worker_alt_limit)

    def _futu():
        # 富途资讯:每轮拉一个轮转切片的公司新闻(快照+板块随夜批全量刷)。OpenD 关=跳过。
        from ..config import get_settings
        from ..ingestion.registry import COMPANIES
        from ..providers import futu

        if not futu.available():
            return {"skipped": "futu OpenD unavailable"}
        n = get_settings().glm_worker_alt_limit
        # code_from_tickers(c["tickers"]) directly — futu_code(c["id"]) re-scans all COMPANIES
        # via company_by_id per company (O(n²) over ~1000 names).
        ids = [c["id"] for c in COMPANIES if futu.code_from_tickers(c.get("tickers"))]
        if not ids:
            return {"skipped": "no futu-addressable companies"}
        off = int(get_state("cursor").get("futu", 0)) % len(ids)
        sl = ids[off:off + n]
        docs = sum(futu.pull_news(cid) for cid in sl)
        cur = get_state("cursor")
        cur["futu"] = (off + len(sl)) % len(ids)
        save_state("cursor", cur)
        return {"news": docs, "companies": len(sl), "offset": off}
    def _gangtise():
        # 富途/万得之外的深度投研:CN 名单轮转切片拉 Gangtise 财报/估值/一致预期/投研文本
        # (零 LLM;文本随后走 triage→build_kg 喂 thesis)。未启用/无 key = 跳过。
        from ..config import get_settings
        from ..ingestion.registry import COMPANIES
        from ..providers import gangtise

        if not gangtise.available():
            return {"skipped": "gangtise disabled"}
        # 核心公司优先序(种子∩CN → 覆盖度 → 注册表);游标是偏移量,名单日内稳定。
        try:
            from ..providers.gangtise import planner
            cn = planner.cn_priority_order()
        except Exception:  # noqa: BLE001 —— 规划器不可用时退回注册表原序
            cn = [c["id"] for c in COMPANIES
                  if c.get("region") == "CN"
                  or any(str(t).endswith((".SS", ".SH", ".SZ")) for t in (c.get("tickers") or []))]
        if not cn:
            return {"skipped": "no CN names"}
        limit = min(get_settings().glm_worker_gangtise_limit, len(cn))
        off = int(get_state("cursor").get("gangtise", 0)) % len(cn)
        todo = (cn + cn)[off:off + limit]          # wrap-around so tail cycles still get `limit`
        ok = 0
        for cid in todo:
            try:
                gangtise.pull(cid)
                ok += 1
            except Exception as e:  # noqa: BLE001 — 单公司失败不沉整轮
                log.warning("gangtise %s: %s", cid, str(e)[:120])
        cur = get_state("cursor")                  # advance cursor AFTER the slice runs
        cur["gangtise"] = (off + len(todo)) % len(cn)
        save_state("cursor", cur)
        return {"attempted": len(todo), "ok": ok, "off": off}

    def _gangtise_insight():
        # 非标语义抓取(券商研报/纪要/MD&A)——核心优先 + 每日刷新;零 LLM。
        from ..providers.gangtise import planner
        return planner.fresh_sweep()

    def _gangtise_backfill():
        from ..config import get_settings
        from ..providers.gangtise import planner
        return planner.backfill_step(get_settings().gangtise_backfill_units)

    def _wind_edb():
        from ..ingestion import alt
        return alt.pull_source("wind_edb")

    def _aifin_theme():
        # 修 pull_theme_news 孤儿:遍历主题拉行业资讯(company_id=None)。
        from ..ingestion.registry import THEMES
        from ..providers import aifinmarket
        if not aifinmarket.available():
            return {"skipped": "aifinmarket disabled"}
        n = 0
        for tid, t in THEMES.items():
            try:
                n += aifinmarket.pull_theme_news(f"{t.get('nameCn') or tid} 产业链 业绩 需求")
            except Exception as e:  # noqa: BLE001
                log.warning("aifin theme %s: %s", tid, str(e)[:120])
        return {"themes": len(THEMES), "docs": n}

    from ..config import get_settings
    s = get_settings()
    _run("twitter", 3600, _twitter)
    _run("wechat", 3600, _wechat)
    _run("finnhub_news", 4 * 3600, _finnhub)
    _run("rss", 2 * 3600, _rss)
    _run("alt", 6 * 3600, _alt)
    _run("futu_news", 3 * 3600, _futu)
    _run("gangtise", 12 * 3600, _gangtise)
    _run("gangtise_insight", s.gangtise_insight_hours * 3600, _gangtise_insight)
    _run("gangtise_backfill", 6 * 3600, _gangtise_backfill)
    _run("wind_edb", 24 * 3600, _wind_edb)
    _run("aifinmarket_theme", 24 * 3600, _aifin_theme)
    _run("earnings_watch", 6 * 3600, _earnings_watch)   # 季报观察窗:日历/analyst/隐含波动刷新
    _run("flow", 24 * 3600, _flow_daily)                # 资金流:ETF/风格/空头/期权 → alt_signals
    _run("andy_macro", 24 * 3600, _andy_macro)          # Andy 宏观库:连接器/识别/登记簿/勾稽
    return out


def _flow_daily() -> dict:
    from ..research import flow

    return flow.run_daily()


def _andy_macro() -> dict:
    """Andy 宏观库日更(零 LLM):FRED 等连接器 → 识别引擎 → 登记簿评估 → 勾稽桥。
    复刻 daily.py 的 opt-in macro 块为常驻源(Fetchy 可关);全链幂等,双跑无害。"""
    from datetime import date as _date

    from slx.engine import overclaim
    from slx.ingestion.discovery import discover_connectors
    from slx.ingestion.identification_panels import run_identification

    from ..cli import _bridge_slx_env
    from ..ingestion import macro_bridge

    _bridge_slx_env()                      # xar settings → slx env(FRED/BEA/… key 注入)
    swept, failed = 0, []
    for source_id, (cls, is_primary) in sorted(discover_connectors().items()):
        # seed=测试种子;stooq/bls 沙箱网络被封从未成功——跳过省时,别的源单跑单容错
        if not is_primary or source_id in ("seed", "stooq", "bls"):
            continue
        try:
            cls().run()                    # 行落 slx.observation;审计在 audit_log
            swept += 1
        except Exception as e:  # noqa: BLE001
            failed.append(source_id)
            log.warning("andy_macro connector %s: %s", source_id, str(e)[:120])
    out: dict = {"sources": swept}
    if failed:
        out["failed"] = failed
    try:
        run_identification(_date.today())
        overclaim.run(_date.today())
        out["engine"] = "ok"
    except Exception as e:  # noqa: BLE001
        out["engine_error"] = str(e)[:120]
    try:
        out["bridge"] = macro_bridge.sync(_date.today())
    except Exception as e:  # noqa: BLE001
        out["bridge_error"] = str(e)[:120]
    if not swept:
        # 全军覆没(断网/DB down/key 失效)必须上抛——_run 才会 _stamp(ok=False),
        # 1/4 间隔(6h)重试;否则静默盖 ok 戳,失败一次等满 24h(评审捕获)。
        raise RuntimeError(f"andy_macro: all connectors failed {failed}")
    return out


def _earnings_watch() -> dict:
    from ..research import earnings

    return earnings.refresh_window()


def _backfill(units: int) -> dict:
    """10 年历史回填推进(零 LLM;游标断点续走)。"""
    try:
        from ..ingestion import history

        return history.backfill_step(units=units)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:160]}


def _llm_stage(batch_docs: int, q: dict, pin: tuple[str, ...] = GLM_PIN) -> tuple[dict, dict]:
    """钉扎抽取批次(链首 = Fetchy 选中的模型):KG 语义抽取 + 专家洞见。build_kg 逐文档
    容错(毒文档盖戳跳过),但额度类错误(RateLimitError)与预算帽(BudgetExceeded)会
    中止整批上抛到这里 —— 额度错在此定性并翻转状态。"""
    from ..config import get_settings
    from ..kg import expert
    from ..kg import extract as kg_extract
    from ..mining import triage
    from ..research import evidence_link

    s = get_settings()
    out: dict = {}
    run_id = llm.new_batch_run_id("kg")
    try:
        with llm.pinned(pin):
            # T2:微信 SNR triage 必须在 build_kg 之前(同周期新拉的微信文档先打分,
            # 再由两条 WHERE 守卫筛队列 —— 消除"先抽后筛"的竞态与额度浪费)。
            out["triage"] = triage.triage_pending(
                limit=s.glm_worker_triage_docs, run_id=run_id)
            out["kg"] = kg_extract.build_kg(limit=batch_docs, run_id=run_id)
            out["expert"] = expert.process(limit=max(batch_docs // 2, 5), run_id=run_id)
            # 资金流定向抽取(flow 点评/仓位表述 → kg_events(flow_insight);关键词 triage 先筛)
            from ..kg import flow_extract
            out["flow"] = flow_extract.process(limit=max(batch_docs // 4, 3), run_id=run_id)
            # 相对主张证据链接:同周期新事实 → 争论/支柱裁决(订阅池;RateLimitError 中止本批)
            out["links"] = evidence_link.link_pending(s.glm_worker_link_companies, run_id=run_id)
    except Exception as e:  # noqa: BLE001
        out["aborted"] = str(e)[:160]
        if is_quota_error(e):
            q = _mark_exhausted(q, str(e))
    # VP 数值检查(零 LLM,与额度无关,每轮做)
    try:
        out["vp_checks"] = evidence_link.check_pending(s.glm_worker_link_companies, run_id=run_id)
    except Exception as e:  # noqa: BLE001
        out["vp_checks"] = {"error": str(e)[:120]}
    return out, q


def _research_audit_step() -> dict:
    """独立抓取审计(每日节拍;TaskClass.AUDIT 强 token 模型,pinned/quota 门之外)。
    模块级函数:run_once 调用此单一入口,glm_worker 单测可整体打桩,避免测试发真实审计 LLM 调用。"""
    if not _due("research_audit", 24 * 3600):
        return {"skipped": "not due"}
    try:
        from ..orchestration import research_audit
        out = research_audit.run_audit()
        _stamp("research_audit", 24 * 3600, ok=True)
        return out
    except Exception as e:  # noqa: BLE001
        _stamp("research_audit", 24 * 3600, ok=False)
        return {"error": str(e)[:160]}


def _earnings_step() -> dict:
    """季报裁决 + 盘后回验(pinned/quota 门之外;host 由 build_verdict 内 pinned 提级订阅执行器)。
    模块级:run_once 单一入口,glm_worker 单测可整体打桩,不发真实裁决 LLM 调用。"""
    out: dict = {}
    if _due("earnings_verdicts", 24 * 3600):
        try:
            from ..research import earnings
            out["verdicts"] = earnings.judge_due()
            _stamp("earnings_verdicts", 24 * 3600, ok=True)
        except Exception as e:  # noqa: BLE001
            out["verdicts"] = {"error": str(e)[:160]}
            _stamp("earnings_verdicts", 24 * 3600, ok=False)
    if _due("earnings_outcomes", 12 * 3600):
        try:
            from ..research import earnings
            out["outcomes"] = earnings.score_outcomes()
            _stamp("earnings_outcomes", 12 * 3600, ok=True)
        except Exception as e:  # noqa: BLE001
            out["outcomes"] = {"error": str(e)[:160]}
            _stamp("earnings_outcomes", 12 * 3600, ok=False)
    return out


def _alt_correction(q: dict, rebuilds: int, pin: tuple[str, ...] = GLM_PIN) -> dict:
    """信号→事件(每轮,零 LLM)+ 信号挑战最重的论点在额度 ok 时钉扎重建。"""
    out: dict = {}
    try:
        from ..research import thesis_signals

        out["events"] = thesis_signals.sync_alt_events()
    except Exception as e:  # noqa: BLE001
        out["events"] = {"error": str(e)[:160]}
    # GLM 订阅门只对 GLM 链首适用(与 run_once 的 extract 门同理):用户钉扎其他模型时
    # save 已校验其密钥在位,不能再拿 GLM_SUB_API_KEY 一票否决。
    head_ok = _sub_ready() if pin[0] in GLM_PIN else True
    if q.get("status") == "ok" and head_ok and rebuilds > 0:
        try:
            from ..research import thesis, thesis_health

            # 信号面 + 争论翻转面挑战最重的论点 → 钉扎重写(天平翻转闭环)
            cids = thesis_health.challenged_companies_v2(limit=rebuilds)
            rebuilt = []
            with llm.pinned(pin):
                for cid in cids:
                    r = thesis.build(cid, force=True)
                    rebuilt.append({"cid": cid, "status": r.get("status")})
            out["rebuilt"] = rebuilt
        except Exception as e:  # noqa: BLE001
            if is_quota_error(e):
                _mark_exhausted(q, str(e))
            out["rebuilt"] = {"error": str(e)[:160]}
    return out


def run_once(*, batch_docs: int | None = None, backfill_units: int | None = None) -> dict:
    """单轮:非 LLM 工作(拉取/回填/解析)必做;LLM 抽取按探针结果做或跳。"""
    from ..config import get_settings
    from ..parsing import parse

    s = get_settings()
    batch_docs = batch_docs or s.glm_worker_batch_docs
    backfill_units = backfill_units if backfill_units is not None else s.glm_worker_backfill_units
    q = get_state("quota", {"status": "ok"})
    out: dict = {"ts": _now()}

    # Fetchy 管理面:总开关关 → 只留心跳(计数器照更新,看板可见工人仍活着)。
    # 配置读取失败 = fail-closed 跳过本轮(显式关掉的开关不得因 DB 抖动悄悄失效)。
    try:
        cfg = fetchy_config(strict=True)
    except Exception as e:  # noqa: BLE001
        out["skipped"] = f"fetchy config unreadable — failing closed: {str(e)[:120]}"
        out["quota"] = q.get("status", "ok")
        return out
    if not cfg["enabled"]:
        counters = get_state("counters")
        counters["cycles"] = int(counters.get("cycles", 0)) + 1
        counters["last_cycle_at"] = _now()
        save_state("counters", counters)
        out["skipped"] = "fetchy disabled"
        out["quota"] = q.get("status", "ok")
        return out
    stages_on = cfg["stages"]
    pin = _fetchy_pin(cfg)

    out["pulls"] = _pull_fresh(cfg)
    if stages_on.get("backfill", True):
        out["backfill"] = _backfill(backfill_units)
    if stages_on.get("parse", True):
        try:
            out["parsed_chunks"] = parse.parse_pending(limit=200)   # 本地嵌入,零 LLM
        except Exception as e:  # noqa: BLE001
            out["parsed_chunks"] = {"error": str(e)[:120]}

    # 衍生追踪指标(零 LLM,6h 节拍):从 fundamentals 算同比/增速二阶导/趋势,写回 source='derived'
    if stages_on.get("indicators", True) and _due("indicators", 6 * 3600):
        try:
            from ..research import indicators
            out["indicators"] = indicators.compute_all()
            _stamp("indicators", 6 * 3600, ok=True)
        except Exception as e:  # noqa: BLE001
            out["indicators"] = {"error": str(e)[:120]}
            _stamp("indicators", 6 * 3600, ok=False)

    # GLM 订阅门只在链首仍是 GLM 订阅模型时适用;用户显式选择其他模型(save 时已校验
    # 密钥/执行器可用)则直接放行 —— 不能再拿 GLM_SUB_API_KEY 挡别人家的模型。
    head_is_glm_sub = pin[0] in GLM_PIN
    if not stages_on.get("extract", True):
        out["extract"] = {"skipped": "fetchy: extract stage off"}
    elif head_is_glm_sub and not _sub_ready():
        # 订阅 key 缺位 = 若继续,_endpoint 会静默回退到按 token 计费 —— 直接拒绝。
        out["extract"] = {"skipped": "GLM_SUB_API_KEY not configured — refusing metered fallback"}
    elif q.get("status") == "exhausted" and head_is_glm_sub:
        # 耗尽态才发探针(避免每轮 1 次探针白耗 5h 窗的请求额度)
        if probe():
            q = _mark_ok(q)
            out["extract"], q = _llm_stage(batch_docs, q, pin)
        else:
            q = _mark_exhausted(q, "probe failed")
            out["extract"] = {"skipped": "quota exhausted — waiting for window reset"}
    elif q.get("status") == "exhausted":
        # 非 GLM 链首:probe() 只探 GLM 池,对选中模型无意义 —— 直接试一批;
        # 成功即恢复 ok(_llm_stage 成功不自动翻状态),再失败由其内部重新定性。
        out["extract"], q = _llm_stage(batch_docs, q, pin)
        if "aborted" not in out["extract"]:
            q = _mark_ok(q)
    else:
        # ok 态零探针开销:直接抽取;额度耗尽由 _llm_stage 内的错误定性翻转状态
        out["extract"], q = _llm_stage(batch_docs, q, pin)

    # 另类数据高频校正闭环(零 LLM 的信号→事件 每轮做;论点重建仅在额度 ok 时)
    if stages_on.get("alt_correction", True):
        out["alt_correct"] = _alt_correction(q, s.glm_worker_thesis_rebuilds, pin)

    # 独立抓取审计(每日):TaskClass.AUDIT 强 token 模型,**在 pinned 与 quota 门之外**——
    # 验收模型 ≠ 生产 GLM,GLM 耗尽也不影响审计(独立性的另一半)。模块级函数便于测试打桩。
    if stages_on.get("research_audit", True):
        out["research_audit"] = _research_audit_step()
    if stages_on.get("earnings", True):
        out["earnings"] = _earnings_step()

    counters = get_state("counters")
    counters["cycles"] = int(counters.get("cycles", 0)) + 1
    kg_stats = (out.get("extract") or {}).get("kg") or {}
    counters["docs_extracted"] = int(counters.get("docs_extracted", 0)) + int(
        kg_stats.get("docs", kg_stats.get("processed", 0)) or 0)
    counters["last_cycle_at"] = _now()
    save_state("counters", counters)
    out["quota"] = q["status"]
    return out


def run_daemon() -> None:
    """常驻循环:额度可用 → 每 cycle_seconds 一轮;耗尽 → 每 probe_seconds 一探。
    SIGTERM/Ctrl-C 干净退出;所有阶段幂等,重启即续。"""
    from ..config import get_settings

    s = get_settings()
    # docker stop 发 SIGTERM;转成 KeyboardInterrupt 让 sleep 中也能干净退出
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    log.info("glm-worker daemon up: pin=%s cycle=%ss probe=%ss batch=%s",
             ",".join(GLM_PIN), s.glm_worker_cycle_seconds, s.glm_worker_probe_seconds,
             s.glm_worker_batch_docs)
    while True:
        try:
            out = run_once()
            log.info("cycle done: quota=%s extract=%s backfill=%s",
                     out.get("quota"),
                     json.dumps(out.get("extract", {}), ensure_ascii=False, default=str)[:200],
                     json.dumps(out.get("backfill", {}), ensure_ascii=False, default=str)[:160])
            exhausted = out.get("quota") == "exhausted"
        except KeyboardInterrupt:
            log.info("glm-worker interrupted — exiting cleanly")
            return
        except Exception as e:  # noqa: BLE001 — 常驻进程绝不因单轮异常退出
            log.warning("cycle failed: %s", e)
            exhausted = False
        try:
            time.sleep(s.glm_worker_probe_seconds if exhausted else s.glm_worker_cycle_seconds)
        except KeyboardInterrupt:
            log.info("glm-worker interrupted during sleep — exiting cleanly")
            return


def status() -> dict:
    """CLI/看板口径:额度状态 + 计数器 + 回填游标 + 待抽取积压。"""
    try:  # 与 build_kg 的 pending 口径完全一致;init 之前降级为 None 而非崩溃
        pending = db.query(
            "SELECT count(*) AS n FROM documents "
            "WHERE kg_extracted_at IS NULL AND permission <> 'red'")
        backlog = int(pending[0]["n"]) if pending else None
    except Exception:  # noqa: BLE001
        backlog = None
    try:
        from ..ingestion import history

        backfill = history.backfill_status()
    except Exception as e:  # noqa: BLE001
        backfill = {"error": str(e)[:120]}
    cfg = fetchy_config()
    return {"quota": get_state("quota", {"status": "unknown"}),
            "counters": get_state("counters"),
            "cadence": get_state("cadence"),
            "backfill": backfill,
            "extraction_backlog_docs": backlog,
            "pin": list(_fetchy_pin(cfg)),
            "fetchy": cfg}
