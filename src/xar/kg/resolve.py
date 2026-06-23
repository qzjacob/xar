"""Deterministic entity resolution — a first-class layer that runs BEFORE KG
writes (design §5). Exact alias table -> trigram fuzzy match -> new node.

Fuzzy matches must clear `_FUZZY_THRESHOLD` to resolve; only HIGH-confidence
fuzzy matches (>= `_LEARN_THRESHOLD`) are written back as learned aliases, so a
single borderline match can't permanently bind a spelling to the wrong node."""
from __future__ import annotations

import hashlib
import re

from ..logging import get_logger
from ..storage import db

log = get_logger("xar.kg.resolve")
_FUZZY_THRESHOLD = 0.62   # min similarity to RESOLVE to an existing node
_LEARN_THRESHOLD = 0.85   # min similarity to CACHE the spelling as a learned alias

# English corporate-form suffixes stripped before matching.
_EN_SUFFIX = re.compile(
    r"[,\.]?\s+(inc|corp|corporation|co|ltd|holdings|holding|group|"
    r"technology|technologies|networks|systems)\b\.?", re.IGNORECASE)
# Chinese corporate-form suffixes (bilingual platform): "中际旭创股份有限公司" -> "中际旭创".
_CN_SUFFIX = re.compile(
    r"(股份有限公司|有限责任公司|有限公司|集团股份|控股集团|集团|控股|科技|股份|公司)$")


def normalize(name: str) -> str:
    s = (name or "").strip().lower()
    s = _EN_SUFFIX.sub("", s)
    # strip Chinese suffixes iteratively (e.g. "...科技股份有限公司")
    prev = None
    while prev != s:
        prev = s
        s = _CN_SUFFIX.sub("", s).strip()
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
        sim = float(cand[0]["sim"])
        # only cache as a learned alias when the match is HIGH-confidence — a
        # borderline 0.62 match resolves for this call but is not made permanent.
        if sim >= _LEARN_THRESHOLD:
            register_alias(name, node_id, source="learned")
        return node_id, sim
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
