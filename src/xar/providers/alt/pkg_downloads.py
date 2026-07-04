"""包下载量 (PyPI + npm) —— company-scope `alt.pkg_downloads` tracker.

Weekly package downloads are the top of the adoption funnel for devtools /
infrastructure software. For every company that binds one or more PyPI and/or
npm packages we SUM their last-week download counts into ONE `alt_signals`
row:  value = total weekly downloads, period_end = today (the weekly period we
observed), meta = the per-package breakdown + which registries were queried.

Keyless — both endpoints are public JSON, no token. Per-package failures are
logged and skipped, never raised: a dead package never sinks a company, and a
dead company never sinks the sweep. Fetches are paced (>= 1s between HTTP GETs)
to stay courteous to the public APIs.

Sources (one GET per package):
  PyPI:  https://pypistats.org/api/packages/{pkg}/recent      -> data.last_week
  npm:   https://api.npmjs.org/downloads/point/last-week/{pkg} -> downloads
"""
from __future__ import annotations

import time
from datetime import date

from ...ontology.altdata import SIGNALS_BY_KEY, bindings
from ...storage import altstore
from ..base import get_json, log

SIGNAL_KEY = "alt.pkg_downloads"
SOURCE = "pkg_downloads"

_PYPI_URL = "https://pypistats.org/api/packages/{pkg}/recent"
_NPM_URL = "https://api.npmjs.org/downloads/point/last-week/{pkg}"
_PACE = 1.0  # seconds between successive HTTP GETs (rate-limit courtesy)

_last_fetch = 0.0  # monotonic timestamp of the previous GET (module-global pacer)


def available() -> bool:
    return True  # public JSON APIs, no key


# --- pure parsers (offline-testable, no I/O) --------------------------------
def parse_pypi_recent(payload: dict | None) -> int | None:
    """pypistats `/recent` JSON -> last_week downloads, or None if malformed."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    v = data.get("last_week")
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_npm_point(payload: dict | None) -> int | None:
    """npm `downloads/point` JSON -> downloads, or None if malformed/not-found.

    The not-found body is `{"error": "package X not found"}` (no `downloads`),
    which naturally yields None.
    """
    if not isinstance(payload, dict):
        return None
    v = payload.get("downloads")
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# --- paced fetch (thin I/O wrappers over the parsers) -----------------------
def _paced_get(url: str) -> dict | None:
    """Polite, paced JSON GET. get_json logs+swallows failures (returns None)."""
    global _last_fetch
    wait = _PACE - (time.monotonic() - _last_fetch)
    if wait > 0:
        time.sleep(wait)
    payload = get_json(url)
    _last_fetch = time.monotonic()
    return payload


def fetch_pypi(pkg: str) -> int | None:
    return parse_pypi_recent(_paced_get(_PYPI_URL.format(pkg=pkg)))


def fetch_npm(pkg: str) -> int | None:
    return parse_npm_point(_paced_get(_NPM_URL.format(pkg=pkg)))


# --- aggregation (pure over injectable fetchers -> offline-testable) --------
def company_totals(pypi_pkgs, npm_pkgs, *, pypi_fetch=None, npm_fetch=None):
    """Sum weekly downloads across a company's packages.

    Returns (total, per_package_breakdown, n_ok). Unreachable/malformed packages
    are logged and skipped; they contribute nothing and are absent from the
    breakdown, so `n_ok == 0` means "nothing landed for this company".

    `pypi_fetch`/`npm_fetch` override the fetchers (tests inject stubs); left
    None they resolve the module-level fetchers at call time (monkeypatchable).
    """
    pypi_fetch = pypi_fetch or fetch_pypi
    npm_fetch = npm_fetch or fetch_npm
    per: dict[str, int] = {}
    total = 0
    for registry, pkgs, fetch in (("pypi", pypi_pkgs, pypi_fetch),
                                  ("npm", npm_pkgs, npm_fetch)):
        for pkg in pkgs:
            try:
                dl = fetch(pkg)
            except Exception as e:  # noqa: BLE001  (never let one package raise)
                log.warning("%s: %s %s fetch errored: %s", SOURCE, registry, pkg,
                            type(e).__name__)
                dl = None
            if dl is None:
                log.warning("%s: %s %s unavailable, skipped", SOURCE, registry, pkg)
                continue
            per[f"{registry}:{pkg}"] = dl
            total += dl
    return total, per, len(per)


# --- run --------------------------------------------------------------------
def _ingest(items, *, period_end: date | None = None) -> dict:
    """Core sweep over `(company_id, pypi_pkgs, npm_pkgs)` items.

    One summed row per company. `pull` builds `items` from the ontology
    bindings; the live smoke injects hardcoded packages here without touching
    bindings. Never raises — every per-company failure is logged and skipped.
    """
    spec = SIGNALS_BY_KEY.get(SIGNAL_KEY)
    unit = spec.unit if spec else "count"
    pe = period_end or date.today()
    stats = {"companies": 0, "rows": 0, "packages": 0, "downloads": 0, "skipped": 0}
    for company_id, pypi_pkgs, npm_pkgs in items:
        stats["companies"] += 1
        total, per, n_ok = company_totals(tuple(pypi_pkgs or ()), tuple(npm_pkgs or ()))
        if n_ok == 0:
            log.warning("%s: %s no packages resolved, skipped", SOURCE, company_id)
            stats["skipped"] += 1
            continue
        try:
            altstore.upsert_signal(
                SIGNAL_KEY, period_end=pe, value=float(total),
                company_id=company_id, unit=unit, source=SOURCE,
                meta={"per_package": per, "n_packages": n_ok,
                      "pypi": list(pypi_pkgs or ()), "npm": list(npm_pkgs or ())})
        except Exception as e:  # noqa: BLE001
            log.warning("%s: upsert %s failed: %s", SOURCE, company_id, type(e).__name__)
            continue
        stats["rows"] += 1
        stats["packages"] += n_ok
        stats["downloads"] += total
    log.info("%s: %d rows, %d packages, %d weekly downloads (%d companies, %d skipped)",
             SOURCE, stats["rows"], stats["packages"], stats["downloads"],
             stats["companies"], stats["skipped"])
    return stats


def pull(limit: int | None = None) -> dict:
    """Fetch weekly PyPI+npm downloads for every pkg-bound company; upsert one
    summed row each. `limit` caps the number of companies processed. Returns run
    stats: {companies, rows, packages, downloads, skipped}."""
    items = [(cid, b.pypi_packages, b.npm_packages)
             for cid, b in bindings().items()
             if b.pypi_packages or b.npm_packages]
    if limit is not None:
        items = items[:limit]
    return _ingest(items)
