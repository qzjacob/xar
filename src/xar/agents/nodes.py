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


# Per-theme retrieval terms + the demand-clock framing, so a software/space/robotics
# company is NOT searched with optical-module keywords. Each entry feeds the catalyst /
# supply-chain / valuation analyst queries and the valuation framing.
_THEME_TERMS = {
    "ai_optical": {
        "catalyst": "order capacity expansion qualification customer 800G 1.6T CPO capex",
        "supply": "supplier customer EML DSP single source qualification NVIDIA",
        "valuation": "valuation multiple growth margin TAM 1.6T", "clock": "GPU launch cadence",
        "risk": "CPO/LPO substitution of DSP attach, EML undersupply, customer concentration, valuation"},
    "ai_chip": {
        "catalyst": "order capacity foundry HBM CoWoS advanced packaging tape-out capex",
        "supply": "supplier customer HBM CoWoS substrate single source TSMC NVIDIA",
        "valuation": "valuation multiple growth margin wafer ASP TAM", "clock": "GPU/accelerator launch cadence",
        "risk": "HBM/CoWoS capacity tightness, foundry single-source, export controls, customer concentration"},
    "ai_software": {
        "catalyst": "ARR net revenue retention seats AI agent attach product launch bookings",
        "supply": "platform ecosystem partners hyperscaler model providers competitive moat",
        "valuation": "valuation EV/revenue Rule-of-40 NRR growth durability", "clock": "enterprise AI adoption wave",
        "risk": "AI-native disruption, seat compression from agents, NRR decay, platform/hyperscaler dependency, valuation"},
    "space_exploration": {
        "catalyst": "launch cadence reusability contract award constellation deployment capacity",
        "supply": "supplier customer launch provider satellite bus propulsion single source",
        "valuation": "valuation backlog launch ASP constellation TAM", "clock": "launch cadence & constellation buildout",
        "risk": "launch failure/delay, SpaceX cost dominance, contract concentration, regulatory/spectrum, capital intensity"},
    "humanoid_robotics": {
        "catalyst": "unit volume pilot deployment order actuator capacity product launch",
        "supply": "supplier harmonic reducer roller screw actuator motor sensor single source",
        "valuation": "valuation unit economics BOM cost volume ramp TAM", "clock": "humanoid volume ramp",
        "risk": "volume-ramp slippage, BOM cost, actuator/reducer single-source, embodied-AI maturity, customer concentration"},
    # consumer cycle themes — framed by the consumer/economic cycle, not a supply chain
    "internet": {
        "catalyst": "MAU DAU engagement ARPU ad load gross bookings subscriber net adds take rate product launch",
        "supply": "platform competition network effects regulatory app store dependency moat",
        "valuation": "valuation EV/revenue user growth ARPU LTV/CAC bookings durability",
        "clock": "consumer & advertising cycle rotation",
        "risk": "ad-budget cyclicality, user/engagement saturation, platform/app-store dependency, regulatory, valuation"},
    "retail": {
        "catalyst": "same-store sales comparable sales traffic ticket inventory store openings guidance",
        "supply": "consumer demand pricing promotion private label vendor freight wage cost",
        "valuation": "valuation P/E comps inventory turns margin trade-down exposure",
        "clock": "consumer-spending cycle (trade-down vs discretionary)",
        "risk": "discretionary demand softening, inventory/markdown risk, wage & freight cost, trade-down share shift, valuation"},
    "restaurants": {
        "catalyst": "same-store sales traffic check average unit volume unit growth digital mix franchising guidance",
        "supply": "food labor cost commodity franchisee health pricing throughput delivery",
        "valuation": "valuation P/E unit economics AUV restaurant-level margin unit growth runway",
        "clock": "consumer dining cycle (QSR trade-down vs casual)",
        "risk": "discretionary dining pullback, food & labor inflation, traffic softness, franchisee health, valuation"},
}
_DEFAULT_TERMS = {
    "catalyst": "order capacity expansion qualification customer capex product launch",
    "supply": "supplier customer single source qualification key partner",
    "valuation": "valuation multiple growth margin demand TAM", "clock": "AI demand cycle",
    "risk": "demand cyclicality, single-source exposure, customer concentration, competition, valuation"}


def _theme_terms(company_id: str | None) -> dict:
    from ..ingestion.registry import company_by_id
    if company_id:
        c = company_by_id(company_id)
        if c:
            for t in c.get("themes", []):
                if t in _THEME_TERMS:
                    return _THEME_TERMS[t]
    return _DEFAULT_TERMS


def run_analysts(state: RunState) -> None:
    t = _theme_terms(state.get("company_id"))
    analysts = [
        ("fundamental", "revenue growth margins guidance financial results",
         "Assess financial trajectory: revenue growth, gross-margin mix shift toward AI, guidance.", "fast", True),
        ("catalyst", t["catalyst"],
         "Identify the most material dated catalysts/orders and their polarity for the company.", "fast", False),
        ("supply_chain", t["supply"],
         "Map the company's position: key suppliers, customers, single-source exposure, tech-route bets.", "fast", False),
        ("sentiment", "demand outlook commentary risks competition",
         "Summarize sentiment and forward demand signals from news/filings (bilingual).", "fast", False),
        ("valuation", t["valuation"],
         f"Give a valuation perspective tied to the demand clock ({t['clock']}) and margin mix.", "strong", True),
    ]
    for name, query, instr, tier, numeric in analysts:
        analyst(state, name, query, instr, tier=tier, numeric=numeric)
