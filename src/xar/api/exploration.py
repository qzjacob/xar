"""Exploration API — frontier-research sections, parallel to the `/api/ui/*`
investment dashboard. Read-only views over `frontier_fronts` + `frontier_domain_state`
+ `documents`. AI is the first section.
"""
from __future__ import annotations

from ..exploration.domains import DOMAINS, domain_by_id
from ..storage import db


def _state(domain_id: str) -> dict:
    rows = db.query("SELECT * FROM frontier_domain_state WHERE domain=%s", (domain_id,))
    return rows[0] if rows else {}


def _doc_counts(domain_id: str) -> tuple[int, int, int]:
    """Live (papers, voices, articles) of ingested frontier docs for a domain —
    accurate even before synthesis has run."""
    rows = db.query(
        "SELECT source, count(*) n FROM documents "
        "WHERE meta->>'domain'=%s AND meta->>'frontier'='true' GROUP BY source", (domain_id,))
    by = {r["source"]: r["n"] for r in rows}
    return by.get("arxiv", 0), by.get("x", 0), by.get("journal", 0)


def _front_rows(domain_id: str, limit: int = 30) -> list[dict]:
    return db.query(
        "SELECT id, title, summary, direction, significance, maturity, horizon, momentum, "
        "confidence, key_papers, key_terms, key_voices, updated_at "
        "FROM frontier_fronts WHERE domain=%s ORDER BY momentum DESC, updated_at DESC LIMIT %s",
        (domain_id, limit))


def _paper_index(domain_id: str, limit: int = 60) -> dict[str, dict]:
    rows = db.query(
        "SELECT title, url, meta FROM documents WHERE source='arxiv' AND meta->>'domain'=%s "
        "ORDER BY COALESCE(published_at, ingested_at) DESC LIMIT %s", (domain_id, limit))
    idx: dict[str, dict] = {}
    for r in rows:
        aid = (r["meta"] or {}).get("arxiv_id")
        if aid:
            idx[aid] = {"arxivId": aid, "title": r["title"], "url": r["url"],
                        "authors": (r["meta"] or {}).get("authors", [])[:4]}
    return idx


def _section_card(d: dict) -> dict:
    st = _state(d["id"])
    fronts = _front_rows(d["id"], limit=3)
    papers, voices, articles = _doc_counts(d["id"])
    return {
        "id": d["id"], "name": d["name"], "nameCn": d["nameCn"], "icon": d.get("icon"),
        "blurb": d["blurb"], "blurbCn": d["blurbCn"],
        "headline": st.get("headline") or d["blurb"],
        "momentum": st.get("momentum", 50),
        "paperCount": papers, "voiceCount": voices, "articleCount": articles,
        "frontCount": st.get("front_count", 0),
        "topFronts": [{"title": f["title"], "maturity": f["maturity"], "momentum": f["momentum"]}
                      for f in fronts],
        "updatedAt": st.get("updated_at"),
    }


def overview() -> dict:
    """Exploration dashboard — one card per frontier section (AI first)."""
    cards = [_section_card(d) for d in DOMAINS]
    totals = db.query(
        "SELECT (SELECT count(*) FROM frontier_fronts) AS fronts, "
        "(SELECT count(*) FROM documents WHERE source='arxiv' AND meta->>'frontier'='true') AS papers, "
        "(SELECT count(*) FROM documents WHERE source='journal' AND meta->>'frontier'='true') AS articles, "
        "(SELECT count(*) FROM documents WHERE source='x' AND meta->>'frontier'='true') AS voices")
    return {"sections": cards, "totals": totals[0] if totals else {},
            "updatedAt": max((c["updatedAt"] for c in cards if c["updatedAt"]), default=None)}


def section(domain_id: str) -> dict | None:
    """Section detail — fronts (with cited papers), recent papers, expert voices."""
    d = domain_by_id(domain_id)
    if not d:
        return None
    st = _state(domain_id)
    pidx = _paper_index(domain_id)
    fronts = []
    for f in _front_rows(domain_id):
        fronts.append({
            "id": f["id"], "title": f["title"], "summary": f["summary"],
            "direction": f["direction"], "significance": f["significance"],
            "maturity": f["maturity"], "horizon": f["horizon"], "momentum": f["momentum"],
            "confidence": f["confidence"], "keyTerms": f["key_terms"], "keyVoices": f["key_voices"],
            "papers": [pidx[a] for a in (f["key_papers"] or []) if a in pidx],
        })
    papers = [{"arxivId": (r["meta"] or {}).get("arxiv_id"), "title": r["title"], "url": r["url"],
               "authors": (r["meta"] or {}).get("authors", [])[:4],
               "published": r["published_at"]}
              for r in db.query(
                  "SELECT title, url, published_at, meta FROM documents "
                  "WHERE source='arxiv' AND meta->>'domain'=%s "
                  "ORDER BY COALESCE(published_at, ingested_at) DESC LIMIT 24", (domain_id,))]
    articles = [{"title": r["title"], "url": r["url"], "summary": (r["text"] or "")[:240]}
                for r in db.query(
                    "SELECT title, url, text FROM documents "
                    "WHERE source='journal' AND meta->>'domain'=%s "
                    "ORDER BY COALESCE(published_at, ingested_at) DESC LIMIT 12", (domain_id,))]
    voices = [{"author": (r["meta"] or {}).get("author"), "text": r["text"], "url": r["url"],
               "expert": bool((r["meta"] or {}).get("expert"))}
              for r in db.query(
                  "SELECT text, url, meta FROM documents WHERE source='x' AND meta->>'domain'=%s "
                  "ORDER BY (meta->>'expert'='true') DESC, ingested_at DESC LIMIT 16", (domain_id,))]
    pc, vc, ac = _doc_counts(domain_id)
    return {
        "section": {"id": d["id"], "name": d["name"], "nameCn": d["nameCn"], "icon": d.get("icon"),
                    "blurb": d["blurb"], "blurbCn": d["blurbCn"],
                    "headline": st.get("headline") or d["blurb"], "momentum": st.get("momentum", 50),
                    "paperCount": pc, "voiceCount": vc, "articleCount": ac,
                    "frontCount": st.get("front_count", 0), "updatedAt": st.get("updated_at")},
        "fronts": fronts, "papers": papers, "articles": articles, "voices": voices,
    }
