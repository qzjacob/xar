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
    buf, answer, refs = "", "", []
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
                d = obj.get("data", obj)
                if isinstance(d, dict):
                    if d.get("answer"):
                        answer += d["answer"]
                    if d.get("references"):
                        refs.extend(d["references"])
    return {"answer": answer, "references": refs}


def _post(endpoint: str, payload: dict, *, stream: bool = False, timeout: float = 120) -> dict | None:
    """POST to AlphaPai. Returns the JSON body (non-stream) or the aggregated SSE dict
    (stream). Detects rate-limit codes (203/204). Never raises — logs + returns None."""
    if not available():
        return None
    url = f"{_base()}{endpoint}"
    try:
        if stream:
            with httpx.stream("POST", url, headers=_headers(),
                              content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              timeout=timeout) as r:
                r.raise_for_status()
                return _parse_sse_stream(r)
        r = httpx.post(url, headers=_headers(),
                       content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       timeout=timeout)
        r.raise_for_status()
        body = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("alphapai %s failed: %s", endpoint.rsplit("/", 1)[-1], str(e)[:160])
        return None
    if isinstance(body, dict) and body.get("code") in _RATE_LIMIT_CODES:
        log.warning("alphapai %s rate-limited (code=%s) — backing off",
                    endpoint.rsplit("/", 1)[-1], body.get("code"))
        return {"_rate_limited": True, "code": body.get("code")}
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
                start: str | None = None) -> int:
    if not available():
        return 0
    payload = {"query": query, "isCutOff": True,
               "recallType": recall_types if recall_types is not None else _recall_types()}
    if start:
        payload["startTime"] = start
    out = _post("/alpha/open-api/v1/paipai/recall-data", payload)
    if not out or out.get("_rate_limited") or out.get("code") != 200000:
        return 0
    return _save_recall(out.get("data") or [], company_id=company_id, scope=scope)


def _since() -> str:
    days = get_settings().alphapai_lookback_days
    return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()


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
