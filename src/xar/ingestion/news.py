"""News + product/spec pages. Polite fetch + main-content extraction
(trafilatura). Stores extracted FACTS + citation link, not republished bodies."""
from __future__ import annotations

from urllib.parse import urlparse

import httpx

from ..config import get_settings
from ..logging import get_logger
from .base import Doc, polite, save

log = get_logger("xar.ingest.news")


def _fetch(url: str) -> str | None:
    polite(urlparse(url).netloc)
    s = get_settings()
    try:
        r = httpx.get(url, headers={"User-Agent": s.http_user_agent}, timeout=30, follow_redirects=True)
        r.raise_for_status()
        return r.text
    except Exception as e:
        log.warning("fetch failed %s: %s", url, e)
        return None


def _extract(html: str) -> tuple[str, str]:
    try:
        import trafilatura

        text = trafilatura.extract(html, include_comments=False, include_tables=True) or ""
        meta = trafilatura.extract_metadata(html)
        title = (meta.title if meta else "") or ""
        return title, text
    except Exception:
        return "", ""


def ingest_urls(company_id: str | None, urls: list[str], *, source: str = "news",
                doc_type: str = "article", permission: str = "grey") -> list[str]:
    ids: list[str] = []
    for url in urls:
        html = _fetch(url)
        if not html:
            continue
        title, text = _extract(html)
        if not text:
            continue
        doc = Doc(
            company_id=company_id, source=source, doc_type=doc_type,
            title=title or url, text=text[:120_000], url=url,
            permission=permission, license_tag="extracted-facts-self-use",
        )
        ids.append(save(doc))
    log.info("%s: ingested %d/%d urls", source, len(ids), len(urls))
    return ids


def ingest_product_pages(company_id: str, urls: list[str]) -> list[str]:
    return ingest_urls(company_id, urls, source="product", doc_type="product_page",
                       permission="green")
