"""Bitemporal KG store on Postgres. Nodes/edges/events carry validity time AND
observation time; later docs never overwrite earlier-true facts (supersession is
explicit). Events dedup across sources via a content dedup_key."""
from __future__ import annotations

import hashlib
from datetime import date

from ..logging import get_logger
from ..storage import db

log = get_logger("xar.kg")


def upsert_node(node_id: str, node_type: str, name: str, *, aliases=None,
                tickers=None, attrs=None) -> str:
    db.execute(
        """INSERT INTO kg_nodes(id,node_type,name,aliases,tickers,attrs)
           VALUES(%s,%s,%s,%s,%s,%s)
           ON CONFLICT (id) DO UPDATE SET
             name=EXCLUDED.name,
             aliases=(SELECT ARRAY(SELECT DISTINCT unnest(kg_nodes.aliases || EXCLUDED.aliases))),
             tickers=(SELECT ARRAY(SELECT DISTINCT unnest(kg_nodes.tickers || EXCLUDED.tickers))),
             attrs=kg_nodes.attrs || EXCLUDED.attrs""",
        (node_id, node_type, name, aliases or [], tickers or [], _json(attrs or {})),
    )
    return node_id


def add_edge(src_id: str, dst_id: str, rel_type: str, *, valid_from=None, valid_to=None,
             confidence: float = 0.7, source_doc_id=None, license_tag=None,
             evidence: str | None = None) -> None:
    """Insert a relation. Dedup is bitemporal: an existing currently-valid edge is a
    duplicate ONLY when it covers the SAME validity window — so a later-asserted fact
    over a different window (e.g. Q3 re-stating a Q1 relation) is preserved, not dropped.
    When the same window is re-asserted by another source, that's corroboration: bump
    confidence toward 1.0 instead of discarding the new evidence."""
    existing = db.query(
        "SELECT id, confidence, source_doc_id, license_tag FROM kg_edges WHERE src_id=%s AND dst_id=%s "
        "AND rel_type=%s AND invalidated_at IS NULL AND t_valid_from IS NOT DISTINCT FROM %s "
        "AND t_valid_to IS NOT DISTINCT FROM %s",
        (src_id, dst_id, rel_type, valid_from, valid_to),
    )
    if existing:
        ex = existing[0]
        # Corroboration must come from an INDEPENDENT source. Skip self-reinforcement so
        # the graph stays idempotent and curated facts stay at their stated confidence:
        #  - seed edges (bootstrap_seed re-runs on every startup/build_kg) never drift;
        #  - a re-assertion carrying the same source doc never double-counts.
        same_source = source_doc_id is not None and source_doc_id == ex["source_doc_id"]
        if license_tag == "seed" or ex["license_tag"] == "seed" or same_source:
            return
        old = float(ex["confidence"] or 0.7)
        boosted = min(0.99, old + (1.0 - old) * 0.4)  # independent source -> diminishing-returns boost
        db.execute("UPDATE kg_edges SET confidence=%s WHERE id=%s", (boosted, ex["id"]))
        return
    attrs = {"evidence": evidence[:500]} if evidence else {}
    db.execute(
        """INSERT INTO kg_edges(src_id,dst_id,rel_type,attrs,t_valid_from,t_valid_to,
                                confidence,source_doc_id,license_tag)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (src_id, dst_id, rel_type, _json(attrs), valid_from, valid_to, confidence,
         source_doc_id, license_tag),
    )


def supersede_edge(edge_id: int) -> None:
    db.execute("UPDATE kg_edges SET invalidated_at=now() WHERE id=%s", (edge_id,))


def add_event(company_id, node_id, event_type, *, event_date=None, magnitude=None,
              polarity="neutral", tech_route_tag=None, summary="", confidence=0.7,
              source_doc_id=None, license_tag=None) -> bool:
    """Insert a catalyst/order event. Returns False if deduped against an
    existing event (same company+type+date+magnitude+route across sources)."""
    dedup = hashlib.sha256(
        f"{company_id}|{event_type}|{event_date}|{(magnitude or '').strip()}|{tech_route_tag or ''}".encode()
    ).hexdigest()[:32]
    rows = db.query("SELECT id FROM kg_events WHERE dedup_key=%s", (dedup,))
    if rows:
        return False
    db.execute(
        """INSERT INTO kg_events(company_id,node_id,event_type,event_date,magnitude,
                                 polarity,tech_route_tag,summary,confidence,
                                 source_doc_id,license_tag,dedup_key)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (dedup_key) DO NOTHING""",
        (company_id, node_id, event_type, event_date, magnitude, polarity,
         tech_route_tag, summary, confidence, source_doc_id, license_tag, dedup),
    )
    return True


def add_fundamental_from_extraction(company_id, metric, value, *, period=None,
                                    unit=None, source_doc_id=None) -> bool:
    """Bridge an LLM-extracted operating metric (ARR/NRR/RPO/book-to-bill/…) into
    the long `fundamentals` table as `source='extracted'`. The metric must resolve
    to a canonical KPI key or alias; the caller is responsible for grounding the
    evidence (so a hallucinated number never reaches the table). Returns True if
    written."""
    from ..ontology.metric_packs import canonical_kpi, spec
    from ..storage import structured

    key = canonical_kpi(metric)
    if not key or value is None:
        return False
    sp = spec(key)
    u = unit or (sp.unit if sp else "USD")
    structured.upsert_fundamental(
        company_id, key, float(value), period=period, unit=u, source="extracted",
        meta={"source_doc_id": source_doc_id} if source_doc_id else None,
    )
    return True


def bootstrap_seed() -> None:
    """Create company + tech-route nodes, seed structural edges, and alias table.
    Idempotent — safe to run on every startup."""
    from ..ingestion.registry import (COMPANIES, NODE_TYPE_BY_ROLE, SEED_EDGES,
                                       SEGMENTS, TECH_ROUTES)
    from ..ontology import EdgeType, NodeType
    from . import resolve

    for c in COMPANIES:
        ntype = NODE_TYPE_BY_ROLE.get(c.get("chain_role", ""), NodeType.MODULE_MAKER.value)
        upsert_node(c["id"], ntype, c["name"], aliases=c.get("aliases", []),
                    tickers=c.get("tickers", []), attrs={"region": c.get("region")})
        resolve.register_alias(c["name"], c["id"])
        for a in c.get("aliases", []):
            resolve.register_alias(a, c["id"])
        for t in c.get("tickers", []):
            resolve.register_alias(t, c["id"])
    for tr in TECH_ROUTES:
        upsert_node(tr["id"], NodeType.TECH_ROUTE.value, tr["name"], attrs=tr.get("attrs", {}))
        resolve.register_alias(tr["name"], tr["id"])
    # EndMarket nodes (one per chain segment) + competes_in edges = the industry-
    # landscape (行业格局) backbone: HHI/share for a segment is computed over the
    # companies that compete_in its EndMarket.
    for seg_id, meta in SEGMENTS.items():
        upsert_node(f"em_{seg_id}", NodeType.END_MARKET.value, f"{meta['name']} (end-market)",
                    attrs={"theme": meta.get("theme"), "tier": meta.get("tier"), "segment": seg_id})
    for src, dst, rel in SEED_EDGES:
        add_edge(src, dst, rel, confidence=0.9, license_tag="seed")
    for c in COMPANIES:
        for seg_id in dict.fromkeys((c.get("seg") or {}).values()):
            add_edge(c["id"], f"em_{seg_id}", EdgeType.COMPETES_IN.value,
                     confidence=0.8, license_tag="seed")
    log.info("KG seed bootstrapped (%d companies, %d tech routes, %d end-markets)",
             len(COMPANIES), len(TECH_ROUTES), len(SEGMENTS))


def _json(d: dict) -> str:
    import json

    return json.dumps(d, ensure_ascii=False, default=str)


def parse_date(v) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None
