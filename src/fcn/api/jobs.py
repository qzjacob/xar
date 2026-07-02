"""Lightweight in-process async pricing jobs.

Monte Carlo + full Greeks on a daily American grid is seconds-to-a-minute, which
blocks a synchronous request. This is a single-node, dependency-free job runner
(a thread pool + a lock-guarded in-memory store) so the API can return a job id
immediately, stream the fast PV first, and fill Greeks in as they finish. For
multi-node scale this is the seam to swap in Celery/RQ + Redis — deliberately not
pulled in now. The store is mutated by worker threads and read by request
threads, so every access is guarded by ``_LOCK`` and the poller is handed a copy.

Memory & abuse bounds:
  * ``_MAX_JOBS`` is a hard cap on the total store size; incoming work is rejected
    with :class:`JobQueueFull` (-> HTTP 503) rather than letting the dict grow.
  * ``_MAX_RUNNING`` bounds concurrent workers; beyond it, new submissions are
    rejected so callers fail fast instead of queuing silently for minutes.
  * Cleanup still drops oldest finished jobs first so a busy but healthy node does
    not evict live results.
"""

from __future__ import annotations

import copy
import threading
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

_EXEC = ThreadPoolExecutor(max_workers=2)
_LOCK = threading.Lock()
_JOBS: "OrderedDict[str, dict]" = OrderedDict()
_MAX_JOBS = 256
_MAX_RUNNING = 4  # beyond this many concurrent active jobs, reject new work


class JobQueueFull(RuntimeError):
    """Raised when the job store is full or too many jobs are running concurrently."""


def _count_active_locked() -> int:
    return sum(1 for v in _JOBS.values() if v["status"] in ("queued", "running"))


def new_job() -> str:
    """Reserve a slot for a new job.

    Raises :class:`JobQueueFull` when either (a) the store is at capacity with no
    finished jobs to evict, or (b) too many jobs are already queued/running. The
    caller should surface this as HTTP 503, not 500.
    """
    with _LOCK:
        # Evict finished jobs first (oldest first) to make room.
        if len(_JOBS) >= _MAX_JOBS:
            finished = [k for k, v in _JOBS.items() if v["status"] in ("done", "error")]
            for k in finished[: max(64, len(_JOBS) - _MAX_JOBS + 1)]:
                _JOBS.pop(k, None)
        if len(_JOBS) >= _MAX_JOBS:
            raise JobQueueFull("job store full; retry shortly")
        if _count_active_locked() >= _MAX_RUNNING:
            raise JobQueueFull("too many concurrent pricing jobs; retry shortly")
        jid = uuid4().hex[:12]
        _JOBS[jid] = {"job_id": jid, "status": "queued", "stage": "queued",
                      "partial": {}, "error": None}
    return jid


def get_job(jid: str) -> dict | None:
    """Return a snapshot copy so the poller never serialises a dict a worker is
    concurrently mutating."""
    with _LOCK:
        job = _JOBS.get(jid)
        return copy.deepcopy(job) if job is not None else None


def update(jid: str, **fields) -> None:
    """Atomically publish field updates (e.g. status/stage/partial) for a job."""
    with _LOCK:
        job = _JOBS.get(jid)
        if job is not None:
            job.update(fields)
            _JOBS.move_to_end(jid)  # LRU: most-recently-touched at the tail


def submit(jid: str, fn: Callable[[str], None]) -> None:
    def run() -> None:
        update(jid, status="running")
        try:
            fn(jid)
        except Exception as exc:  # surface to the poller rather than crash the worker
            update(jid, status="error", error=str(exc)[:400])

    _EXEC.submit(run)
