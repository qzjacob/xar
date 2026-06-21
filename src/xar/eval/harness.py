"""Offline eval harness. Retrieval hit-rate over a gold Q/A set + a report rubric
check (LLM-as-judge). Optional Arize Phoenix logging (`pip install '.[eval]'`)."""
from __future__ import annotations

import json
from pathlib import Path

from ..logging import get_logger
from ..retrieval import vector

log = get_logger("xar.eval")
_GOLD = Path(__file__).with_name("gold.json")


def load_gold() -> dict:
    return json.loads(_GOLD.read_text())


def eval_retrieval(k: int = 8) -> dict:
    gold = load_gold()["retrieval"]
    hits, total = 0, 0
    details = []
    for item in gold:
        total += 1
        res = vector.hybrid_search(item["q"], company_id=item.get("company_id"), k=k)
        joined = " ".join(h.text.lower() for h in res)
        ok = any(kw.lower() in joined for kw in item["expect_keywords"])
        hits += int(ok)
        details.append({"q": item["q"], "hit": ok, "n_results": len(res)})
    return {"hit_rate": round(hits / total, 3) if total else 0.0, "n": total, "details": details}


def eval_report_rubric(content_md: str) -> dict:
    from pydantic import BaseModel, Field

    from ..models import llm

    rubric = load_gold()["report_rubric"]

    class RubricScore(BaseModel):
        passed: list[bool] = Field(default_factory=list)
        notes: str = ""

    prompt = (
        "Score this report against each rubric item (true/false in order).\n"
        f"RUBRIC: {rubric}\n\nREPORT:\n{content_md[:9000]}"
    )
    s = llm.complete_json(prompt, RubricScore, node="eval_rubric", tier="fast", max_tokens=500)
    passed = s.passed[: len(rubric)]
    score = sum(passed) / len(rubric) if rubric else 0.0
    return {"rubric_score": round(score, 3), "items": dict(zip(rubric, passed)), "notes": s.notes}
