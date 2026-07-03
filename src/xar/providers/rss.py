"""Curated industry-news RSS/Atom provider (丰富资讯来源).

Pulls the free feeds declared code-as-truth in `xar.ingestion.feeds` (8 themes)
and folds each new entry into the SAME unstructured path as every other news
source: Doc(source='rss', permission='grey') -> documents -> chunk/embed ->
LLM extraction -> ontology. Posture is self-use: we store headline + summary +
the canonical link (citation), never a republished article body.

Public feeds, no key — `available()` is always True (like polymarket). Fetches
are polite (settings.crawl_delay_seconds per host, settings.http_user_agent)
and parsing is stdlib xml.etree (RSS 2.0 + Atom), no new dependency. Dedup
rides the content-hash Doc.id upsert, so overlapping windows / re-runs are
idempotent; `since` (the daily runlog cursor) additionally skips old entries.
"""
from __future__ import annotations

import html as _html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

from ..config import get_settings
from ..ingestion.base import Doc, polite, save
from ..ingestion.feeds import FEEDS, feed_by_id
from ..storage import db
from .base import log

_ATOM = "{http://www.w3.org/2005/Atom}"
_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"
_DC_DATE = "{http://purl.org/dc/elements/1.1/}date"
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_DEFAULT_LICENSE = "rss-headline-extracted-facts-self-use"


def available() -> bool:
    return True  # public feeds, no key


# --- fetch + parse (parse is pure -> offline-testable) -----------------------
def _fetch(url: str) -> str | None:
    polite(urlparse(url).netloc)
    s = get_settings()
    try:
        r = httpx.get(url, headers={"User-Agent": s.http_user_agent}, timeout=30,
                      follow_redirects=True)
        r.raise_for_status()
        return r.text
    except Exception as e:  # noqa: BLE001
        status = getattr(getattr(e, "response", None), "status_code", "")
        log.warning("rss fetch %s failed: %s %s", url, type(e).__name__, status)
        return None


def _clean(fragment: str | None) -> str:
    if not fragment:
        return ""
    return _WS.sub(" ", _html.unescape(_TAG.sub(" ", fragment))).strip()


def _parse_date(v) -> datetime | None:
    """RFC-2822 (RSS pubDate) or ISO-8601 (Atom/dc) -> aware UTC datetime."""
    if not v:
        return None
    s = str(v).strip()
    for fn in (parsedate_to_datetime,
               lambda x: datetime.fromisoformat(x.replace("Z", "+00:00"))):
        try:
            dt = fn(s)
        except Exception:  # noqa: BLE001
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def parse_feed(text: str) -> list[dict]:
    """RSS 2.0 / Atom XML -> [{title, url, summary, published}] (stdlib only)."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        log.warning("rss XML parse failed: %s", e)
        return []
    out: list[dict] = []
    for item in root.iter("item"):  # RSS 2.0
        out.append({
            "title": _clean(item.findtext("title")),
            "url": (item.findtext("link") or item.findtext("guid") or "").strip(),
            "summary": _clean(item.findtext("description") or item.findtext(_CONTENT_NS)),
            "published": _parse_date(item.findtext("pubDate") or item.findtext(_DC_DATE)),
        })
    if out:
        return out
    for entry in root.iter(f"{_ATOM}entry"):  # Atom
        link = entry.find(f"{_ATOM}link")
        out.append({
            "title": _clean(entry.findtext(f"{_ATOM}title")),
            "url": (link.get("href") if link is not None else entry.findtext(f"{_ATOM}id") or "").strip(),
            "summary": _clean(entry.findtext(f"{_ATOM}summary") or entry.findtext(f"{_ATOM}content")),
            "published": _parse_date(entry.findtext(f"{_ATOM}published")
                                     or entry.findtext(f"{_ATOM}updated")),
        })
    return out


# --- store -------------------------------------------------------------------
def _as_dt(v) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v.replace(tzinfo=timezone.utc) if isinstance(v, datetime) and not v.tzinfo else v
    return _parse_date(v)


def _save_entries(feed: dict, entries: list[dict], since: datetime | None, limit: int) -> int:
    n = 0
    for e in entries[:limit]:
        if since and e.get("published") and e["published"] <= since:
            continue  # older than the incremental cursor
        title, summary = e.get("title") or "", e.get("summary") or ""
        text = f"{title}\n\n{summary}".strip() if summary else title
        if len(text) < 24:
            continue
        doc_id = save(Doc(
            company_id=None, source="rss", doc_type="news",
            title=title or text[:80], text=text[:20_000], url=e.get("url") or None,
            published_at=e.get("published"), permission="grey",
            license_tag=feed.get("license_tag") or _DEFAULT_LICENSE,
            meta={"feed_id": feed["id"], "feed_name": feed["name"],
                  "themes": feed["themes"], "lang": feed["lang"]}))
        # theme-tag the doc (single primary theme column; full list stays in meta)
        db.execute("UPDATE documents SET theme=%s WHERE id=%s AND theme IS NULL",
                   (feed["themes"][0], doc_id))
        n += 1
    return n


def pull_feed(feed_id: str, *, since=None, limit: int = 50) -> int:
    """Fetch + store one curated feed. Returns docs saved (0 on any failure)."""
    feed = feed_by_id(feed_id)
    if not feed:
        log.warning("rss: unknown feed id %r", feed_id)
        return 0
    text = _fetch(feed["url"])
    if not text:
        return 0
    n = _save_entries(feed, parse_feed(text), _as_dt(since), limit)
    log.info("rss %s: %d docs", feed_id, n)
    return n


def pull(feed_id: str | None = None, *, since=None, limit: int = 50) -> int:
    """Pull one feed, or ALL curated feeds when feed_id is None. One dead feed
    never sinks the sweep. Returns total docs saved."""
    ids = [feed_id] if feed_id else [f["id"] for f in FEEDS]
    n = 0
    for fid in ids:
        try:
            n += pull_feed(fid, since=since, limit=limit)
        except Exception as e:  # noqa: BLE001
            log.warning("rss %s failed: %s", fid, e)
    return n
