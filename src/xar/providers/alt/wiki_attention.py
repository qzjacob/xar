"""维基注意力 —— Wikipedia pageviews as a company-attention proxy (alt.wiki_attention).

Keyless, public Wikimedia REST APIs. Per company (bindings with a ``wiki_title``):

  1. **Resolve** the article via ``…/page/summary/{title}`` (follows redirects, so
     "NVIDIA" → "Nvidia", "AMD" → "Advanced Micro Devices"). A 404 means the
     heuristic title has no article — skipped silently, counted in stats (titles
     are heuristic, so misses are normal).
  2. **Pageviews**: ``…/metrics/pageviews/per-article/en.wikipedia/all-access/user/
     {title}/daily/{start}/{end}`` over the last 28 days.

We write ONE weekly point per company via ``altstore.upsert_signal``:
``value = sum(last 7 available days)``, ``period_end = today``, and
``meta = {prev_7d, resolved_title, avg_28d, days}``. Direction is undefined
(good_when=None) — a spike may be a launch OR a scandal.

Politeness: a descriptive ``settings.http_user_agent`` (Wikimedia requires it) and
0.5s pacing between requests. An optional ``WIKIMEDIA_ACCESS_TOKEN`` (never printed)
is sent as a Bearer header for higher limits. Per-item failures are logged and
skipped, never raised; parsing is pure (offline-testable).
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from threading import Lock
from urllib.parse import quote

import httpx

from ...config import get_settings
from ...ontology.altdata import bindings
from ...storage.altstore import upsert_signal
from ..base import log

SIGNAL_KEY = "alt.wiki_attention"
SOURCE = "wiki_attention"
UNIT = "views"
WINDOW_DAYS = 28
PACE_SECONDS = 0.5

_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
_PAGEVIEWS_URL = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia/all-access/user/{article}/daily/{start}/{end}"
)


def available() -> bool:
    return True  # public Wikimedia REST, no key required


# --- politeness (0.5s pacing) ------------------------------------------------
_LOCK = Lock()
_LAST = [0.0]


def _pace() -> None:
    with _LOCK:
        wait = PACE_SECONDS - (time.time() - _LAST[0])
        if wait > 0:
            time.sleep(wait)
        _LAST[0] = time.time()


def _enc(title: str) -> str:
    """Article path segment: spaces→underscores, then percent-encode."""
    return quote(title.replace(" ", "_"), safe="")


def _get(url: str) -> httpx.Response | None:
    """Paced GET with the required descriptive UA. Returns the response or None
    on a transport error (logged, never raised). HTTP status is left to callers."""
    _pace()
    s = get_settings()
    headers = {"User-Agent": s.http_user_agent}
    token = os.environ.get("WIKIMEDIA_ACCESS_TOKEN")  # optional; value never logged
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        return httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
    except Exception as e:  # noqa: BLE001
        log.warning("wiki_attention fetch %s failed: %s", url, type(e).__name__)
        return None


# --- pure parsing (offline-testable) -----------------------------------------
def parse_summary(payload: dict) -> str | None:
    """Canonical article title from a page-summary JSON (underscore form), or None."""
    titles = payload.get("titles") or {}
    canonical = titles.get("canonical") or payload.get("title")
    if not canonical:
        return None
    return str(canonical).replace(" ", "_")


def parse_pageviews(payload: dict) -> list[tuple[date, int]]:
    """[(day, views)] ascending from a pageviews-per-article JSON (stdlib only)."""
    out: list[tuple[date, int]] = []
    for it in (payload or {}).get("items") or []:
        ts = str(it.get("timestamp") or "")
        views = it.get("views")
        if len(ts) < 8 or views is None:
            continue
        try:
            day = date(int(ts[0:4]), int(ts[4:6]), int(ts[6:8]))
            out.append((day, int(views)))
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda t: t[0])
    return out


def summarize(series: list[tuple[date, int]]) -> dict:
    """last_7d / prev_7d / avg_28d over the available daily points (lag-robust:
    'last 7 days' = the 7 most recent points the API actually returned)."""
    views = [v for _, v in series]
    n = len(views)
    return {
        "last_7d": sum(views[-7:]),
        "prev_7d": sum(views[-14:-7]),
        "avg_28d": round(sum(views) / n, 1) if n else 0.0,
        "days": n,
    }


# --- per-company flow --------------------------------------------------------
def pull_company(company_id: str, title: str, *, today: date | None = None) -> str:
    """Resolve → pageviews → upsert for one company. Returns a status string:
    landed | no_views | unresolved | error. Never raises."""
    today = today or date.today()

    # 1. resolve the (heuristic) title to a canonical article, following redirects
    r = _get(_SUMMARY_URL.format(title=_enc(title)))
    if r is None:
        return "error"
    if r.status_code == 404:
        return "unresolved"  # heuristic title has no article — normal, skip silently
    if r.status_code != 200:
        log.warning("wiki_attention summary %r: HTTP %s", title, r.status_code)
        return "error"
    try:
        canonical = parse_summary(r.json())
    except Exception as e:  # noqa: BLE001
        log.warning("wiki_attention summary parse %r: %s", title, type(e).__name__)
        return "error"
    if not canonical:
        return "unresolved"

    # 2. daily pageviews over the trailing window
    start = (today - timedelta(days=WINDOW_DAYS)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    r2 = _get(_PAGEVIEWS_URL.format(article=_enc(canonical), start=start, end=end))
    if r2 is None:
        return "error"
    if r2.status_code == 404:
        return "no_views"  # resolved page but no pageview record in the window
    if r2.status_code != 200:
        log.warning("wiki_attention pageviews %r: HTTP %s", canonical, r2.status_code)
        return "error"
    try:
        series = parse_pageviews(r2.json())
    except Exception as e:  # noqa: BLE001
        log.warning("wiki_attention pageviews parse %r: %s", canonical, type(e).__name__)
        return "error"
    if not series:
        return "no_views"

    agg = summarize(series)
    upsert_signal(
        SIGNAL_KEY, company_id=company_id, period_end=today,
        value=float(agg["last_7d"]), unit=UNIT, source=SOURCE,
        meta={"prev_7d": agg["prev_7d"], "resolved_title": canonical,
              "avg_28d": agg["avg_28d"], "days": agg["days"]},
    )
    return "landed"


def pull(limit: int | None = None) -> dict:
    """Sweep company bindings with a ``wiki_title``; write one weekly point each.

    ``limit`` caps companies per run (the worker calls with a slice). Returns
    stats: {companies, resolved, unresolved, no_views, errors, landed}. One dead
    title never sinks the sweep.
    """
    items = [(cid, b.wiki_title) for cid, b in bindings().items() if b.wiki_title]
    if limit is not None:
        items = items[:limit]

    today = date.today()
    stats = {"signal": SIGNAL_KEY, "companies": 0, "resolved": 0,
             "unresolved": 0, "no_views": 0, "errors": 0, "landed": 0}
    for cid, title in items:
        stats["companies"] += 1
        try:
            status = pull_company(cid, title, today=today)
        except Exception as e:  # noqa: BLE001  # per-item failure never sinks the run
            log.warning("wiki_attention %s failed: %s", cid, type(e).__name__)
            status = "error"
        if status == "landed":
            stats["resolved"] += 1
            stats["landed"] += 1
        elif status == "no_views":
            stats["resolved"] += 1
            stats["no_views"] += 1
        elif status == "unresolved":
            stats["unresolved"] += 1
        else:
            stats["errors"] += 1
    log.info("wiki_attention: %s", stats)
    return stats
