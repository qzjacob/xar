"""Shared HTTP plumbing for market-data providers: a polite, retrying JSON GET
and small helpers. Every provider gates on its key via `available()` and returns
empty results (never raises) when unconfigured — so the turnkey path runs with
zero provider keys and simply skips what it can't reach."""
from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import get_settings
from ..ingestion.base import polite
from ..ingestion.registry import company_by_id
from ..logging import get_logger

log = get_logger("xar.providers")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=8), reraise=True)
def _get(url: str, *, params=None, headers=None, host: str | None = None, timeout=30):
    if host:
        polite(host)
    s = get_settings()
    h = {"User-Agent": s.http_user_agent}
    if headers:
        h.update(headers)
    r = httpx.get(url, params=params, headers=h, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return r


def get_json(url: str, *, params=None, headers=None, host: str | None = None, timeout=30):
    """GET returning parsed JSON, or None on any failure (logged, never raised)."""
    try:
        return _get(url, params=params, headers=headers, host=host, timeout=timeout).json()
    except Exception as e:  # noqa: BLE001
        log.warning("GET %s failed: %s", url, e)
        return None


def us_ticker(company_id: str) -> str | None:
    """The US-listed ticker (no exchange suffix) for a watched company, if any."""
    c = company_by_id(company_id)
    if not c:
        return None
    return next((t for t in c.get("tickers", []) if "." not in t), None)
