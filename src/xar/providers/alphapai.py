"""Alpha派 (AlphaPai, 讯兔科技) — professional CN/HK/US investment-research SaaS as an
XAR alternative-data source.

Talks to the AlphaPai Open API (`open-api.rabyte.cn`, `app-agent: <key>` header, POST
JSON; agent/qa endpoints stream SSE). Two surfaces are ingested, both landing in
`documents(source='alphapai', grey)` so they flow the same expert/KG道 as gangtise/
aifinmarket:
    recall-data     -> 原始投研文档: 路演纪要 / 券商研报 / 点评 / 公告 / 三方研报 / 社媒
    stock/agent     -> 合成投研: 公司一页纸(2) / 投资逻辑(7)

doc_type maps onto the existing `ontology/research_docs` vocabulary (broker_report /
meeting_minutes / announcement / news / one_pager / investment_logic), so research-typed
docs route through the expert 研报 prompt automatically. Gated by ALPHAPAI_API_KEY; a
no-op when unset. Rate-limit codes (203 daily / 204 system) are detected and backed off.
"""
from __future__ import annotations

import codecs
import datetime
import json
import re
import threading
import time
from zoneinfo import ZoneInfo

import httpx

from ..config import get_settings
from ..ingestion.registry import company_by_id
from .base import log

_CJK = re.compile(r"[一-鿿]+")
_PUB_RE = re.compile(r"发布时间[为:：\s]*([0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2}(?:[ T][0-9:]{4,8})?)")
_INST_RE = re.compile(r"机构[:：]\s*([^,，、\n]+)")
_INDUSTRY_RE = re.compile(r"行业[:：]\s*([^,，\n]+)")
_TITLE_RE = re.compile(r"标题[:：]\s*([^\n]+)")

# AlphaPai recall type / agentMode → XAR doc_type (research_docs vocabulary)
_DOCTYPE_MAP = {
    "roadShow": "meeting_minutes", "roadShow_ir": "meeting_minutes", "roadShow_us": "meeting_minutes",
    "report": "broker_report", "foreign_report": "broker_report", "third_report": "broker_report",
    "comment": "broker_report", "ann": "announcement", "social_media": "news", "vps": "news",
    "qa": "news",
}
_AGENT_DOCTYPE = {2: "one_pager", 7: "investment_logic", 1: "broker_report",
                  8: "peer_comparison", 11: "one_pager"}
_AGENT_QTEMPLATE = {2: "{name}（{code}）的公司一页纸", 7: "{name}（{code}）的公司投资逻辑"}
_RATE_LIMIT_CODES = {203, 204}
# 42900(≈HTTP 429)= **未文档化的短窗限流**,实测:连打 1~4 次即触发,恢复 ≈10s,4s 间隔仍失败
# → 可持续速率约 1 次/10s。此前它不在 _RATE_LIMIT_CODES 里,`pull_recall` 只看 code!=200000 就
# **静默返回 0**,链路却继续猛打 → alphapai 的量一直被这道看不见的墙压住(每轮只成功几次)。
# 现在:节流预防(_throttle)+ 识别后原地重试(_RL_RETRIES 次),仍失败才短退避;绝不当作当日耗尽(203)。
_SHORT_RATE_LIMIT_CODES = {42900, 429}
_throttle_lock = threading.Lock()
_last_call = [0.0]


def _throttle() -> None:
    """全局最小调用间隔(默认 11s > 实测 10s 恢复窗),防触 42900。进程内串行,与 aifinmarket 同型。"""
    iv = get_settings().alphapai_min_interval_seconds
    if iv <= 0:
        return
    with _throttle_lock:
        wait = iv - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()

# ── 额度状态(进程内;只有 glmworker 的抓取链驱动本源)────────────────────────────
# AlphaPai(讯兔/rabyte.cn)按**国内日历日**重置额度 → 日界用 Asia/Shanghai(容器跑 UTC,
# 若按 UTC 日界,16:00 UTC 刷新的额度会闲置至多 8h)。203=用户当日超限(锁死当日),
# 204=系统繁忙(短退避,非当日耗尽)。fetch_chain 读 quota_exhausted()/quota_backing_off()
# 决定是否放弃 alphapai 段 fallback 到 gangtise;pull_* 返回类型不变(链读谓词不读返回值)。
_CN_TZ = ZoneInfo("Asia/Shanghai")
_QUOTA = {"cn_date": None, "daily_exhausted": False, "backoff_until": 0.0, "last_code": None}


def _cn_today() -> str:
    return datetime.datetime.now(_CN_TZ).date().isoformat()


def _quota_roll() -> None:
    """沪日切换即重置**进程内镜像**(权威在 provider_quota 表,那边换日即换行、无需重置)。"""
    if _QUOTA["cn_date"] != _cn_today():
        _QUOTA.update({"cn_date": _cn_today(), "daily_exhausted": False,
                       "backoff_until": 0.0, "last_code": None})


# 谓词读的 TTL 缓存:热路径每次调用都查库没必要,而跨进程传播延迟几秒无所谓
# (dagster 夜批得知 glmworker 吃了 203 晚 ≤_SNAP_TTL 秒,代价仅是几次空转调用)。
_SNAP_TTL = 8.0
_snap_cache: dict = {"at": 0.0, "row": {}}


def _row() -> dict:
    """今日本源(单席位)的权威行;读不到则回落进程内镜像 —— **fail-open**。

    额度门是优化信号(省调用),不是预算帽(省钱):DB 抖动时必须放行继续抓,
    绝不能因为读不到额度状态就把抓取链停掉。
    """
    now = time.time()
    if now - float(_snap_cache["at"]) < _SNAP_TTL:
        return _snap_cache["row"]
    from ..storage import quota as q
    row = (q.snapshot("alphapai") or {}).get("-") or {}
    _snap_cache.update({"at": now, "row": row})
    return row


def _invalidate() -> None:
    """自己刚写过 → 立刻让缓存失效,保证本进程读到的是自己写后的值。"""
    _snap_cache["at"] = 0.0


def quota_exhausted() -> bool:
    """当日额度已耗尽(收到 203)——本沪日剩余时间 alphapai 段应让位 fallback。

    ⚠️ 权威在 `provider_quota` 表,不再是进程内变量(2026-08-02)。此前 glmworker
    一天重启 14 次、每次都忘掉今天已经 203,于是抓取链重新把 alphapai 排到链首、
    继续打已耗尽的付费 API —— fetch_chain 的 drain_first(榨干才交棒)整个语义
    都建立在这个活不过重启的变量上。落库之后重启即续。
    """
    _quota_roll()
    # ⚠️ **取较坏者**,不是「有行就只信行」(2026-08-02 审核修正)。
    # 原写法是 `if row: return row['exhausted']` —— 一旦今日行因为别的原因已经存在
    # (例如上午一次 204 建了行),而下午那次 203 的落库恰好失败,镜像里的 True 就被
    # 彻底丢弃、谓词返回 False,drain_first 会把 alphapai 继续吊在链首打已耗尽的付费 API。
    # 那正是本轮要修的原始事故原样复现 —— 「写失败只退化为改造前现状」在那个写法下不成立。
    # 两个来源都是「耗尽」的证据,任一为真即为真。
    return bool(_row().get("exhausted")) or bool(_QUOTA["daily_exhausted"])


def quota_backing_off() -> bool:
    """系统繁忙(204/42900)短退避中——暂停但不判当日耗尽,退避到期自动恢复。"""
    _quota_roll()
    # 同上:取较坏者。DB 说不在退避,不代表本进程刚写失败的那次退避不存在。
    return bool(_row().get("backing_off")) or time.time() < float(_QUOTA["backoff_until"])


def _persist_exhausted(code) -> None:
    """203 落库。写失败只记日志 —— 退化为改造前的「仅进程内」现状,不制造新的失败模式。"""
    try:
        from ..storage import quota as q
        q.mark_exhausted("alphapai", code=str(code))
        _invalidate()
    except Exception as e:  # noqa: BLE001
        log.warning("alphapai 额度耗尽状态落库失败(仅进程内生效): %s", str(e)[:120])


def _persist_backoff(seconds: float, code) -> None:
    try:
        from ..storage import quota as q
        q.set_backoff("alphapai", seconds=float(seconds), code=str(code))
        _invalidate()
    except Exception as e:  # noqa: BLE001
        log.warning("alphapai 退避状态落库失败(仅进程内生效): %s", str(e)[:120])


def _reset_quota_state() -> None:
    """清空额度状态(测试用;进程内状态需在用例间复位防泄漏)。

    ⚠️ 必须连带清 `_snap_cache` —— 它也是模块级进程内状态。忘了这一条,上一个用例
    缓存的「已耗尽」会泄漏进下一个用例,表现为 `_post` 莫名其妙秒返回 203(实测踩到)。
    「新增了进程内状态就要在复位钩子里一起清」是这类模块的固定义务。
    """
    _QUOTA.update({"cn_date": None, "daily_exhausted": False,
                   "backoff_until": 0.0, "last_code": None})
    _snap_cache.update({"at": 0.0, "row": {}})


def available() -> bool:
    return bool(get_settings().alphapai_api_key)


def _base() -> str:
    return (get_settings().alphapai_base_url or "https://open-api.rabyte.cn").rstrip("/")


def _headers() -> dict:
    return {"app-agent": get_settings().alphapai_api_key,
            "Content-Type": "application/json; charset=utf-8"}


def _parse_sse_stream(r: httpx.Response) -> dict:
    """Aggregate an AlphaPai SSE response → {"answer": str, "references": list}.
    Incremental utf-8 decode avoids splitting multi-byte CJK across chunk boundaries."""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    buf, answer, refs, code = "", "", [], None
    for chunk in r.iter_bytes(4096):
        if not chunk:
            continue
        buf += decoder.decode(chunk)
        while "\n\n" in buf:
            event, buf = buf.split("\n\n", 1)
            event = event.strip()
            if not event.startswith("data:"):
                continue
            line = event[5:].strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                c = obj.get("code")                          # 带内限流事件(顶层或 data.code)
                if c is None and isinstance(obj.get("data"), dict):
                    c = obj["data"].get("code")
                # 含 42900:agent(SSE)端点把「超过流量限制」作为**带内事件**返回,若不识别则
                # answer 为空 → pull_agent 静默返回 0(与 recall 侧同一类静默零 bug)。
                if c in _RATE_LIMIT_CODES or c in _SHORT_RATE_LIMIT_CODES:
                    code = c
                d = obj.get("data", obj)
                if isinstance(d, dict):
                    if d.get("answer"):
                        answer += d["answer"]
                    if d.get("references"):
                        refs.extend(d["references"])
    out = {"answer": answer, "references": refs}
    if code is not None:
        out["code"] = code
    return out


def _post(endpoint: str, payload: dict, *, stream: bool = False, timeout: float = 120,
          _attempt: int = 0) -> dict | None:
    """POST to AlphaPai. Returns the JSON body (non-stream) or the aggregated SSE dict
    (stream). Detects rate-limit codes (203 当日/204 系统/42900 短窗)。Never raises — logs + returns None.
    短窗限流(42900)先节流预防,命中则退避重试 `_RL_RETRIES` 次(不算当日耗尽)。"""
    if not available():
        return None
    # 当日已耗尽(203)/退避中(204)→ 秒变 no-op,零 HTTP(链读谓词,pull_* 快速返回 0)。
    if quota_exhausted() or quota_backing_off():
        return {"_rate_limited": True, "code": _QUOTA["last_code"] or 203}
    _throttle()                    # 全局最小间隔:防触 42900(实测 1 次/10s 才可持续)
    url = f"{_base()}{endpoint}"
    try:
        if stream:
            with httpx.stream("POST", url, headers=_headers(),
                              content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              timeout=timeout) as r:
                r.raise_for_status()
                if "text/event-stream" in r.headers.get("content-type", ""):
                    sse = _parse_sse_stream(r)
                    sc = sse.get("code")
                    if sc in _RATE_LIMIT_CODES or sc in _SHORT_RATE_LIMIT_CODES:
                        body = {"code": sc}          # 带内 SSE 限流事件 → 走下方统一 code 判定
                    else:
                        return sse
                else:                                          # 非 SSE(限流/错误 JSON 体)→ 统一 code 判定
                    r.read()
                    body = r.json()
        else:
            r = httpx.post(url, headers=_headers(),
                           content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                           timeout=timeout)
            r.raise_for_status()
            body = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("alphapai %s failed: %s", endpoint.rsplit("/", 1)[-1], str(e)[:160])
        return None
    code = body.get("code") if isinstance(body, dict) else None
    if code in _SHORT_RATE_LIMIT_CODES:                  # 42900:短窗限流 → 退避重试,不算当日耗尽
        retries = get_settings().alphapai_ratelimit_retries
        if _attempt < retries:
            nap = get_settings().alphapai_ratelimit_sleep_seconds
            log.info("alphapai %s 短窗限流(code=%s)→ 等 %ss 重试(%d/%d)",
                     endpoint.rsplit("/", 1)[-1], code, nap, _attempt + 1, retries)
            time.sleep(nap)
            return _post(endpoint, payload, stream=stream, timeout=timeout, _attempt=_attempt + 1)
        _quota_roll()
        _QUOTA["last_code"] = code
        nap2 = get_settings().alphapai_ratelimit_sleep_seconds
        _QUOTA["backoff_until"] = time.time() + nap2
        _persist_backoff(nap2, code)               # 落库:重启后仍知道在退避
        log.warning("alphapai %s 短窗限流重试仍失败(code=%s)→ 短退避",
                    endpoint.rsplit("/", 1)[-1], code)
        return {"_rate_limited": True, "code": code}
    if code in _RATE_LIMIT_CODES:
        _quota_roll()
        _QUOTA["last_code"] = code
        if code == 203:                                  # 用户当日超限 → 锁死当日
            _QUOTA["daily_exhausted"] = True
            _persist_exhausted(code)                     # 落库:**重启后仍记得今天已耗尽**
        else:                                            # 204 系统繁忙 → 短退避
            secs = get_settings().alphapai_backoff_seconds
            _QUOTA["backoff_until"] = time.time() + secs
            _persist_backoff(secs, code)
        log.warning("alphapai %s rate-limited (code=%s) — %s",
                    endpoint.rsplit("/", 1)[-1], code,
                    "当日耗尽" if code == 203 else "退避")
        return {"_rate_limited": True, "code": code}
    return body


def _name(company_id: str) -> str | None:
    c = company_by_id(company_id)
    if not c:
        return None
    m = _CJK.search(c.get("name", ""))
    if m:
        return m.group(0)
    cjk_alias = next((a for a in c.get("aliases", []) if _CJK.search(a)), None)
    return cjk_alias or c.get("name")


def _cn_stock(company_id: str) -> dict | None:
    """{'code','name'} for a CN A-share (agent needs a valid AlphaPai code); else None."""
    c = company_by_id(company_id)
    if not c:
        return None
    code = next((t for t in c.get("tickers", []) if t.endswith((".SZ", ".SS", ".SH"))), None)
    if not code:
        return None
    return {"code": code.replace(".SS", ".SH"), "name": _name(company_id) or code}


def _pub(context_info: str) -> datetime.datetime | None:
    m = _PUB_RE.search(context_info or "")
    if not m:
        return None
    raw = m.group(1).replace("/", "-").replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None


def _save_recall(items: list[dict], *, company_id: str | None, scope: str) -> int:
    from ..ingestion.base import Doc, save

    n = 0
    for it in items:
        atype = it.get("type") or ""
        chunks = it.get("chunks") or []
        ctx = it.get("contextInfo") or ""
        text = (ctx + "\n" + "\n".join(chunks)).strip()
        if it.get("type") == "qa":                       # qa 召回:Q/A 拼进正文
            text = (ctx + "\nQ: " + (it.get("contextText") or "") +
                    "\nA: " + (it.get("answer") or "")).strip()
        if len(text) < 40:
            continue
        vid = it.get("id") or ""
        title = (_TITLE_RE.search(ctx).group(1)[:120] if _TITLE_RE.search(ctx)
                 else f"{_DOCTYPE_MAP.get(atype, atype)} · {text[:30]}")
        meta = {"provider": "alphapai", "alphapai_type": atype, "scope": scope}
        if _INST_RE.search(ctx):
            meta["institution"] = _INST_RE.search(ctx).group(1).strip()
        if _INDUSTRY_RE.search(ctx):
            meta["industry"] = _INDUSTRY_RE.search(ctx).group(1).strip()
        save(Doc(company_id=company_id, source="alphapai",
                 doc_type=_DOCTYPE_MAP.get(atype, "news"),
                 title=title, text=text[:120_000], published_at=_pub(ctx),
                 permission="grey", license_tag="alphapai-research-extracted-facts-self-use",
                 doc_id=f"alphapai:{atype}:{vid}", meta=meta))
        n += 1
    return n


def _recall_types() -> list[str]:
    csv = (get_settings().alphapai_recall_types or "").strip()
    return [t.strip() for t in csv.split(",") if t.strip()]


def pull_recall(query: str, recall_types: list[str] | None = None, *,
                company_id: str | None = None, scope: str = "company",
                start: str | None = None, end: str | None = None) -> int:
    """recall 一次并落库。`start`/`end` = API 的 startTime/endTime(实测**真的按窗过滤**:
    2025-10-01..2025-11-01 只回该窗内文档)。窗口是量的关键杠杆 —— 不带窗时同一 query 只回 ~28 篇
    (跨整年),按月切窗则每窗各回 ~20 篇,故「过去一年逐月新→旧」能把覆盖放大一个量级。"""
    if not available():
        return 0
    payload = {"query": query, "isCutOff": True,
               "recallType": recall_types if recall_types is not None else _recall_types()}
    if start:
        payload["startTime"] = start
    if end:
        payload["endTime"] = end
    out = _post("/alpha/open-api/v1/paipai/recall-data", payload)
    if not out or out.get("_rate_limited") or out.get("code") != 200000:
        return 0
    return _save_recall(out.get("data") or [], company_id=company_id, scope=scope)


def _since() -> str:
    days = get_settings().alphapai_lookback_days
    return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()


def has_cjk_name(company_id: str) -> bool:
    """该公司是否可被 alphapai recall 寻址(recall 检索词是中文名驱动;无中文名的纯美股
    查询只会白耗额度)。fetch_chain 用它把 alphapai 段的公司清单收窄到本源能服务的范围。"""
    nm = _name(company_id)
    return bool(nm and _CJK.search(nm))


def _minutes_types() -> list[str]:
    csv = (get_settings().alphapai_minutes_types or "").strip()
    return [t.strip() for t in csv.split(",") if t.strip()]


def pull_minutes(company_id: str, *, start: str | None = None, end: str | None = None) -> int:
    """纪要专用 recall(roadShow/roadShow_ir/roadShow_us → meeting_minutes)。fetch_chain
    的固定首要任务:相关性高→低逐公司拉纪要,start/end 控制新→旧窗口。"""
    name = _name(company_id)
    if not name or not available():
        return 0
    return pull_recall(f"{name} 路演 调研 电话会 交流 纪要", _minutes_types(),
                       company_id=company_id, scope="company", start=start or _since(), end=end)


def pull_company_window(company_id: str, *, start: str, end: str) -> int:
    """某公司在指定时间窗内的**全类型** recall(研报/纪要/点评/公告/社媒…)。
    过去一年逐窗回溯的公司维工作单元。"""
    name = _name(company_id)
    if not name or not available():
        return 0
    return pull_recall(f"{name} 研报 纪要 点评 业绩 观点", company_id=company_id,
                       scope="company", start=start, end=end)


def pull_theme_window(query: str, *, scope: str = "industry",
                      start: str | None = None, end: str | None = None) -> int:
    """主题维 recall(行业/宏观/策略/资金流),可带窗。scope 落进 meta 供分轨观测。"""
    if not available():
        return 0
    return pull_recall(query, scope=scope, start=start or _since(), end=end)


def pull_company(company_id: str) -> int:
    name = _name(company_id)
    if not name or not available():
        return 0
    return pull_recall(f"{name} 最新 业绩 进展 观点", company_id=company_id,
                       scope="company", start=_since())


def pull_theme(theme: str) -> int:
    if not available():
        return 0
    return pull_recall(f"{theme} 产业链 需求 进展 观点", scope="industry", start=_since())


def pull_agent(company_id: str, mode: int) -> int:
    from ..ingestion.base import Doc, save

    stock = _cn_stock(company_id)
    if not stock or not available() or mode not in _AGENT_QTEMPLATE:
        return 0
    question = _AGENT_QTEMPLATE[mode].format(name=stock["name"], code=stock["code"])
    payload = {"agentMode": mode, "question": question, "stock": stock,
               "template": 0, "templateText": ""}
    out = _post("/alpha/open-api/v1/paipai/stock/agent", payload, stream=True, timeout=300)
    answer = (out or {}).get("answer") or ""
    if len(answer.strip()) < 80:
        return 0
    save(Doc(company_id=company_id, source="alphapai", doc_type=_AGENT_DOCTYPE.get(mode, "one_pager"),
             title=question, text=answer[:120_000], published_at=datetime.datetime.now(),
             permission="grey", license_tag="alphapai-research-extracted-facts-self-use",
             doc_id=f"alphapai:agent{mode}:{stock['code']}", meta={"provider": "alphapai",
             "scope": "company", "agent_mode": mode}))
    return 1


def pull(company_id: str) -> dict:
    # recall only (fast);agent 一页纸/投资逻辑(慢 SSE 合成)只在 daily sweep 里跑,不拖 on-demand/_MARKET。
    if not available():
        return {}
    return {"recall": pull_company(company_id)}


def pull_research_sweep(company_universe: list[str] | None = None) -> dict:
    """激进全量抓取(数据可能过期→尽快落库):公司维 recall + 主题维 recall + 核心公司 agent 一页纸/投资逻辑。
    公司维按 company_universe(通常一个分片)。返回各维计数。"""
    if not available():
        return {"skipped": "alphapai disabled"}
    from ..ingestion.registry import COMPANIES, THEMES

    s = get_settings()
    counts = {"company_recall": 0, "theme_recall": 0, "agent": 0}
    ids = company_universe if company_universe is not None else [c["id"] for c in COMPANIES]
    agent_modes = [int(m) for m in (s.alphapai_agent_modes or "").split(",") if m.strip().isdigit()]

    for cid in ids:
        try:
            counts["company_recall"] += pull_company(cid)
        except Exception as e:  # noqa: BLE001
            log.warning("alphapai company %s: %s", cid, str(e)[:120])
        if _cn_stock(cid):
            for mode in agent_modes:
                try:
                    counts["agent"] += pull_agent(cid, mode)
                except Exception as e:  # noqa: BLE001
                    log.warning("alphapai agent %s/%s: %s", cid, mode, str(e)[:120])

    for tid, t in THEMES.items():
        try:
            counts["theme_recall"] += pull_theme(t.get("nameCn") or tid)
        except Exception as e:  # noqa: BLE001
            log.warning("alphapai theme %s: %s", tid, str(e)[:120])

    log.info("alphapai research sweep: %s", counts)
    return {"counts": counts}
