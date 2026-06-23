"""Frontier-trend synthesis — the analytical core of the Exploration module.

Reads recent preprints + expert voices for a domain and asks the reasoning-tier
LLM to distill a handful of forward-looking *research fronts*: where the edge is
moving, the directional thesis, maturity, horizon, and significance. The emphasis
is long-horizon direction, NOT trade ideas. Results upsert into `frontier_fronts`
+ `frontier_domain_state` for the API/UI.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ..logging import get_logger
from ..models import llm
from ..storage import db
from .domains import DOMAINS, domain_by_id

log = get_logger("xar.exploration.synth")

_MATURITY = {"emerging", "accelerating", "maturing"}
_HORIZON = {"near", "mid", "long"}


class ResearchFront(BaseModel):
    title: str = Field(default="", description="3-6 word name of the research front")
    summary: str = Field(default="", description="2-3 sentences: what is happening now")
    direction: str = Field(default="", description="forward-looking directional thesis: where this is heading over 1-5+ years")
    significance: str = Field(default="", description="why it matters / second-order implications for the frontier")
    maturity: str = Field(default="emerging", description="emerging | accelerating | maturing")
    horizon: str = Field(default="mid", description="near (0-1y) | mid (1-3y) | long (3y+)")
    momentum: int = Field(default=50, description="0-100 activity/acceleration of this front")
    confidence: float = Field(default=0.6, description="0-1 conviction in this synthesis")
    key_terms: list[str] = Field(default_factory=list)
    key_papers: list[str] = Field(default_factory=list, description="arXiv ids cited from the provided list")


class FrontierReport(BaseModel):
    headline: str = Field(default="", description="one-line state-of-the-frontier for this domain")
    momentum: int = Field(default=50, description="0-100 overall domain momentum")
    fronts: list[ResearchFront] = Field(default_factory=list)


_SYSTEM = (
    "You are a meticulous research scout mapping the frontier of human knowledge. "
    "You synthesize where a field is heading from primary sources (preprints + expert "
    "voices). Prefer precision and intellectual honesty over hype. Emphasize long-horizon "
    "DIRECTION and second-order implications, not near-term trades. Ground every front in "
    "the provided sources; cite arXiv ids you used. Output strictly the requested JSON."
)


def _recent_docs(domain_id: str, *, paper_limit: int = 26, voice_limit: int = 14,
                 article_limit: int = 10) -> tuple[list, list, list]:
    papers = db.query(
        "SELECT id, title, text, meta FROM documents "
        "WHERE source='arxiv' AND meta->>'domain'=%s "
        "ORDER BY COALESCE(published_at, ingested_at) DESC LIMIT %s",
        (domain_id, paper_limit))
    articles = db.query(
        "SELECT id, title, text, url, meta FROM documents "
        "WHERE source='journal' AND meta->>'domain'=%s "
        "ORDER BY COALESCE(published_at, ingested_at) DESC LIMIT %s",
        (domain_id, article_limit))
    # curated-handle (expert) posts first, then term-search hits
    voices = db.query(
        "SELECT id, title, text, meta FROM documents "
        "WHERE source='x' AND meta->>'domain'=%s "
        "ORDER BY (meta->>'expert'='true') DESC, ingested_at DESC LIMIT %s",
        (domain_id, voice_limit))
    return papers, voices, articles


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48] or "front"


def synthesize(domain_id: str, *, run_id: str | None = None) -> dict:
    """Synthesize research fronts for one domain and persist them. Returns a summary."""
    d = domain_by_id(domain_id)
    if not d:
        return {"error": "unknown domain"}
    papers, voices, articles = _recent_docs(domain_id)
    if not papers and not voices and not articles:
        db.execute(
            "INSERT INTO frontier_domain_state (domain, headline, momentum, paper_count, voice_count, front_count) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (domain) DO UPDATE SET "
            "headline=EXCLUDED.headline, paper_count=0, voice_count=0, front_count=0, updated_at=now()",
            (domain_id, "No sources ingested yet.", 50, 0, 0, 0))
        return {"domain": domain_id, "fronts": 0, "papers": 0, "voices": 0}

    valid_ids = {(p["meta"] or {}).get("arxiv_id") for p in papers} - {None}
    paper_block = "\n\n".join(
        f"[{(p['meta'] or {}).get('arxiv_id','?')}] {p['title']}\n{(p['text'] or '')[:700]}"
        for p in papers)
    voice_block = "\n".join(f"@{(v['meta'] or {}).get('author','?')}: {(v['text'] or '')[:240]}"
                            for v in voices)
    article_block = "\n".join(f"- {a['title']}: {(a['text'] or '')[:280]}" for a in articles)
    prompt = (
        f"Frontier domain: {d['name']} ({d['nameCn']}).\n"
        f"Scope: {d['blurb']}\n\n"
        f"=== RECENT arXiv PREPRINTS ({len(papers)}) ===\n{paper_block}\n\n"
        f"=== CURATED JOURNAL / PROFESSIONAL ARTICLES ({len(articles)}) ===\n{article_block or '(none)'}\n\n"
        f"=== RECENT EXPERT VOICES ({len(voices)}) ===\n{voice_block or '(none)'}\n\n"
        "Task: identify 5-7 distinct RESEARCH FRONTS — the directions where this frontier is "
        "actually moving. For each: a crisp title, what's happening now (summary), the "
        "forward-looking DIRECTION (where it heads over 1-5+ years), its SIGNIFICANCE / "
        "second-order implications, maturity (emerging|accelerating|maturing), horizon "
        "(near|mid|long), momentum 0-100, confidence 0-1, key_terms, and key_papers (cite the "
        "bracketed arXiv ids you used). Also give a one-line domain headline and overall momentum."
    )
    report = llm.complete_json(prompt, FrontierReport, system=_SYSTEM, tier="strong",
                               node="frontier_synth", run_id=run_id, max_tokens=8000)

    # a synthesis run represents the CURRENT state of the frontier — replace, don't
    # accumulate. delete + reinsert + domain-state run in ONE transaction so a mid-loop
    # failure can never leave the domain with a half-replaced (or empty) front set.
    kept = 0
    with db.tx() as c:
        if report.fronts:
            c.execute("DELETE FROM frontier_fronts WHERE domain=%s", (domain_id,))
        for f in report.fronts:
            if not f.title:
                continue
            mat = f.maturity if f.maturity in _MATURITY else "emerging"
            hor = f.horizon if f.horizon in _HORIZON else "mid"
            papers_cited = [pid for pid in f.key_papers if pid in valid_ids][:8]
            experts = sorted({(v["meta"] or {}).get("author") for v in voices
                              if (v["meta"] or {}).get("author") and (v["meta"] or {}).get("expert")})
            voices_used = (experts or sorted({(v["meta"] or {}).get("author") for v in voices
                                              if (v["meta"] or {}).get("author")}))[:6]
            fid = f"{domain_id}:{_slug(f.title)}"
            c.execute(
                "INSERT INTO frontier_fronts "
                "(id, domain, title, summary, direction, significance, maturity, horizon, "
                " momentum, confidence, key_papers, key_terms, key_voices, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) "
                "ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title, summary=EXCLUDED.summary, "
                "direction=EXCLUDED.direction, significance=EXCLUDED.significance, "
                "maturity=EXCLUDED.maturity, horizon=EXCLUDED.horizon, momentum=EXCLUDED.momentum, "
                "confidence=EXCLUDED.confidence, key_papers=EXCLUDED.key_papers, "
                "key_terms=EXCLUDED.key_terms, key_voices=EXCLUDED.key_voices, updated_at=now()",
                (fid, domain_id, f.title[:200], f.summary, f.direction, f.significance, mat, hor,
                 max(0, min(100, int(f.momentum))), max(0.0, min(1.0, float(f.confidence))),
                 papers_cited, f.key_terms[:10], voices_used))
            kept += 1

        c.execute(
            "INSERT INTO frontier_domain_state "
            "(domain, headline, momentum, paper_count, voice_count, front_count, synthesized_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (domain) DO UPDATE SET "
            "headline=EXCLUDED.headline, momentum=EXCLUDED.momentum, paper_count=EXCLUDED.paper_count, "
            "voice_count=EXCLUDED.voice_count, front_count=EXCLUDED.front_count, "
            "synthesized_by=EXCLUDED.synthesized_by, updated_at=now()",
            (domain_id, report.headline[:400] or d["blurb"], max(0, min(100, int(report.momentum))),
             len(papers), len(voices), kept, "llm:strong"))
    log.info("synthesized %s: %d fronts from %d papers / %d voices", domain_id, kept, len(papers), len(voices))
    return {"domain": domain_id, "fronts": kept, "papers": len(papers), "voices": len(voices)}


def synthesize_all(*, run_id: str | None = None) -> dict:
    run_id = run_id or llm.new_batch_run_id("synth")  # so the batch budget cap applies
    return {d["id"]: synthesize(d["id"], run_id=run_id) for d in DOMAINS}
