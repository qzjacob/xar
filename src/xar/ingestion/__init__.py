"""Ingestion connectors. Each connector tags every document with a data-permission
posture (green/grey/red) recording self-use risk."""
from __future__ import annotations

from ..logging import get_logger
from . import cninfo, edgar, jobs, news, wechat  # noqa: F401
from .base import save, seed_companies  # noqa: F401
from .registry import COMPANIES

log = get_logger("xar.ingest")


def ingest_wechat(limit: int | None = None) -> list[str]:
    """Ingest WeChat Official Account articles via a we-mp-rss service (skipped
    when WERSS_BASE_URL is unset)."""
    return wechat.ingest(limit=limit)


def ingest_company(company_id: str, *, edgar_limit: int = 8, cn_limit: int = 20) -> list[str]:
    """Run all applicable filing connectors for one company (US + CN)."""
    ids: list[str] = []
    ids += edgar.ingest_company(company_id, limit=edgar_limit)
    ids += cninfo.ingest_company(company_id, limit=cn_limit)
    return ids


def ingest_basket(*, with_wechat: bool = True, **kw) -> dict[str, int]:
    """Ingest filings for the whole watched basket (+ WeChat 公众号 if configured)."""
    seed_companies()
    out: dict[str, int] = {}
    for c in COMPANIES:
        out[c["id"]] = len(ingest_company(c["id"], **kw))
    if with_wechat and wechat.available():
        out["_wechat"] = len(ingest_wechat())
    return out
