"""Shared market-data caching + concurrent fetch for the screener/ranking paths.

Ranking a few hundred names is bottlenecked by **network round-trips** (one option
chain per name), not by the closed-form math. So two small primitives:

  * :class:`TTLCache` — thread-safe, time-bounded, size-bounded. Lets the ranking
    and market-read services reuse a name's live spot / vol across requests within a
    short window without re-hitting the data vendor.
  * :func:`fetch_concurrent` — a bounded thread pool that maps a fetch over many
    tickers and returns ``(item, result, error)`` triples (never raises), so one bad
    name can't sink the batch.

Deliberately dependency-free (stdlib only) and single-node, mirroring jobs.py — the
seam to swap in Redis/a shared cache later.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any


class TTLCache:
    """Thread-safe cache with per-entry TTL and a hard size cap (LRU-ish eviction)."""

    def __init__(self, ttl_seconds: float = 300.0, max_entries: int = 4096) -> None:
        self.ttl = ttl_seconds
        self.max = max_entries
        self._lock = threading.Lock()
        self._d: dict[Any, tuple[float, Any]] = {}  # key -> (expiry_monotonic, value)

    def get(self, key: Any) -> Any | None:
        with self._lock:
            item = self._d.get(key)
            if item is None:
                return None
            expiry, value = item
            if time.monotonic() > expiry:
                self._d.pop(key, None)
                return None
            return value

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            if len(self._d) >= self.max and key not in self._d:
                # Evict the soonest-expiring quarter to make room.
                doomed = sorted(self._d, key=lambda k: self._d[k][0])[: self.max // 4 + 1]
                for k in doomed:
                    self._d.pop(k, None)
            self._d[key] = (time.monotonic() + self.ttl, value)

    def get_or_compute(self, key: Any, compute: Callable[[], Any]) -> Any:
        """Return the cached value or compute+store it. ``None`` results are not
        cached (so transient misses retry next time)."""
        value = self.get(key)
        if value is not None:
            return value
        value = compute()
        if value is not None:
            self.set(key, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


def fetch_concurrent(
    items: Iterable[Any],
    fn: Callable[[Any], Any],
    max_workers: int = 8,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[tuple[Any, Any, Exception | None]]:
    """Map ``fn`` over ``items`` on a bounded thread pool.

    Returns ``[(item, result, error)]`` preserving no particular order; ``error`` is
    the caught exception (and ``result`` is ``None``) when ``fn`` raised, so a single
    failing name never aborts the batch. ``on_progress(done, total)`` (if given) is
    called as each item completes, letting a job stream its progress.
    """
    items = list(items)
    out: list[tuple[Any, Any, Exception | None]] = []
    if not items:
        return out
    total = len(items)
    workers = max(1, min(max_workers, total))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fn, it): it for it in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                out.append((item, future.result(), None))
            except Exception as exc:  # noqa: BLE001 - intentionally collected per-item
                out.append((item, None, exc))
            if on_progress is not None:
                on_progress(len(out), total)
    return out


# Process-wide shared cache for live spot / vol lookups (5-minute freshness).
MARKET_CACHE = TTLCache(ttl_seconds=300.0)
