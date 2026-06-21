"""GraphRAG: query the bitemporal industry-chain KG. Entity/temporal retrieval —
'who supplies X', 'orders since date Y', 'single-source risks', 'what changed'."""
from __future__ import annotations

from ..storage import db


def node(node_id: str) -> dict | None:
    rows = db.query("SELECT * FROM kg_nodes WHERE id=%s", (node_id,))
    return rows[0] if rows else None


def neighbors(node_id: str, rel_types: list[str] | None = None,
              as_of: str | None = None) -> list[dict]:
    """Edges touching node_id, optionally valid as_of a date (bitemporal)."""
    sql = """SELECT e.*, ns.name AS src_name, nd.name AS dst_name
             FROM kg_edges e
             JOIN kg_nodes ns ON ns.id = e.src_id
             JOIN kg_nodes nd ON nd.id = e.dst_id
             WHERE (e.src_id=%s OR e.dst_id=%s) AND e.invalidated_at IS NULL"""
    params: list = [node_id, node_id]
    if rel_types:
        sql += " AND e.rel_type = ANY(%s)"
        params.append(rel_types)
    if as_of:
        sql += " AND (e.t_valid_from IS NULL OR e.t_valid_from <= %s) AND (e.t_valid_to IS NULL OR e.t_valid_to >= %s)"
        params += [as_of, as_of]
    return db.query(sql, params)


def supply_chain(company_id: str) -> dict:
    """Upstream suppliers, downstream customers, tech routes, and risk edges."""
    edges = neighbors(company_id)
    suppliers = [e for e in edges if e["rel_type"] in ("supplies", "second_sources") and e["dst_id"] == company_id]
    customers = [e for e in edges if e["rel_type"] == "supplies" and e["src_id"] == company_id]
    invests = [e for e in edges if e["rel_type"] == "invests_in"]
    tech = [e for e in edges if e["rel_type"] == "uses_techroute"]
    risks = single_source_risks(company_id)
    return {"suppliers": suppliers, "customers": customers, "invests_in": invests,
            "tech_routes": tech, "single_source_risks": risks}


def single_source_risks(company_id: str | None = None) -> list[dict]:
    sql = """SELECT e.*, ns.name AS src_name, nd.name AS dst_name FROM kg_edges e
             JOIN kg_nodes ns ON ns.id=e.src_id JOIN kg_nodes nd ON nd.id=e.dst_id
             WHERE e.rel_type='single_source_risk' AND e.invalidated_at IS NULL"""
    params: list = []
    if company_id:
        sql += " AND (e.src_id=%s OR e.dst_id=%s)"
        params += [company_id, company_id]
    return db.query(sql, params)


def events(company_id: str | None = None, since: str | None = None,
           types: list[str] | None = None, limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM kg_events WHERE invalidated_at IS NULL"
    params: list = []
    if company_id:
        sql += " AND company_id=%s"
        params.append(company_id)
    if since:
        sql += " AND event_date >= %s"
        params.append(since)
    if types:
        sql += " AND event_type = ANY(%s)"
        params.append(types)
    sql += " ORDER BY event_date DESC NULLS LAST LIMIT %s"
    params.append(limit)
    return db.query(sql, params)


def changes_since(company_id: str, observed_after: str) -> dict:
    """For tracking summaries: new events + superseded facts since a timestamp."""
    new_events = db.query(
        "SELECT * FROM kg_events WHERE company_id=%s AND observed_at >= %s "
        "AND invalidated_at IS NULL ORDER BY observed_at DESC",
        (company_id, observed_after),
    )
    superseded = db.query(
        "SELECT * FROM kg_edges WHERE (src_id=%s OR dst_id=%s) AND invalidated_at >= %s",
        (company_id, company_id, observed_after),
    )
    return {"new_events": new_events, "superseded_edges": superseded}
