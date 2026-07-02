"""Postgres-backed blotter store for the vendored Fenny options desk.

Drop-in replacement for `fcn.options.blotter.BlotterStore` (the file store at
`~/.fcn/blotter.json`): same CRUD surface + inherited `.aggregate()`, but rows live in
the `fenny_blotter` table so the desk survives restarts and is queryable alongside the
rest of the XAR platform. Injected via `fcn.api.main.blotter_factory` by the mount shim.

Serialization reuses `BlotterEntry.to_dict()` / `.from_dict()` — the strategy + valuation
snapshot are stored as JSONB exactly as the file store wrote them, so a future migration
of an existing `blotter.json` is a straight row insert.
"""
from __future__ import annotations

import json

from fcn.options.blotter import BlotterEntry, BlotterStore

from ..storage import db


class PgBlotterStore(BlotterStore):
    """`BlotterStore` whose persistence is the `fenny_blotter` Postgres table.

    Overrides only the CRUD methods; `.aggregate()` is inherited (it calls `self.all()`).
    No file path / lock — Postgres owns concurrency.
    """

    def __init__(self) -> None:  # noqa: D107 - deliberately skips the file-store __init__
        pass

    @staticmethod
    def _row_to_entry(r: dict) -> BlotterEntry:
        ts = r["ts"]
        return BlotterEntry.from_dict({
            "id": r["id"],
            "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "strategy": r["strategy"],
            "valuation_snapshot": r["valuation"],
            "notes": r.get("notes", ""),
            "status": r.get("status", "open"),
        })

    def all(self) -> list[BlotterEntry]:
        rows = db.query("SELECT id, ts, strategy, valuation, notes, status "
                        "FROM fenny_blotter ORDER BY ts")
        out: list[BlotterEntry] = []
        for r in rows:
            try:                       # skip a corrupt row, keep the book usable
                out.append(self._row_to_entry(r))
            except Exception:  # noqa: BLE001
                continue
        return out

    def add(self, entry: BlotterEntry) -> BlotterEntry:
        d = entry.to_dict()
        db.execute(
            "INSERT INTO fenny_blotter(id, ts, strategy, valuation, notes, status) "
            "VALUES(%s, %s, %s::jsonb, %s::jsonb, %s, %s) "
            "ON CONFLICT(id) DO UPDATE SET strategy=EXCLUDED.strategy, "
            "valuation=EXCLUDED.valuation, notes=EXCLUDED.notes, status=EXCLUDED.status",
            (d["id"], d["ts"], json.dumps(d["strategy"]), json.dumps(d["valuation_snapshot"]),
             d.get("notes", ""), d.get("status", "open")),
        )
        return entry

    def remove(self, entry_id: str) -> bool:
        rows = db.query("DELETE FROM fenny_blotter WHERE id=%s RETURNING id", (entry_id,))
        return bool(rows)

    def update(self, entry_id: str, **fields) -> BlotterEntry | None:
        sets, params = [], []
        if "notes" in fields:
            sets.append("notes=%s")
            params.append(fields["notes"])
        if "status" in fields:
            sets.append("status=%s")
            params.append(fields["status"])
        if not sets:
            rows = db.query("SELECT id, ts, strategy, valuation, notes, status "
                            "FROM fenny_blotter WHERE id=%s", (entry_id,))
            return self._row_to_entry(rows[0]) if rows else None
        params.append(entry_id)
        rows = db.query(
            f"UPDATE fenny_blotter SET {', '.join(sets)} WHERE id=%s "
            "RETURNING id, ts, strategy, valuation, notes, status", tuple(params))
        return self._row_to_entry(rows[0]) if rows else None
