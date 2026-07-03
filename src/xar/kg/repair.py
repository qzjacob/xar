"""KG orphan repair: kg_events rows whose company_id is not a watched company —
typically an ad-hoc ent_* node created by resolve_or_create() before the universe
grew to cover the name (audit: 152 rows / 71 orphan ids).

Resolution is deterministic and conservative (same normalization + thresholds as
xar.kg.resolve): exact registry name/alias/ticker match, then a learned/seed
entity_aliases entry already bound to a watched company, then a high-confidence
trigram match (>= resolve._LEARN_THRESHOLD). A confident match re-points the
events (and the orphan's alias spellings, so the same name resolves straight to
the company next time); everything else is tagged attrs.orphan=true and left.
Also backfills kg_events.theme/segment from the company's ontology anchor where
NULL (the same anchor rule add_event applies at insert time). Idempotent."""
from __future__ import annotations

from ..logging import get_logger
from ..storage import db
from . import resolve
from .store import _anchor

log = get_logger("xar.kg.repair")

# Only high-confidence fuzzy matches may rebind rows — reuse the resolve layer's
# learn threshold (a borderline resolve-level match must NOT rewrite history).
_CONFIDENT = resolve._LEARN_THRESHOLD


def _company_lookup() -> dict[str, str]:
    """Normalized company name/alias/ticker -> company id. Deterministic: rows are
    scanned in id order and the first writer wins, so re-runs always resolve an
    ambiguous spelling (duplicate roster entries) to the same id."""
    lut: dict[str, str] = {}
    for c in db.query("SELECT id, name, aliases, tickers FROM companies ORDER BY id"):
        for raw in (c["name"], *(c["aliases"] or []), *(c["tickers"] or [])):
            norm = resolve.normalize(raw or "")
            if norm and norm not in lut:
                lut[norm] = c["id"]
    return lut


def _candidate_names(orphan_id: str) -> list[str]:
    rows = db.query("SELECT name, aliases, tickers FROM kg_nodes WHERE id=%s", (orphan_id,))
    if not rows:
        return [orphan_id]  # bare id with no node — try the id string itself
    r = rows[0]
    return [r["name"], *(r["aliases"] or []), *(r["tickers"] or [])]


def _resolve_orphan(orphan_id: str, lut: dict[str, str]) -> tuple[str | None, float, str]:
    """Resolve an orphan id to a watched company: (company_id, confidence, how).
    company_id None means 'no confident match' — the caller tags, never guesses."""
    norms = [n for n in (resolve.normalize(x or "") for x in _candidate_names(orphan_id)) if n]
    for norm in norms:  # 1) exact registry name/alias/ticker
        cid = lut.get(norm)
        if cid:
            return cid, 1.0, "exact"
    for norm in norms:  # 2) alias-table entry already bound to a watched company
        hit = db.query(
            "SELECT a.node_id FROM entity_aliases a JOIN companies c ON c.id=a.node_id "
            "WHERE a.alias_norm=%s AND a.node_id<>%s", (norm, orphan_id))
        if hit:
            return hit[0]["node_id"], 1.0, "alias"
    best_id, best_sim = None, 0.0  # 3) trigram fuzzy over company names + aliases
    for norm in norms:
        cand = db.query(
            "SELECT c.id, similarity(lower(al), %s) AS sim "
            "FROM companies c, unnest(c.aliases || ARRAY[c.name]) AS al "
            "ORDER BY sim DESC NULLS LAST LIMIT 1", (norm,))
        if cand and cand[0]["sim"] and float(cand[0]["sim"]) > best_sim:
            best_id, best_sim = cand[0]["id"], float(cand[0]["sim"])
    if best_id and best_sim >= _CONFIDENT:
        return best_id, best_sim, "fuzzy"
    return None, best_sim, "none"


def backfill_event_anchors() -> int:
    """Fill kg_events.theme/segment from the company's ontology anchor where NULL
    (reuses kg.store._anchor — the rule add_event applies at insert time). Segment
    is only written alongside a NULL theme or onto rows already sitting on the
    anchor theme, so a segment from one theme never leaks into another. Returns
    the number of rows updated."""
    rows = db.query(
        "SELECT DISTINCT e.company_id FROM kg_events e JOIN companies c ON c.id=e.company_id "
        "WHERE e.theme IS NULL OR e.segment IS NULL ORDER BY 1")
    n = 0
    for r in rows:
        theme, seg = _anchor(r["company_id"])
        if theme is None:
            continue
        if seg is None:
            pred, args = "company_id=%s AND theme IS NULL", (r["company_id"],)
        else:
            pred = "company_id=%s AND (theme IS NULL OR (theme=%s AND segment IS NULL))"
            args = (r["company_id"], theme)
        cnt = db.query(f"SELECT count(*) AS c FROM kg_events WHERE {pred}", args)[0]["c"]
        if not cnt:
            continue
        db.execute(
            f"UPDATE kg_events SET theme=COALESCE(theme,%s), segment=COALESCE(segment,%s) "
            f"WHERE {pred}", (theme, seg, *args))
        n += cnt
    return n


def repair_orphan_events(verbose: bool = True) -> dict:
    """Repair kg_events rows whose company_id is not in companies. Confident
    matches get their rows (and alias spellings) re-pointed to the watched
    company and any stale attrs.orphan flag cleared; unresolvable ids are tagged
    attrs.orphan=true and left for a human. Then theme/segment anchors are
    backfilled for all watched-company events. Idempotent; returns (and prints)
    a report dict."""
    orphans = db.query(
        "SELECT e.company_id AS orphan_id, count(*) AS n FROM kg_events e "
        "WHERE e.company_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM companies c WHERE c.id=e.company_id) "
        "GROUP BY 1 ORDER BY 1")
    lut = _company_lookup()
    repaired: dict[str, dict] = {}
    repaired_rows = tagged_rows = 0
    tagged_ids: list[str] = []
    for row in orphans:
        oid = row["orphan_id"]
        cid, conf, how = _resolve_orphan(oid, lut)
        if cid:
            db.execute(
                "UPDATE kg_events SET company_id=%s, attrs=attrs - 'orphan' "
                "WHERE company_id=%s", (cid, oid))
            # Re-point the orphan's alias spellings so future extraction resolves
            # the same name straight to the watched company (no new orphans).
            db.execute(
                "UPDATE entity_aliases SET node_id=%s, source='learned' WHERE node_id=%s",
                (cid, oid))
            repaired[oid] = {"company_id": cid, "confidence": round(conf, 2),
                             "via": how, "rows": row["n"]}
            repaired_rows += row["n"]
        else:
            db.execute(
                """UPDATE kg_events SET attrs = attrs || '{"orphan": true}'::jsonb """
                "WHERE company_id=%s", (oid,))
            tagged_ids.append(oid)
            tagged_rows += row["n"]
    anchored = backfill_event_anchors()
    report = {
        "orphan_ids": len(orphans), "orphan_rows": sum(r["n"] for r in orphans),
        "repaired_ids": len(repaired), "repaired_rows": repaired_rows,
        "tagged_ids": len(tagged_ids), "tagged_rows": tagged_rows,
        "anchored_rows": anchored, "repaired": repaired,
    }
    log.info("orphan repair: %d/%d ids repaired (%d rows), %d tagged, %d anchored",
             len(repaired), len(orphans), repaired_rows, len(tagged_ids), anchored)
    if verbose:
        print(report)
    return report
