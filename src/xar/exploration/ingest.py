"""Frontier ingestion: arXiv preprints + expert voices (X) -> documents.

Both land in the shared `documents` table tagged with `meta.domain` and
`meta.frontier=true`, so the synthesis layer (and the data-lake / embeddings)
can find them. Metadata + abstracts only — no full-text redistribution.
"""
from __future__ import annotations

from ..config import get_settings
from ..ingestion.base import Doc, save
from ..logging import get_logger
from ..providers import arxiv, journals, twitter
from .domains import DOMAINS, domain_by_id

log = get_logger("xar.exploration.ingest")


def _parse_dt(s: str | None):
    if not s:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def ingest_papers(domain_id: str, *, days: int | None = None, max_results: int | None = None) -> int:
    """Pull recent arXiv preprints for a domain into `documents` (source='arxiv')."""
    d = domain_by_id(domain_id)
    if not d or not d.get("arxiv_cats") or not arxiv.available():
        return 0
    s = get_settings()
    papers = arxiv.fetch(d["arxiv_cats"], max_results=max_results or s.arxiv_max_results,
                         days=days if days is not None else s.arxiv_lookback_days)
    n = 0
    for p in papers:
        text = p["summary"]
        if len(text) < 80:
            continue
        save(Doc(company_id=None, source="arxiv", doc_type="preprint",
                 title=p["title"], text=text[:60_000], url=p["url"],
                 published_at=_parse_dt(p["published"]), permission="green",
                 license_tag="arxiv-abstract-metadata",
                 meta={"frontier": True, "domain": domain_id, "arxiv_id": p["arxiv_id"],
                       "authors": p["authors"], "cats": p["cats"],
                       "primary_cat": p.get("primary_cat")}))
        n += 1
    log.info("frontier papers %s: %d", domain_id, n)
    return n


def ingest_voices(domain_id: str, *, max_results: int = 40) -> int:
    """Pull recent CURATED-EXPERT posts (X) for a domain into `documents` (source='x').
    Handles-only (no noisy domain-term search) and reply-filtered, so the displayed
    'expert voices' are genuinely the curated frontier researchers."""
    d = domain_by_id(domain_id)
    if not d or not twitter.available():
        return 0
    handle_set = {h.lower() for h in d.get("handles", [])}
    posts = twitter.pull_frontier(d.get("handles", []), [], max_results=max_results)  # handles only
    n = 0
    for tw in posts:
        text = (tw.get("text") or "").strip()
        author = tw.get("author")
        # skip replies (start with @) and thin posts; keep only curated handles
        if (not tw.get("id") or len(text) < 50 or text.startswith("@")
                or not (author and author.lower() in handle_set)):
            continue
        save(Doc(company_id=None, source="x", doc_type="x_post",
                 title=f"X @{author} · {domain_id}", text=text,
                 url=f"https://x.com/i/web/status/{tw['id']}", permission="grey",
                 license_tag="x-extracted-facts-self-use",
                 meta={"frontier": True, "domain": domain_id, "author": author,
                       "expert": True, "social_id": f"x:{tw['id']}"}))
        n += 1
    log.info("frontier voices %s: %d", domain_id, n)
    return n


def ingest_journals(domain_id: str, *, max_items: int = 16) -> int:
    """Pull recent curated journal/professional articles into `documents`
    (source='journal') — the peer-reviewed/editorial layer above preprints."""
    d = domain_by_id(domain_id)
    if not d or not journals.available():
        return 0
    n = 0
    for a in journals.fetch(domain_id, max_items=max_items):
        text = (a.get("summary") or a.get("title") or "").strip()
        if len(text) < 40:
            continue
        save(Doc(company_id=None, source="journal", doc_type="article",
                 title=a["title"], text=text[:20_000], url=a["url"],
                 published_at=_parse_dt(a.get("published")), permission="green",
                 license_tag="journal-metadata-summary",
                 meta={"frontier": True, "domain": domain_id}))
        n += 1
    log.info("frontier journals %s: %d", domain_id, n)
    return n


def ingest_domain(domain_id: str, *, days: int | None = None, voices: bool = True) -> dict:
    out = {"papers": ingest_papers(domain_id, days=days),
           "journals": ingest_journals(domain_id)}
    if voices:
        out["voices"] = ingest_voices(domain_id)
    return out


def ingest_all(*, days: int | None = None, voices: bool = True) -> dict:
    return {d["id"]: ingest_domain(d["id"], days=days, voices=voices) for d in DOMAINS}
