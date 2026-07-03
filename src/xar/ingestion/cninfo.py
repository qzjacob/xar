"""China A-share ingestion via AKShare (optional dep: `pip install '.[cn]'`).

- cninfo statutory disclosures: GREEN (mandatory public filings).
- Sell-side research: METADATA ONLY (title/institution/rating/target) — RED for
  full text; never ingest broker PDF bodies. (design §4 highest-risk pillar)
"""
from __future__ import annotations

import re
from datetime import datetime

from ..logging import get_logger
from .base import Doc, polite, save
from .registry import company_by_id

log = get_logger("xar.ingest.cninfo")


def _ak():
    try:
        import akshare as ak

        return ak
    except Exception:
        log.warning("akshare not installed; `pip install '.[cn]'` for CN coverage")
        return None


def ingest_company(company_id: str, limit: int = 20) -> list[str]:
    company = company_by_id(company_id)
    if not company or not company.get("cn_code"):
        return []
    ak = _ak()
    if ak is None:
        return []
    code = company["cn_code"]
    ids: list[str] = []
    ids += _disclosures(ak, company_id, code, limit)
    ids += _research_meta(ak, company_id, code, limit)
    log.info("cninfo: %s -> %d docs", code, len(ids))
    return ids


def _disclosures(ak, company_id: str, code: str, limit: int) -> list[str]:
    polite("cninfo")
    out: list[str] = []
    try:
        df = ak.stock_zh_a_disclosure_report_cninfo(symbol=code, market="沪深京")
    except Exception as e:
        log.warning("cninfo disclosures failed (%s): %s", code, e)
        return out
    for _, row in df.head(limit).iterrows():
        r = {k: row[k] for k in df.columns}
        title = str(r.get("公告标题") or r.get("title") or "")
        url = str(r.get("公告链接") or r.get("url") or "")
        date = _date(r.get("公告时间") or r.get("date"))
        doc = Doc(
            company_id=company_id, source="cninfo", doc_type="announcement",
            title=title, text=title, url=url, published_at=date,
            permission="green", license_tag="cn-mandatory-disclosure",
            meta={k: str(v) for k, v in r.items()},
        )
        out.append(save(doc))
    return out


def _research_meta(ak, company_id: str, code: str, limit: int) -> list[str]:
    """Research report METADATA only — title/institution/rating/target price."""
    polite("eastmoney")
    out: list[str] = []
    try:
        df = ak.stock_research_report_em(symbol=code)
    except Exception as e:
        log.warning("research meta failed (%s): %s", code, e)
        return out
    for _, row in df.head(limit).iterrows():
        r = {k: str(row[k]) for k in df.columns}
        title = r.get("报告名称") or r.get("title") or ""
        # Store only structured metadata as text; NEVER the broker PDF body.
        summary = " | ".join(
            f"{k}:{r[k]}" for k in r
            if any(t in k for t in ("机构", "评级", "目标", "盈利", "日期", "报告名称"))
        )
        doc = Doc(
            company_id=company_id, source="research_meta", doc_type="research_report_meta",
            title=title, text=summary, url=r.get("报告pdf链接") or None,
            permission="red", license_tag="broker-copyright-metadata-only",
            meta=r,
        )
        out.append(save(doc))
    return out


def _date(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:19])
    except Exception:
        try:
            return datetime.strptime(str(v)[:10], "%Y-%m-%d")
        except Exception:
            return None


# --- research-rating second pass (deterministic, offline) --------------------
# Ordered most-specific-first so 强烈推荐 wins over 推荐, 谨慎增持 over 增持, 强烈买入
# over 买入 … CN 5-tier conventions mapped onto the analyst_ratings buckets.
_RATING_BUCKETS: tuple[tuple[str, str], ...] = (
    ("强烈推荐", "strong_buy"), ("强烈买入", "strong_buy"), ("强力买入", "strong_buy"),
    ("强推", "strong_buy"),
    ("谨慎增持", "buy"), ("谨慎推荐", "buy"), ("增持", "buy"), ("买入", "buy"),
    ("优于大市", "buy"), ("跑赢行业", "buy"), ("强于大市", "buy"), ("推荐", "buy"),
    ("同步大市", "hold"), ("大市同步", "hold"), ("中性", "hold"), ("持有", "hold"),
    ("观望", "hold"),
    ("弱于大市", "sell"), ("跑输行业", "sell"), ("减持", "sell"),
    ("卖出", "strong_sell"), ("回避", "strong_sell"),
)
_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_TARGET_RE = re.compile(rf"目标价[^\d]{{0,8}}({_NUM})(?:\s*[—–~至-]\s*({_NUM}))?")
_ORG_RE = re.compile(r"【([^】]{2,20})】")


def _rating_bucket(text: str) -> str | None:
    for token, bucket in _RATING_BUCKETS:
        if token in text:
            return bucket
    return None


def _target_price(text: str) -> float | None:
    m = _TARGET_RE.search(text or "")
    if not m:
        return None
    lo = float(m.group(1).replace(",", ""))
    if m.group(2):  # a range ("目标价55-60元") -> midpoint
        return round((lo + float(m.group(2).replace(",", ""))) / 2, 4)
    return lo


def _meta_val(meta: dict, key_sub: str) -> str | None:
    """First non-empty metadata value whose column name contains `key_sub`."""
    for k, v in meta.items():
        if key_sub in str(k):
            s = str(v).strip()
            if s and s.lower() not in ("nan", "none", "nat"):
                return s
    return None


def extract_rating_fields(title: str, meta: dict | None = None) -> dict | None:
    """Deterministic extraction of (rating bucket, target price, broker org, date)
    from one already-ingested research-report metadata row. Structured metadata
    columns win (评级/机构/日期/目标); title regex is the fallback. Returns None
    when the row carries no rating signal (neither a rating word nor a target)."""
    meta = meta if isinstance(meta, dict) else {}
    rating = _rating_bucket(_meta_val(meta, "评级") or "") or _rating_bucket(title or "")
    pt = _target_price(title or "")
    if pt is None:
        pt = _target_price(_meta_val(meta, "目标") or "")
    org = _meta_val(meta, "机构")
    if not org:
        m = _ORG_RE.search(title or "")
        org = m.group(1) if m else None
    if rating is None and pt is None:
        return None
    d = _date(_meta_val(meta, "日期"))
    return {"rating": rating, "target_price": pt, "org": org,
            "date": d.date() if d else None}


def parse_research_ratings(company_id: str | None = None) -> dict:
    """Second pass over ALREADY-INGESTED research-report metadata rows (stored by
    `_research_meta` with source='research_meta'): extract (rating, target price,
    org, date) per report — deterministic regex/column lookup only, no LLM — and
    aggregate per (company, day) into the canonical `analyst_ratings` upsert as a
    rating distribution + price-target stats, source='cninfo'. Idempotent: the
    (company_id, as_of, source) upsert key makes re-runs recompute in place."""
    from ..storage import db, structured

    sql = ("SELECT id, company_id, title, published_at, meta FROM documents "
           "WHERE (source='research_meta' OR doc_type='research_report_meta') "
           "AND company_id IS NOT NULL")
    params: list = []
    if company_id:
        sql += " AND company_id=%s"
        params.append(company_id)
    docs = db.query(sql, params or None)
    watched = {r["id"] for r in db.query("SELECT id FROM companies")}
    groups: dict[tuple, dict] = {}
    parsed = skipped = 0
    for d in docs:
        fields = extract_rating_fields(d.get("title") or "", d.get("meta"))
        as_of = (fields or {}).get("date") or (
            d["published_at"].date() if d.get("published_at") else None)
        # analyst_ratings.company_id is an FK -> companies; skip non-watched rows.
        if not fields or as_of is None or d["company_id"] not in watched:
            skipped += 1
            continue
        g = groups.setdefault((d["company_id"], as_of), {
            "strong_buy": 0, "buy": 0, "hold": 0, "sell": 0, "strong_sell": 0,
            "n": 0, "pts": [], "orgs": []})
        if fields["rating"]:
            g[fields["rating"]] += 1
        if fields["target_price"] is not None:
            g["pts"].append(fields["target_price"])
        if fields["org"]:
            g["orgs"].append(fields["org"])
        g["n"] += 1
        parsed += 1
    written = 0
    for (cid, as_of), g in sorted(groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        pts, orgs, n = g.pop("pts"), g.pop("orgs"), g.pop("n")
        structured.upsert_rating(
            cid, as_of, **g,
            pt_mean=sum(pts) / len(pts) if pts else None,
            pt_high=max(pts) if pts else None,
            pt_low=min(pts) if pts else None,
            source="cninfo",
            meta={"orgs": sorted(set(orgs)), "n_reports": n},
        )
        written += 1
    report = {"docs": len(docs), "parsed": parsed, "skipped": skipped, "rating_rows": written}
    log.info("research ratings: %s", report)
    return report
