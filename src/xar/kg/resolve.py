"""Deterministic entity resolution — a first-class layer that runs BEFORE KG
writes (design §5). Exact alias table -> trigram fuzzy match -> new node.
High-stakes edges are flagged for human review when confidence is low."""
from __future__ import annotations

import hashlib
import re

from ..logging import get_logger
from ..storage import db

log = get_logger("xar.kg.resolve")
_FUZZY_THRESHOLD = 0.55


def normalize(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"[,\.]?\s+(inc|corp|corporation|co|ltd|holdings|technology|technologies|networks|systems)\b\.?", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def register_alias(alias: str, node_id: str, source: str = "seed") -> None:
    norm = normalize(alias)
    if not norm:
        return
    db.execute(
        "INSERT INTO entity_aliases(alias_norm,node_id,source) VALUES(%s,%s,%s) "
        "ON CONFLICT (alias_norm) DO NOTHING",
        (norm, node_id, source),
    )


def resolve(name: str) -> tuple[str | None, float]:
    """Return (node_id, confidence). node_id None means 'no confident match'."""
    norm = normalize(name)
    if not norm:
        return None, 0.0
    exact = db.query("SELECT node_id FROM entity_aliases WHERE alias_norm=%s", (norm,))
    if exact:
        return exact[0]["node_id"], 1.0
    # trigram fuzzy over node names + aliases
    cand = db.query(
        """SELECT id, name, similarity(lower(name), %s) AS sim
           FROM kg_nodes ORDER BY sim DESC LIMIT 1""",
        (norm,),
    )
    if cand and cand[0]["sim"] and cand[0]["sim"] >= _FUZZY_THRESHOLD:
        node_id = cand[0]["id"]
        register_alias(name, node_id, source="learned")  # cache the learned alias
        return node_id, float(cand[0]["sim"])
    return None, 0.0


def resolve_or_create(name: str, node_type: str, *, tickers=None, attrs=None) -> tuple[str, bool]:
    """Resolve to an existing node or create a new one. Returns (node_id, created)."""
    from . import store

    node_id, conf = resolve(name)
    if node_id:
        return node_id, False
    new_id = "ent_" + hashlib.sha256(normalize(name).encode()).hexdigest()[:16]
    store.upsert_node(new_id, node_type, name, tickers=tickers or [], attrs=attrs or {})
    register_alias(name, new_id, source="learned")
    return new_id, True
