"""China A-share ingestion via AKShare (optional dep: `pip install '.[cn]'`).

- cninfo statutory disclosures: GREEN (mandatory public filings).
- Sell-side research: METADATA ONLY (title/institution/rating/target) — RED for
  full text; never ingest broker PDF bodies. (design §4 highest-risk pillar)
"""
from __future__ import annotations

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
