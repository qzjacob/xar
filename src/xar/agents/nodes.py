"""Pipeline nodes. Every node grounds its output in retrieval (pgvector hybrid)
and the KG; no claim is made without a citation marker [n]."""
from __future__ import annotations

from ..kg import resolve
from ..logging import get_logger
from ..models import llm
from ..retrieval import graphrag, vector
from .state import RunState

log = get_logger("xar.agents")


def _ground(state: RunState, query: str, company_id: str | None, k: int = 8,
            numeric: bool = False) -> str:
    hits = vector.hybrid_search(query, company_id=company_id, k=k, numeric=numeric)
    blocks = []
    for h in hits:
        n = state.cite(h.citation())
        flag = "" if h.tie_out_ok else " [UNVERIFIED-NUMERIC]"
        blocks.append(f"[{n}]{flag} ({h.source}/{h.doc_type}) {h.text[:900]}")
    return "\n\n".join(blocks) if blocks else "(no retrieved evidence)"


# --- Node 1: scope / planner ----------------------------------------------
def scope(state: RunState) -> None:
    req = state.request
    company_id = req.get("company_id")
    if not company_id and req.get("company"):
        rid, _ = resolve.resolve(req["company"])
        company_id = rid
    node = graphrag.node(company_id) if company_id else None
    state.put("company_id", company_id)
    state.put("company_name", node["name"] if node else (req.get("company") or company_id))
    log.info("scope: company=%s kind=%s", company_id, state.kind)


# --- Node 2: graph retrieve -----------------------------------------------
def graph_retrieve(state: RunState) -> None:
    cid = state.get("company_id")
    if not cid:
        state.put("graph", {})
        return
    sc = graphrag.supply_chain(cid)
    since = state.request.get("since")
    evs = graphrag.events(cid, since=since, limit=40)
    state.put("graph", {
        "suppliers": [f"{e['src_name']} -> {e['dst_name']} ({e['rel_type']})" for e in sc["suppliers"]],
        "customers": [f"{e['src_name']} -> {e['dst_name']}" for e in sc["customers"]],
        "invests_in": [f"{e['src_name']} -> {e['dst_name']}" for e in sc["invests_in"]],
        "single_source_risks": [f"{e['src_name']} / {e['dst_name']}" for e in sc["single_source_risks"]],
        "events": [
            {"type": e["event_type"], "date": str(e["event_date"]), "polarity": e["polarity"],
             "magnitude": e["magnitude"], "route": e["tech_route_tag"], "summary": e["summary"]}
            for e in evs
        ],
    })
    log.info("graph_retrieve: %d events, %d suppliers", len(evs), len(sc["suppliers"]))


def _graph_brief(state: RunState) -> str:
    g = state.get("graph", {})
    if not g:
        return "(no graph context)"
    lines = ["SUPPLY CHAIN (from knowledge graph):"]
    lines += [f"- supplier: {s}" for s in g.get("suppliers", [])[:12]]
    lines += [f"- customer: {c}" for c in g.get("customers", [])[:12]]
    lines += [f"- equity: {i}" for i in g.get("invests_in", [])]
    lines += [f"- SINGLE-SOURCE RISK: {r}" for r in g.get("single_source_risks", [])]
    lines.append("\nRECENT CATALYST/ORDER EVENTS:")
    for e in g.get("events", [])[:20]:
        lines.append(f"- [{e['date']}] {e['type']} ({e['polarity']}) route={e['route']} "
                     f"mag={e['magnitude']}: {e['summary']}")
    return "\n".join(lines)


# --- Node 3: analysts ------------------------------------------------------
def analyst(state: RunState, name: str, query: str, instruction: str, *,
            tier: str = "fast", numeric: bool = False) -> None:
    cid = state.get("company_id")
    evidence = _ground(state, query, cid, k=8, numeric=numeric)
    graph_brief = _graph_brief(state)
    prompt = (
        f"Company: {state.get('company_name')}\n\n{graph_brief}\n\n"
        f"RETRIEVED EVIDENCE (cite by [n]):\n{evidence}\n\n"
        f"TASK ({name}): {instruction}\n"
        "Write 4-8 tight bullet findings. Cite every factual claim with [n] from "
        "the evidence or reference a graph event. Do NOT state numbers you cannot cite. "
        "If evidence is thin, say so explicitly."
    )
    text = llm.complete(prompt, tier=tier, node=f"analyst:{name}", run_id=state.run_id,
                        max_tokens=1200)
    state.state.setdefault("findings", {})[name] = text


ANALYSTS = [
    ("fundamental", "revenue growth margins guidance financial results",
     "Assess financial trajectory: revenue growth, gross-margin mix shift toward AI, guidance.", "fast", True),
    ("catalyst", "order capacity expansion qualification customer 800G 1.6T capex",
     "Identify the most material dated catalysts/orders and their polarity for the company.", "fast", False),
    ("supply_chain", "supplier customer EML DSP single source qualification NVIDIA",
     "Map the company's position: key suppliers, customers, single-source exposure, tech-route bets.", "fast", False),
    ("sentiment", "demand outlook commentary risks competition",
     "Summarize sentiment and forward demand signals from news/filings (bilingual).", "fast", False),
    ("valuation", "valuation multiple growth margin demand TAM 1.6T",
     "Give a valuation perspective tied to the demand clock (GPU launch cadence) and margin mix.", "strong", True),
]


def run_analysts(state: RunState) -> None:
    for name, query, instr, tier, numeric in ANALYSTS:
        analyst(state, name, query, instr, tier=tier, numeric=numeric)
