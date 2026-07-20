"""WeChat Official Account (微信公众号) connector via a self-hosted we-mp-rss
service (https://github.com/rachelos/we-mp-rss).

we-mp-rss logs into WeChat, scrapes subscribed 公众号, and exposes PUBLIC feed
endpoints. We consume those — no auth, no new dependency (stdlib XML/JSON
parsing) — and fold each article into the SAME unstructured path as news/research:
    fetch -> Doc(source="wechat", permission="grey") -> documents
          -> chunk + embed (RAG) -> LLM extraction -> bitemporal ontology.

So 公众号 industry commentary becomes first-class evidence: it is chunked for
retrieval and mined for the same nodes/edges/catalyst-events as any filing. Posture
is self-use: we store extracted facts + the canonical article URL (citation), not a
republished copy (permission=grey, like news).

Endpoints used (we-mp-rss, default port 8001):
    GET {base}/feed/{feed_id}.json   per-account JSON feed   (preferred)
    GET {base}/feed/{feed_id}.rss    per-account RSS 2.0     (fallback)
    GET {base}/rss                   aggregated RSS of all subscribed accounts
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

from ..config import get_settings
from ..logging import get_logger
from .base import Doc, polite, save
from .registry import COMPANIES

log = get_logger("xar.ingest.wechat")

_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"
_ATOM = "{http://www.w3.org/2005/Atom}"
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f]+")


def available() -> bool:
    return bool(get_settings().werss_base_url)


# --- company linking -------------------------------------------------------
def _alias_index() -> list[tuple[str, str]]:
    idx: list[tuple[str, str]] = []
    for c in COMPANIES:
        for a in [c["name"], *c.get("aliases", [])]:
            if a:
                idx.append((a.lower(), c["id"]))
    # longer aliases first so "中际旭创" wins over a bare token
    return sorted(idx, key=lambda t: -len(t[0]))


def _link_company(text: str, aliases, feed_company: str | None) -> str | None:
    if feed_company:
        return feed_company
    t = (text or "").lower()
    return next((cid for alias, cid in aliases if alias and alias in t), None)


def _feed_map() -> dict[str, str]:
    raw = get_settings().werss_feed_map.strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        log.warning("WERSS_FEED_MAP is not valid JSON; ignoring")
        return {}


# --- HTTP ------------------------------------------------------------------
def _get(path: str) -> httpx.Response | None:
    s = get_settings()
    url = s.werss_base_url.rstrip("/") + path
    polite(urlparse(url).netloc)
    headers = {"User-Agent": s.http_user_agent}
    if s.werss_api_token:
        headers["Authorization"] = f"Bearer {s.werss_api_token}"
    try:
        r = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
        r.raise_for_status()
        return r
    except Exception as e:  # noqa: BLE001
        log.warning("werss GET %s failed: %s", url, e)
        return None


# --- parsing ---------------------------------------------------------------
def _clean(htmltext: str) -> str:
    if not htmltext:
        return ""
    text = _TAG.sub(" ", htmltext)
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    return "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())


def _parse_date(v) -> datetime | None:
    if not v:
        return None
    s = str(v).strip()
    for fn in (lambda x: parsedate_to_datetime(x), lambda x: datetime.fromisoformat(x.replace("Z", "+00:00"))):
        try:
            return fn(s)
        except Exception:
            continue
    return None


def _items_from_json(payload) -> list[dict]:
    """Accept JSON Feed ({items:[...]}) or a bare list / {data:{list:[...]}}."""
    if isinstance(payload, dict):
        items = payload.get("items") or payload.get("data", {}).get("list") or payload.get("list") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append({
            "title": it.get("title") or "",
            "url": it.get("url") or it.get("link") or it.get("id") or "",
            "content": (it.get("content_html") or it.get("content") or it.get("content_text")
                        or it.get("description") or it.get("summary") or ""),
            "date": (it.get("date_published") or it.get("pubDate") or it.get("publish_time")
                     or it.get("updated")),
        })
    return out


def _items_from_xml(text: str) -> list[dict]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        log.warning("werss XML parse failed: %s", e)
        return []
    out: list[dict] = []
    # RSS 2.0
    for item in root.iter("item"):
        content = item.findtext(_CONTENT_NS) or item.findtext("description") or ""
        out.append({"title": item.findtext("title") or "",
                    "url": item.findtext("link") or item.findtext("guid") or "",
                    "content": content, "date": item.findtext("pubDate")})
    if out:
        return out
    # Atom
    for entry in root.iter(f"{_ATOM}entry"):
        link_el = entry.find(f"{_ATOM}link")
        url = link_el.get("href") if link_el is not None else ""
        content = (entry.findtext(f"{_ATOM}content") or entry.findtext(f"{_ATOM}summary") or "")
        out.append({"title": entry.findtext(f"{_ATOM}title") or "", "url": url,
                    "content": content,
                    "date": entry.findtext(f"{_ATOM}published") or entry.findtext(f"{_ATOM}updated")})
    return out


# --- ingestion -------------------------------------------------------------
def _ingest_items(items: list[dict], *, feed_company: str | None, aliases, limit: int,
                  feed_id: str | None = None) -> list[str]:
    s = get_settings()
    ids: list[str] = []
    for it in items[:limit]:
        text = _clean(it.get("content", ""))
        title = (it.get("title") or "").strip()
        if not text and not title:
            continue
        body = (f"{title}\n\n{text}").strip()
        company_id = _link_company(f"{title}\n{text}", aliases, feed_company)
        # feed_id 溯源:让订阅文章可归因到来源公众号(账号级发现的去留评估据此聚合)
        meta = {"platform": "wechat_mp", "werss": s.werss_base_url}
        if feed_id:
            meta["feed_id"] = feed_id
        doc = Doc(
            company_id=company_id, source="wechat", doc_type="mp_article",
            title=title or "微信公众号文章", text=body[:120_000], url=it.get("url") or None,
            published_at=_parse_date(it.get("date")), permission="grey",
            license_tag="wechat-extracted-facts-self-use",
            meta=meta,
        )
        ids.append(save(doc))
    return ids


def ingest_feed(feed_id: str, *, company_id: str | None = None, limit: int | None = None) -> list[str]:
    """Ingest one 公众号 feed. Tries JSON first, falls back to RSS XML."""
    s = get_settings()
    limit = limit or s.werss_max_items
    aliases = _alias_index()
    r = _get(f"/feed/{feed_id}.json")
    items: list[dict] = []
    if r is not None:
        try:
            items = _items_from_json(r.json())
        except Exception:
            items = []
    if not items:
        r = _get(f"/feed/{feed_id}.rss")
        items = _items_from_xml(r.text) if r is not None else []
    ids = _ingest_items(items, feed_company=company_id, aliases=aliases, limit=limit,
                        feed_id=feed_id)
    log.info("wechat feed %s: %d articles", feed_id, len(ids))
    return ids


def ingest_aggregated(limit: int | None = None) -> list[str]:
    """Ingest the aggregated RSS of ALL subscribed accounts (no feed ids needed)."""
    s = get_settings()
    limit = limit or s.werss_max_items
    r = _get("/rss")
    if r is None:
        return []
    items = _items_from_xml(r.text)
    ids = _ingest_items(items, feed_company=None, aliases=_alias_index(), limit=limit)
    log.info("wechat aggregated: %d articles", len(ids))
    return ids


def ingest(limit: int | None = None) -> list[str]:
    """Entry point. Per-feed when WERSS_FEEDS is set (enables company mapping),
    else the aggregated feed. No-op when no we-mp-rss base url is configured."""
    if not available():
        return []
    s = get_settings()
    feeds = [f.strip() for f in s.werss_feeds.split(",") if f.strip()]
    if not feeds:
        return ingest_aggregated(limit)
    fmap = _feed_map()
    ids: list[str] = []
    for fid in feeds:
        ids += ingest_feed(fid, company_id=fmap.get(fid), limit=limit)
    return ids
