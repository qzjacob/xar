"""Run state + checkpointing. State persists to report_runs.state after every
node so a run is resumable and can pause for human approval (the interrupt)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from ..storage import db


def new_run_id() -> str:
    return "run_" + uuid.uuid4().hex[:16]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunState:
    def __init__(self, run_id: str, kind: str, request: dict, state: dict | None = None):
        self.run_id = run_id
        self.kind = kind
        self.request = request
        self.state: dict = state or {"citations": [], "findings": {}}

    # --- citations: dedup by chunk_id, return stable 1-based marker ---
    def cite(self, citation: dict) -> int:
        cites = self.state.setdefault("citations", [])
        key = citation.get("chunk_id") or citation.get("url") or citation.get("title")
        for i, c in enumerate(cites):
            if (c.get("chunk_id") or c.get("url") or c.get("title")) == key:
                return i + 1
        cites.append(citation)
        return len(cites)

    @property
    def citations(self) -> list[dict]:
        return self.state.get("citations", [])

    def put(self, key: str, value) -> None:
        self.state[key] = value

    def get(self, key: str, default=None):
        return self.state.get(key, default)

    # --- persistence ---
    def create(self) -> None:
        db.execute(
            "INSERT INTO report_runs(id,kind,request,status,state,snapshot_at) "
            "VALUES(%s,%s,%s,'running',%s,%s)",
            (self.run_id, self.kind, json.dumps(self.request, default=str),
             json.dumps(self.state, default=str), now()),
        )

    def checkpoint(self, status: str | None = None) -> None:
        if status:
            db.execute(
                "UPDATE report_runs SET state=%s, status=%s, updated_at=now() WHERE id=%s",
                (json.dumps(self.state, default=str), status, self.run_id),
            )
        else:
            db.execute(
                "UPDATE report_runs SET state=%s, updated_at=now() WHERE id=%s",
                (json.dumps(self.state, default=str), self.run_id),
            )

    @classmethod
    def load(cls, run_id: str) -> "RunState | None":
        rows = db.query("SELECT * FROM report_runs WHERE id=%s", (run_id,))
        if not rows:
            return None
        r = rows[0]
        req = r["request"] if isinstance(r["request"], dict) else json.loads(r["request"])
        st = r["state"] if isinstance(r["state"], dict) else json.loads(r["state"])
        rs = cls(run_id, r["kind"], req, st)
        rs.status = r["status"]
        return rs
