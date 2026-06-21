"""Evidence-coverage gate (the trust layer). Computes citation coverage, numeric
grounding, and an LLM-as-judge hallucination risk. Below threshold -> the report
is marked low-confidence so the human reviewer sees it before publication."""
from __future__ import annotations

import re

from ..models import llm
from .state import RunState

_CLAIM = re.compile(r"[.!?。！？]\s")
_CITE = re.compile(r"\[\d+\]")
_NUM = re.compile(r"\d")
COVERAGE_THRESHOLD = 0.55


def compute(state: RunState, content_md: str) -> dict:
    # strip the sources/disclaimer tail for fair coverage measurement
    body = content_md.split("## 引用来源")[0]
    sentences = [s for s in _CLAIM.split(body) if len(s.strip()) > 25]
    numeric = [s for s in sentences if _NUM.search(s)]
    cited_numeric = [s for s in numeric if _CITE.search(s)]
    coverage = (len(cited_numeric) / len(numeric)) if numeric else 1.0

    cites = state.citations
    numeric_grounding = (
        sum(1 for c in cites if c.get("tie_out_ok", True)) / len(cites) if cites else 1.0
    )

    judge = _judge(state, body)
    return {
        "evidence_coverage": round(coverage, 3),
        "numeric_grounding": round(numeric_grounding, 3),
        "hallucination_risk": judge.get("risk", 0.0),
        "judge_notes": judge.get("notes", ""),
        "passed": coverage >= COVERAGE_THRESHOLD and judge.get("risk", 1.0) < 0.5,
        "citation_count": len(cites),
    }


def _judge(state: RunState, body: str) -> dict:
    from pydantic import BaseModel, Field

    class Verdict(BaseModel):
        risk: float = Field(default=0.3, ge=0, le=1, description="prob. that some claim is unsupported")
        notes: str = Field(default="")

    prompt = (
        "You are a skeptical fact-check judge. Below is a draft research report whose "
        "claims should each be backed by a [n] citation. Estimate the probability (0-1) "
        "that one or more material claims (especially numbers) are NOT supported by a "
        "citation, and note the riskiest unsupported claim.\n\nREPORT:\n" + body[:8000]
    )
    v = llm.complete_json(prompt, Verdict, node="evidence_judge", run_id=state.run_id,
                          tier="fast", max_tokens=400)
    return {"risk": v.risk, "notes": v.notes}
