"""Offline tests for the PyPI/npm package-downloads tracker (no network, no DB).

Fixtures are captured shapes of the two public endpoints:
  pypistats.org /api/packages/{pkg}/recent  -> {"data": {"last_week": N, ...}, ...}
  api.npmjs.org /downloads/point/last-week/{pkg} -> {"downloads": N, "package": ...}
"""
from __future__ import annotations

from datetime import date

from xar.providers.alt import pkg_downloads as pkg

# --- captured API payloads ---------------------------------------------------
PYPI_OK = {
    "data": {"last_day": 3595542, "last_month": 110665655, "last_week": 25736980},
    "package": "pymongo",
    "type": "recent_downloads",
}
NPM_OK = {
    "downloads": 13460955,
    "start": "2026-06-26",
    "end": "2026-07-02",
    "package": "mongodb",
}
NPM_NOT_FOUND = {"error": "package thispackagedoesnotexist-zzz not found"}


# --- pure parsers ------------------------------------------------------------
def test_parse_pypi_recent_last_week():
    assert pkg.parse_pypi_recent(PYPI_OK) == 25736980


def test_parse_npm_point_downloads():
    assert pkg.parse_npm_point(NPM_OK) == 13460955


def test_parsers_reject_malformed():
    # npm not-found body has no "downloads"
    assert pkg.parse_npm_point(NPM_NOT_FOUND) is None
    # None / wrong-type / missing keys / non-numeric all -> None (never raise)
    for bad in (None, "nope", [], {}, {"data": None}, {"data": {}},
                {"data": {"last_week": "x"}}, {"data": {"last_week": True}}):
        assert pkg.parse_pypi_recent(bad) is None
    for bad in (None, "nope", [], {}, {"downloads": None}, {"downloads": "x"},
                {"downloads": False}):
        assert pkg.parse_npm_point(bad) is None


# --- aggregation over injected fetchers --------------------------------------
def test_company_totals_sums_and_breaks_down():
    total, per, n_ok = pkg.company_totals(
        ("pymongo",), ("mongodb",),
        pypi_fetch=lambda p: 25736980, npm_fetch=lambda p: 13460955)
    assert total == 25736980 + 13460955
    assert per == {"pypi:pymongo": 25736980, "npm:mongodb": 13460955}
    assert n_ok == 2


def test_company_totals_skips_dead_packages():
    # one good pypi pkg, one dead pypi pkg (None), one npm pkg that raises
    def fp(p):
        return 100 if p == "good" else None

    def fn(p):
        raise RuntimeError("boom")

    total, per, n_ok = pkg.company_totals(
        ("good", "dead"), ("explodes",), pypi_fetch=fp, npm_fetch=fn)
    assert total == 100 and per == {"pypi:good": 100} and n_ok == 1


def test_company_totals_all_dead_is_zero():
    total, per, n_ok = pkg.company_totals(
        ("x",), ("y",), pypi_fetch=lambda p: None, npm_fetch=lambda p: None)
    assert total == 0 and per == {} and n_ok == 0


# --- _ingest end-to-end (fetch + upsert stubbed) -----------------------------
def test_ingest_upserts_one_summed_row_per_company(monkeypatch):
    rows = []
    monkeypatch.setattr(pkg, "fetch_pypi", lambda p: 25736980)
    monkeypatch.setattr(pkg, "fetch_npm", lambda p: 13460955)
    monkeypatch.setattr(pkg.altstore, "upsert_signal",
                        lambda *a, **k: rows.append((a, k)))

    stats = pkg._ingest([("mdb", ("pymongo",), ("mongodb",))],
                        period_end=date(2026, 7, 2))
    assert stats == {"companies": 1, "rows": 1, "packages": 2,
                     "downloads": 25736980 + 13460955, "skipped": 0}
    (args, kw) = rows[0]
    assert args[0] == "alt.pkg_downloads"
    assert kw["company_id"] == "mdb"
    assert kw["period_end"] == date(2026, 7, 2)
    assert kw["value"] == float(25736980 + 13460955)
    assert kw["unit"] == "count" and kw["source"] == "pkg_downloads"
    assert kw["meta"]["per_package"] == {"pypi:pymongo": 25736980,
                                         "npm:mongodb": 13460955}
    assert kw["meta"]["n_packages"] == 2
    assert kw["meta"]["pypi"] == ["pymongo"] and kw["meta"]["npm"] == ["mongodb"]


def test_ingest_skips_company_with_no_resolvable_packages(monkeypatch):
    calls = []
    monkeypatch.setattr(pkg, "fetch_pypi", lambda p: None)
    monkeypatch.setattr(pkg, "fetch_npm", lambda p: None)
    monkeypatch.setattr(pkg.altstore, "upsert_signal",
                        lambda *a, **k: calls.append(k))

    stats = pkg._ingest([("mdb", ("dead",), ())])
    assert stats["rows"] == 0 and stats["skipped"] == 1 and calls == []


def test_pull_builds_items_from_bindings_and_limits(monkeypatch):
    captured = {}
    fake = {
        "co_a": type("B", (), {"pypi_packages": ("a",), "npm_packages": ()})(),
        "co_b": type("B", (), {"pypi_packages": (), "npm_packages": ("b",)})(),
        "co_none": type("B", (), {"pypi_packages": (), "npm_packages": ()})(),
    }
    monkeypatch.setattr(pkg, "bindings", lambda: fake)
    monkeypatch.setattr(pkg, "_ingest",
                        lambda items, **k: captured.update(items=items) or {"ok": 1})

    pkg.pull(limit=1)
    # co_none (no packages) filtered out; limit=1 keeps the first bound company
    assert captured["items"] == [("co_a", ("a",), ())]


def test_available_is_true():
    assert pkg.available() is True
