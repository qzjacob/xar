"""Run log for the daily ingest system (table: ingest_runs).

`start()` opens a 'running' row; `finish()` stamps the outcome + stats; and
`last_success_ts()` is the per-source incremental cursor — the last time a given
source completed successfully, so the next run only pulls newer content. The log
is the observability surface the daily orchestrator and the ops dashboard read.
"""
from __future__ import annotations

import json
from datetime import datetime

from ..logging import get_logger
from . import db

log = get_logger("xar.runlog")


def start(kind: str, since_ts: datetime | None = None) -> int:
    """Open a 'running' run row and return its id."""
    rows = db.query(
        "INSERT INTO ingest_runs(kind, since_ts) VALUES(%s, %s) RETURNING id",
        (kind, since_ts),
    )
    return rows[0]["id"]


def finish(run_id: int, status: str, stats: dict | None = None, error: str | None = None) -> None:
    """Close a run row with its outcome (ok | failed | skipped) and stats."""
    db.execute(
        "UPDATE ingest_runs SET finished_at=now(), status=%s, stats=%s, error=%s WHERE id=%s",
        (status, json.dumps(stats or {}, ensure_ascii=False, default=str), error, run_id),
    )


def last_success_ts(kind: str) -> datetime | None:
    """The most recent successful finish for a source — the incremental pull cursor.
    None when the source has never completed (first run pulls its default window)."""
    rows = db.query(
        "SELECT max(finished_at) AS m FROM ingest_runs WHERE kind=%s AND status='ok'",
        (kind,),
    )
    return rows[0]["m"] if rows else None
