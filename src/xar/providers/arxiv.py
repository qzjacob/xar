"""arXiv connector — the frontier-preprint source for the Exploration module.

Public API (no key): GET export.arxiv.org/api/query returns an Atom feed of recent
preprints by category, sorted newest-first. We extract title/abstract/authors/date
(metadata + abstract only — no full-text redistribution) for the trend-synthesis
layer. Polite + retrying via the shared provider HTTP plumbing.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from ..config import get_settings
from ..logging import get_logger
from .base import _get

log = get_logger("xar.providers.arxiv")

_API = "http://export.arxiv.org/api/query"
_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def available() -> bool:
    return get_settings().arxiv_enabled


def _text(el, path: str) -> str:
    node = el.find(path, _NS)
    return (node.text or "").strip() if node is not None and node.text else ""


def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _parse_feed(xml_text: str) -> list[dict]:
    out: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except Exception as e:  # noqa: BLE001
        log.warning("arxiv feed parse failed: %s", e)
        return out
    for e in root.findall("a:entry", _NS):
        raw_id = _text(e, "a:id")              # http://arxiv.org/abs/2401.01234v1
        arxiv_id = raw_id.rsplit("/abs/", 1)[-1] if "/abs/" in raw_id else raw_id
        title = " ".join(_text(e, "a:title").split())
        summary = " ".join(_text(e, "a:summary").split())
        if not arxiv_id or not title:
            continue
        authors = [_text(a, "a:name") for a in e.findall("a:author", _NS)]
        cats = [c.get("term") for c in e.findall("a:category", _NS) if c.get("term")]
        prim = e.find("arxiv:primary_category", _NS)
        out.append({
            "arxiv_id": arxiv_id,
            "title": title,
            "summary": summary,
            "authors": [a for a in authors if a][:12],
            "published": _text(e, "a:published"),
            "updated": _text(e, "a:updated"),
            "url": raw_id,
            "primary_cat": prim.get("term") if prim is not None else (cats[0] if cats else None),
            "cats": cats,
        })
    return out


def fetch(categories: list[str], *, max_results: int = 60, days: int | None = None) -> list[dict]:
    """Recent preprints across the given arXiv categories, newest first. Returns
    [] on any failure (never raises). `days` filters by submission recency."""
    if not categories:
        return []
    query = " OR ".join(f"cat:{c}" for c in categories)
    try:
        r = _get(_API, params={"search_query": query, "sortBy": "submittedDate",
                               "sortOrder": "descending", "max_results": max_results},
                 host="export.arxiv.org", timeout=40)
        papers = _parse_feed(r.text)
    except Exception as e:  # noqa: BLE001
        log.warning("arxiv fetch failed (%s): %s", query[:50], e)
        return []
    if days:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        papers = [p for p in papers
                  if (_parse_dt(p["published"]) or datetime.now(timezone.utc)).timestamp() >= cutoff]
    log.info("arxiv %s -> %d papers", ",".join(categories), len(papers))
    return papers
