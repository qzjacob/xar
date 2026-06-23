"""Bounded bull/bear debate subgraph — the only emergent-autonomy region.
Capped rounds, strong tier, grounded only in the analysts' cited findings."""
from __future__ import annotations

from ..models import llm
from .state import RunState

_ROUNDS = 2


def _findings_brief(state: RunState) -> str:
    return "\n\n".join(f"### {k}\n{v}" for k, v in state.get("findings", {}).items())


def run_debate(state: RunState) -> None:
    from .nodes import _theme_terms
    company = state.get("company_name")
    findings = _findings_brief(state)
    risk_hint = _theme_terms(state.get("company_id"))["risk"]
    bull, bear = "", ""
    for r in range(_ROUNDS):
        bull = llm.complete(
            f"Company: {company}\nAnalyst findings (cited [n]):\n{findings}\n\n"
            f"Prior bear case:\n{bear or '(none yet)'}\n\n"
            "You are the BULL. Make the strongest evidence-grounded case for upside. "
            "Rebut the bear. Keep every claim tied to a [n] citation from the findings. "
            "5-7 bullets.",
            tier="strong", node=f"debate:bull:{r}", run_id=state.run_id, max_tokens=1100,
        )
        bear = llm.complete(
            f"Company: {company}\nAnalyst findings (cited [n]):\n{findings}\n\n"
            f"Bull case to rebut:\n{bull}\n\n"
            "You are the BEAR. Make the strongest evidence-grounded case for downside/risk "
            f"(consider: {risk_hint}). Cite [n]. 5-7 bullets.",
            tier="strong", node=f"debate:bear:{r}", run_id=state.run_id, max_tokens=1100,
        )
    state.put("bull_case", bull)
    state.put("bear_case", bear)


def run_risk(state: RunState) -> None:
    from .nodes import _theme_terms
    risk_hint = _theme_terms(state.get("company_id"))["risk"]
    state.put("risk", llm.complete(
        f"Company: {state.get('company_name')}\n\n"
        f"Bull:\n{state.get('bull_case','')}\n\nBear:\n{state.get('bear_case','')}\n\n"
        "As the RISK manager, stress-test the thesis. Enumerate the 4-6 risks that would "
        f"most change the conclusion (consider: {risk_hint}), each with a severity and what "
        "evidence would confirm/deny it. Cite [n] where possible.",
        tier="strong", node="risk", run_id=state.run_id, max_tokens=1100,
    ))
