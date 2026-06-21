"""Top-journal / professional-platform connector for the Exploration module.

Pulls curated, high-signal science journalism + journal feeds (Quanta Magazine,
Physics World, …) via public RSS — the peer-reviewed / editorially-curated layer
above arXiv preprints. Metadata + summary only (no full-text redistribution).
Per-domain feed map; degrades to [] on any failure (never raises).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime

from ..config import get_settings
from ..logging import get_logger
from .base import _get

log = get_logger("xar.providers.journals")

# domain -> curated RSS feeds (frontier science journalism + journals)
FEEDS: dict[str, list[str]] = {
    "ai": ["https://www.quantamagazine.org/tag/artificial-intelligence/feed/"],
    "physics": ["https://www.quantamagazine.org/physics/feed/", "https://physicsworld.com/feed/"],
    "math": ["https://www.quantamagazine.org/mathematics/feed/"],
    "cs_systems": ["https://www.quantamagazine.org/computer-science/feed/"],
    "neuro": ["https://www.quantamagazine.org/biology/feed/"],
    "complex": ["https://www.quantamagazine.org/feed/"],
}
_RSS_DATE = "%a, %d %b %Y %H:%M:%S %z"


def available() -> bool:
    return get_settings().arxiv_enabled  # same public-source posture as arXiv


def _clean(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", html or "").strip()


def _parse_date(s: str):
    try:
        return datetime.strptime(s.strip(), _RSS_DATE)
    except Exception:
        return None


def _parse_rss(xml_text: str) -> list[dict]:
    out: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return out
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = _clean(item.findtext("description") or "")
        if not title or not link:
            continue
        out.append({"title": " ".join(title.split()), "url": link,
                    "summary": " ".join(desc.split())[:2000],
                    "published": item.findtext("pubDate") or ""})
    return out


def fetch(domain_id: str, *, max_items: int = 20) -> list[dict]:
    """Recent articles for a domain across its curated journal feeds."""
    out: list[dict] = []
    for url in FEEDS.get(domain_id, []):
        try:
            host = url.split("/")[2]
            r = _get(url, host=host, timeout=30)
            out += _parse_rss(r.text)
        except Exception as e:  # noqa: BLE001
            log.warning("journal feed %s failed: %s", url, e)
    # de-dup by url, newest-ish first (feeds are already roughly reverse-chron)
    seen, uniq = set(), []
    for a in out:
        if a["url"] in seen:
            continue
        seen.add(a["url"])
        uniq.append(a)
    log.info("journals %s -> %d articles", domain_id, len(uniq))
    return uniq[:max_items]
