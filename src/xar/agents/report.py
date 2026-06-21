"""Editor synthesis (Data-CoT -> Concept-CoT -> Thesis-CoT) + report assembly.
Three products from one graph: deep report, tracking summary, takeaways."""
from __future__ import annotations

from ..models import llm
from .state import RunState

DISCLAIMER = (
    "\n\n---\n*免责声明 / Disclaimer: 本报告由 XAR 自动生成，仅为信息聚合与研究辅助，"
    "**不构成投资建议**。所有结论需经人工复核；数据来源见引用。*"
)


def _findings_brief(state: RunState) -> str:
    return "\n\n".join(f"### {k}\n{v}" for k, v in state.get("findings", {}).items())


def synthesize(state: RunState) -> str:
    company = state.get("company_name")
    if state.kind == "tracking_summary":
        instr = ("Write a TRACKING SUMMARY: what materially changed since the last "
                 "snapshot — new orders, qualifications, capex revisions, catalyst "
                 "polarity flips. Lead with the single most important change.")
    elif state.kind == "takeaways":
        instr = ("Write 5-8 INVESTMENT TAKEAWAYS as crisp bullets, each citing [n] or a graph event.")
    else:
        instr = ("Write a DEEP REPORT with sections: 1) Snapshot & thesis, "
                 "2) Supply-chain position (suppliers/customers/single-source risk), "
                 "3) Catalysts & orders (dated), 4) Bull vs Bear, 5) Risks, "
                 "6) Valuation & demand clock, 7) What to watch next.")

    prompt = (
        f"Company: {company}\n\n"
        f"ANALYST FINDINGS:\n{_findings_brief(state)}\n\n"
        f"BULL CASE:\n{state.get('bull_case','')}\n\n"
        f"BEAR CASE:\n{state.get('bear_case','')}\n\n"
        f"RISK ASSESSMENT:\n{state.get('risk','')}\n\n"
        "Reason internally Data -> Concept -> Thesis, then write the report.\n"
        f"INSTRUCTION: {instr}\n"
        "Markdown. PRESERVE all [n] citation markers from the inputs — every factual/numeric "
        "claim must keep its [n]. Do not invent citations or numbers. Be decisive but grounded."
    )
    body = llm.complete(prompt, tier="strong", node="editor", run_id=state.run_id, max_tokens=4000)
    return f"# {company} — {_title(state.kind)}\n\n{body}\n\n{_sources(state)}{DISCLAIMER}"


def _title(kind: str) -> str:
    return {"deep_report": "深度报告 Deep Report",
            "tracking_summary": "跟踪摘要 Tracking Summary",
            "takeaways": "投资启示 Takeaways"}.get(kind, "Report")


def _sources(state: RunState) -> str:
    lines = ["## 引用来源 / Sources"]
    for i, c in enumerate(state.citations, 1):
        flag = "" if c.get("tie_out_ok", True) else " ⚠未通过数值对账"
        url = f" <{c['url']}>" if c.get("url") else ""
        lines.append(f"[{i}] {c.get('title','?')} ({c.get('source')}/{c.get('doc_type')}){url}{flag}")
    return "\n".join(lines)
