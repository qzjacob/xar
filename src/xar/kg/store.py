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
             confidence: float = 0.7, source_doc_id=None, license_tag=None) -> None:
    # skip if an identical currently-valid edge already exists (dedup)
    existing = db.query(
        "SELECT id FROM kg_edges WHERE src_id=%s AND dst_id=%s AND rel_type=%s "
        "AND invalidated_at IS NULL",
        (src_id, dst_id, rel_type),
    )
    if existing:
        return
    db.execute(
        """INSERT INTO kg_edges(src_id,dst_id,rel_type,t_valid_from,t_valid_to,
                                confidence,source_doc_id,license_tag)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
        (src_id, dst_id, rel_type, valid_from, valid_to, confidence, source_doc_id, license_tag),
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


def bootstrap_seed() -> None:
    """Create company + tech-route nodes, seed structural edges, and alias table.
    Idempotent — safe to run on every startup."""
    from ..ingestion.registry import (COMPANIES, NODE_TYPE_BY_ROLE, SEED_EDGES,
                                       TECH_ROUTES)
    from ..ontology import NodeType
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
    for src, dst, rel in SEED_EDGES:
        add_edge(src, dst, rel, confidence=0.9, license_tag="seed")
    log.info("KG seed bootstrapped (%d companies, %d tech routes)", len(COMPANIES), len(TECH_ROUTES))


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
