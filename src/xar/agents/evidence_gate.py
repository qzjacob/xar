"""Evidence-coverage gate (the trust layer). Computes citation coverage, numeric
grounding, and an LLM-as-judge hallucination risk. Below threshold -> the report
is marked low-confidence so the human reviewer sees it before publication.

Hardened (review §1.1): citation markers are range-validated against the registered
citations (a hallucinated `[99]` no longer counts as coverage); only genuine *figures*
count as numeric claims (not "Q3"/"2024"/"Step 2"); and the judge is shown the actual
cited source chunks so it can assess groundedness rather than mere plausibility."""
from __future__ import annotations

import re

from ..models import llm
from ..storage import db
from .state import RunState

_CLAIM = re.compile(r"[.!?。！？]\s")
_CITE = re.compile(r"\[(\d+)\]")
# A quantitative claim worth grounding: currency, a decimal, a percent, a magnitude
# word, or comma-grouped thousands. Bare years / quarters / "Step 2" deliberately
# do NOT match, so they can't inflate the numeric denominator.
_FINNUM = re.compile(
    r"[$¥€]\s?\d|\d[\d,]*\.\d|\d\s?%|\d[\d.]*\s?(亿|万|億|billion|million|trillion|bn|mn)\b|\d{1,3}(,\d{3})+",
    re.IGNORECASE)
COVERAGE_THRESHOLD = 0.55


def _has_valid_cite(sentence: str, n_cites: int) -> bool:
    """A `[n]` marker counts only when n is a real registered citation (1..n_cites)."""
    return any(1 <= int(m) <= n_cites for m in _CITE.findall(sentence))


def compute(state: RunState, content_md: str) -> dict:
    # strip the sources/disclaimer tail for fair coverage measurement
    body = content_md.split("## 引用来源")[0]
    cites = state.citations
    n_cites = len(cites)
    sentences = [s for s in _CLAIM.split(body) if len(s.strip()) > 25]
    numeric = [s for s in sentences if _FINNUM.search(s)]
    cited_numeric = [s for s in numeric if _has_valid_cite(s, n_cites)]
    coverage = (len(cited_numeric) / len(numeric)) if numeric else 1.0

    numeric_grounding = (
        sum(1 for c in cites if c.get("tie_out_ok", True)) / n_cites if cites else 1.0
    )

    judge = _judge(state, body)
    return {
        "evidence_coverage": round(coverage, 3),
        "numeric_grounding": round(numeric_grounding, 3),
        "hallucination_risk": judge.get("risk", 0.0),
        "judge_notes": judge.get("notes", ""),
        "passed": coverage >= COVERAGE_THRESHOLD and judge.get("risk", 1.0) < 0.5,
        "citation_count": n_cites,
    }


def _cited_sources(state: RunState, limit: int = 10) -> str:
    """Fetch the actual text of the cited chunks so the judge can check groundedness."""
    ids = [c.get("chunk_id") for c in state.citations[:limit] if c.get("chunk_id")]
    if not ids:
        return "(no source chunks available)"
    rows = db.query("SELECT id, text FROM chunks WHERE id = ANY(%s)", (ids,))
    by_id = {r["id"]: r["text"] for r in rows}
    out = []
    for i, c in enumerate(state.citations[:limit], start=1):
        txt = (by_id.get(c.get("chunk_id")) or "")[:600]
        if txt:
            out.append(f"[{i}] {txt}")
    return "\n\n".join(out) or "(no source chunks available)"


def _judge(state: RunState, body: str) -> dict:
    from pydantic import BaseModel, Field

    class Verdict(BaseModel):
        # default 0.6 (> 0.5 threshold): a judge that fails to return valid JSON must
        # NOT auto-pass the report — failure is treated as elevated risk.
        risk: float = Field(default=0.6, ge=0, le=1, description="prob. that some claim is unsupported")
        notes: str = Field(default="")

    sources = _cited_sources(state)
    prompt = (
        "You are a skeptical fact-check judge. Below is a draft research report whose "
        "claims should each be backed by a [n] citation, followed by the ACTUAL TEXT of "
        "the cited sources. Check whether the report's material claims (especially numbers) "
        "are genuinely SUPPORTED BY the cited source text. Estimate the probability (0-1) "
        "that one or more material claims are NOT grounded in the sources, and note the "
        "riskiest unsupported claim.\n\nREPORT:\n" + body[:8000] +
        "\n\nCITED SOURCES:\n" + sources[:6000]
    )
    v = llm.complete_json(prompt, Verdict, node="evidence_judge", run_id=state.run_id,
                          tier="fast", max_tokens=400)
    return {"risk": v.risk, "notes": v.notes}
