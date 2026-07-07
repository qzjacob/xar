"""Gangtise open-insight 非标语义抓取器(零 LLM)。

券商研报 / 会议·业绩会·专家纪要 / 经营讨论 MD&A 的 list 元数据(保守策略:brief +
精华段落 essence[],不下载全文,零信用消耗)→ ingestion.base.Doc→save(doc_type ∈
ontology.research_docs 注册表,permission='grey',doc_id=来源天然主键)。券商评级走零 LLM
确定性第二遍(镜像 cninfo.parse_research_ratings)→ analyst_ratings。security_clue 作为
每日"变更雷达"返回目标集,**不落库**(裁决 2)。

全局日期窗扫描(不逐公司!一页 50 行覆盖全市场),securityList 反解 registry 名单过滤——
避免逐公司重复扫同一批全局数据。CN A股/港股焦点;字段名以 Gangtise 文档为准,真机对照后修正
(附录 H;前科:资产负债表字段位错)。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ...ingestion.base import Doc, save
from ...ingestion.cninfo import _rating_bucket
from ...ingestion.registry import COMPANIES, company_by_id
from ...logging import get_logger
from ...ontology.research_docs import DOCS_BY_TYPE
from ...storage import db, structured
from . import client

log = get_logger("xar.gangtise.insight")

# gtsCode 反解 registry:key = "{numeric}:{market}" 避 9999.SS vs 09999.HK 数字撞车(会话前科)。
_EXCH = {"SS": "SH", "SH": "SH", "SZ": "SZ", "BJ": "BJ", "HK": "HK"}
_SEC_INDEX: dict[str, str] | None = None


def _key(code: str | None) -> str | None:
    parts = str(code or "").split(".")
    if len(parts) != 2:
        return None
    num, suf = parts[0].lstrip("0"), _EXCH.get(parts[1].upper())
    return f"{num}:{suf}" if (num and suf) else None


def _sec_index() -> dict[str, str]:
    global _SEC_INDEX
    if _SEC_INDEX is None:
        idx: dict[str, str] = {}
        for c in COMPANIES:
            for t in (c.get("tickers") or []):
                k = _key(t)
                if k:
                    idx.setdefault(k, c["id"])
        _SEC_INDEX = idx
    return _SEC_INDEX


def _company_for_security(sec) -> str | None:
    """securityList 元素 → registry cid(数字段+交易所双匹配)。评审 #11:元素也可能是
    代码字符串(如 '600519.SH')而非 dict——都容忍,非 dict 非 str 一律跳过不炸。"""
    if isinstance(sec, dict):
        code = sec.get("securityCode") or sec.get("gtsCode") or sec.get("code")
    elif isinstance(sec, str):
        code = sec
    else:
        return None
    return _sec_index().get(_key(code) or "")


def _companies_in(row: dict) -> list[str]:
    out: list[str] = []
    for sec in (row.get("securityList") or []):
        cid = _company_for_security(sec)
        if cid and cid not in out:
            out.append(cid)
    return out


def _pub(ms) -> datetime | None:
    """时间戳解析:13 位毫秒 / 10 位秒 / **8 位 yyyyMMdd** / 'yyyy-MM-dd' / 'yyyy/MM/dd'。

    评审 #10:8 位 yyyyMMdd(如 20990331)不能当 Unix 秒(会变 1970)——先按紧凑日期解析。"""
    if ms in (None, ""):
        return None
    try:
        n = int(ms)
        if 10_000_000 <= n <= 99_999_999:     # 8 位 yyyyMMdd
            s = str(n)
            return datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc)
        if n > 10_000_000_000:                # 13 位 ms
            return datetime.fromtimestamp(n / 1000, tz=timezone.utc)
        return datetime.fromtimestamp(n, tz=timezone.utc)  # 10 位秒
    except (TypeError, ValueError):
        s = str(ms)[:10].replace("/", "-")
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(s.replace("-", "") if fmt == "%Y%m%d" else s,
                                         fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None


def _ymd(ms: int) -> str:
    return (datetime.fromtimestamp(ms / 1000, tz=timezone.utc)).strftime("%Y-%m-%d")


def _save(*, doc_type: str, vendor_id: str, cid: str | None, title: str, text: str,
          pub, meta: dict) -> None:
    spec = DOCS_BY_TYPE[doc_type]
    kind = ("report" if doc_type == "broker_report"
            else "mgmt" if doc_type == "mgmt_discussion"
            else "summary")
    save(Doc(company_id=cid, source=spec.vendor, doc_type=doc_type,
             doc_id=f"gangtise:{kind}:{vendor_id}", title=title[:400], text=text,
             published_at=pub, permission=spec.permission, license_tag=spec.license_tag,
             meta=meta))


# ── 券商研报 ────────────────────────────────────────────────────────────────────
def pull_broker_reports(*, start_ms: int, end_ms: int, max_pages: int = 2) -> dict:
    payload = {"startDate": _ymd(start_ms), "endDate": _ymd(end_ms), "keyword": "",
               "categoryList": [], "ratingList": []}
    seen = saved = 0
    for page in client.pages(client.BROKER_REPORT_LIST_URL, payload, max_pages=max_pages):
        for r in page:
            seen += 1
            vid = str(r.get("reportId") or r.get("id") or "")
            cids = _companies_in(r)
            if not vid or not cids:          # 只留命中 registry 名单的报告(评审 #3)
                continue
            pub = _pub(r.get("publishTime") or r.get("reportDate"))
            brief = str(r.get("brief") or r.get("summary") or "")
            title = str(r.get("title") or "")
            for cid in cids:
                _save(doc_type="broker_report", vendor_id=f"{vid}:{cid}",
                      cid=cid, title=title, text=(title + "\n" + brief).strip(), pub=pub,
                      meta={"reportId": vid, "publisher": r.get("publisher"),
                            "category": r.get("category"), "llmTagList": r.get("llmTagList"),
                            "rating": r.get("rating"), "ratingChange": r.get("ratingChange"),
                            "targetPrice": r.get("targetPrice") or r.get("target")})
                saved += 1
    out = {"seen": seen, "saved": saved}
    log.info("gangtise broker-reports: %s", out)
    return out


# ── 会议·业绩会·专家纪要 ─────────────────────────────────────────────────────────
def pull_minutes(*, start_ms: int, end_ms: int, max_pages: int = 2) -> dict:
    payload = {"startDate": _ymd(start_ms), "endDate": _ymd(end_ms), "keyword": "",
               "categoryList": [], "marketList": []}
    seen = saved = 0
    for page in client.pages(client.SUMMARY_LIST_URL, payload, max_pages=max_pages):
        for r in page:
            seen += 1
            vid = str(r.get("summaryId") or r.get("id") or "")
            cids = _companies_in(r)
            if not vid or not cids:          # 只留命中 registry 名单的纪要(评审 #3)
                continue
            roles = r.get("participantRoleList") or []
            doc_type = "expert_minutes" if ("expert" in roles or r.get("guest")) else "meeting_minutes"
            pub = _pub(r.get("publishTime") or r.get("summaryTime"))
            title = str(r.get("title") or r.get("translatedTitle") or "")
            brief = str(r.get("brief") or r.get("translatedBrief") or "")
            essence = "\n".join(str(e.get("content") or "") for e in (r.get("essence") or [])
                                if isinstance(e, dict))
            text = "\n".join(p for p in (title, brief, essence) if p).strip()
            for cid in cids:
                _save(doc_type=doc_type, vendor_id=f"{vid}:{cid}",
                      cid=cid, title=title, text=text, pub=pub,
                      meta={"summaryId": vid, "guest": r.get("guest"),
                            "institutionList": r.get("institutionList"),
                            "categoryList": r.get("categoryList"),
                            "marketList": r.get("marketList"), "roles": roles,
                            "sourceName": r.get("sourceName")})
                saved += 1
    out = {"seen": seen, "saved": saved}
    log.info("gangtise minutes: %s", out)
    return out


# ── 经营讨论 MD&A(按 reportDate 取历史季度,不受账户历史窗限制)──────────────────
def pull_mgmt_discussion(company_id: str, report_date: str) -> int:
    from .__init__ import gts_code
    code = gts_code(company_id)
    if not code:
        return 0
    c = company_by_id(company_id)
    name = (c.get("name") if c else "") or code
    n = 0
    for origin, url in (("ec", client.MGMT_DISCUSS_EC_URL), ("ann", client.MGMT_DISCUSS_ANN_URL)):
        data = client.post(url, {"securityCode": code, "reportDate": report_date,
                                 "discussionDimension": "all"})
        text = (data or {}).get("content") if isinstance(data, dict) else None
        if not text or not str(text).strip():
            continue
        _save(doc_type="mgmt_discussion", vendor_id=f"{code}:{report_date}:{origin}",
              cid=company_id, title=f"{name} · 经营讨论 {report_date} ({origin})",
              text=str(text), pub=_pub(report_date),
              meta={"code": code, "reportDate": report_date, "origin": origin})
        n += 1
    return n


# ── 投研线索(变更雷达,不落库)───────────────────────────────────────────────────
def pull_clues(*, start_ms: int, end_ms: int, securities: list[str] | None = None) -> dict:
    payload = {"queryMode": "bySecurity" if securities else "byIndustry",
               "securities": securities or ["all"], "pageFrom": 0, "pageSize": 500,
               "startTime": start_ms, "endTime": end_ms,
               "source": ["researchReport", "conference", "announcement", "view"]}
    data = client.post(client.SECURITY_CLUE_URL, payload)
    rows = client.rows(data) or ((data or {}).get("list") or [])
    targets: list[tuple[str, str]] = []
    counts: dict[str, int] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        src = str(r.get("source") or "")
        counts[src] = counts.get(src, 0) + 1
        cid = _company_for_security(r) or _sec_index().get(_key(r.get("securityCode")) or "")
        if not cid:
            for sec in (r.get("securityList") or []):
                cid = _company_for_security(sec)
                if cid:
                    break
        if cid and (cid, src) not in targets:
            targets.append((cid, src))
    return {"targets": targets, "counts": counts, "rows": len(rows)}


# ── 券商评级第二遍(零 LLM 确定性;镜像 cninfo.parse_research_ratings)──────────────
def parse_broker_ratings(company_id: str | None = None) -> dict:
    where = "WHERE source='gangtise' AND doc_type='broker_report'"
    params: list = []
    if company_id:
        where += " AND company_id=%s"
        params.append(company_id)
    rows = db.query(f"SELECT company_id, published_at, meta FROM documents {where} "
                    "AND company_id IS NOT NULL", params)
    # (company_id, day) → 桶计数 + 目标价列表
    agg: dict[tuple, dict] = {}
    for r in rows:
        cid = r["company_id"]
        day = (r["published_at"].date() if r.get("published_at") else None)
        if day is None:
            continue
        meta = r["meta"] if isinstance(r["meta"], dict) else {}
        bucket = _rating_bucket(str(meta.get("rating") or ""))
        if not bucket:
            continue
        a = agg.setdefault((cid, day), {"buckets": {}, "targets": []})
        a["buckets"][bucket] = a["buckets"].get(bucket, 0) + 1
        tp = meta.get("targetPrice")
        try:
            if tp is not None:
                a["targets"].append(float(tp))
        except (TypeError, ValueError):
            pass
    n = 0
    for (cid, day), a in agg.items():
        b = a["buckets"]
        tps = a["targets"]
        structured.upsert_rating(
            cid, day, source="gangtise",
            strong_buy=b.get("strong_buy"), buy=b.get("buy"), hold=b.get("hold"),
            sell=b.get("sell"), strong_sell=b.get("strong_sell"),
            pt_mean=(round(sum(tps) / len(tps), 4) if tps else None),
            pt_high=(max(tps) if tps else None), pt_low=(min(tps) if tps else None),
            meta={"n_reports": sum(b.values())})
        n += 1
    return {"companies_days": n}


def default_window(days: int = 3) -> tuple[int, int]:
    """最近 N 天的 (start_ms, end_ms) —— fresh_sweep 的默认增量窗。"""
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=days)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _quarter_ends(n: int, before: date | None = None) -> list[str]:
    """最近 n 个季度末 yyyy-MM-dd(最新在前),供 MD&A 回填。"""
    d = before or date.today()
    ends = [date(y, m, dd) for y in range(d.year, d.year - 4, -1)
            for (m, dd) in ((12, 31), (9, 30), (6, 30), (3, 31))]
    return [e.isoformat() for e in ends if e <= d][:n]
