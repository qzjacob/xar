"""Offline tests for the GitHub 开源动能 provider (no network, no DB).

Exercises the pure aggregation/parse layer with Python-dict fixtures and the
pull()/collect orchestration with the HTTP + DB seams monkeypatched.
"""
from __future__ import annotations

from datetime import datetime, timezone

from xar.ontology.altdata import SIGNALS_BY_KEY
from xar.providers.alt import github_metrics as gm

NOW = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)

# Two orgs' worth of repos. Stars deliberately not monotonic with pushed order
# (the API sorts by pushed, not stars) so top_repo != first item.
REPOS_A = [
    {"full_name": "acme/edge", "stargazers_count": 120, "open_issues_count": 8,
     "pushed_at": "2026-06-30T09:00:00Z"},          # recent push
    {"full_name": "acme/core", "stargazers_count": 900, "open_issues_count": 40,
     "pushed_at": "2026-06-20T09:00:00Z"},          # recent push, top star
    {"full_name": "acme/legacy", "stargazers_count": 30, "open_issues_count": 2,
     "pushed_at": "2025-01-01T09:00:00Z"},          # stale (>30d)
]
REPOS_B = [
    {"full_name": "acme-io/driver", "stargazers_count": 250, "open_issues_count": 5,
     "pushed_at": "2026-07-01T09:00:00Z"},          # recent push
]

RELEASES_CORE = [
    {"published_at": "2026-06-25T00:00:00Z", "draft": False},   # within 30d
    {"published_at": "2026-06-10T00:00:00Z", "draft": False},   # within 30d
    {"published_at": "2026-01-01T00:00:00Z", "draft": False},   # old
    {"published_at": "2026-06-28T00:00:00Z", "draft": True},    # draft -> skip
]


# --- signal spec sanity ------------------------------------------------------
def test_signal_spec_is_company_weekly_stars():
    spec = SIGNALS_BY_KEY[gm._KEY]
    assert spec.scope == "company"
    assert spec.cadence == "weekly"
    assert spec.unit == "stars"
    assert spec.source == "github_metrics" == gm.pull.__module__.split(".")[-1]


def test_available_is_keyless():
    assert gm.available() is True


# --- pure parse/aggregate ----------------------------------------------------
def test_parse_dt_zulu_and_naive():
    assert gm._parse_dt("2026-06-30T09:00:00Z") == datetime(2026, 6, 30, 9, 0, tzinfo=timezone.utc)
    assert gm._parse_dt(None) is None
    assert gm._parse_dt("not-a-date") is None
    # naive iso is coerced to UTC
    assert gm._parse_dt("2026-06-30T09:00:00").tzinfo == timezone.utc


def test_top_repos_orders_by_stars():
    top = gm._top_repos(REPOS_A, 2)
    assert [r["full_name"] for r in top] == ["acme/core", "acme/edge"]


def test_aggregate_sums_and_flags_recent():
    agg = gm._aggregate(REPOS_A, now=NOW)
    assert agg["stars"] == 120 + 900 + 30
    assert agg["open_issues"] == 8 + 40 + 2
    assert agg["repos"] == 3
    assert agg["top_repo"] == "acme/core"      # max stars, not first-by-pushed
    assert agg["pushed_30d"] == 2              # legacy is stale


def test_aggregate_empty_is_zeroed():
    agg = gm._aggregate([], now=NOW)
    assert agg == {"stars": 0, "open_issues": 0, "repos": 0, "top_repo": None, "pushed_30d": 0}


def test_count_releases_30d_skips_old_and_drafts():
    assert gm._count_releases_30d(RELEASES_CORE, now=NOW) == 2
    assert gm._count_releases_30d([], now=NOW) == 0


def test_budget_caps_requests():
    b = gm._Budget(cap=2)
    assert b.spend() and b.spend()
    assert b.spend() is False and b.count == 2


# --- collect (multi-org merge + real releases on top-3) ----------------------
def test_collect_merges_orgs_and_uses_real_releases(monkeypatch):
    repos_by_org = {"acme": REPOS_A, "acme-io": REPOS_B}
    monkeypatch.setattr(gm, "_fetch_repos", lambda org, budget: repos_by_org.get(org))
    # only acme/core has releases; others -> empty list (still counts as "got")
    monkeypatch.setattr(gm, "_fetch_releases",
                        lambda full, budget: RELEASES_CORE if full == "acme/core" else [])

    agg = gm._collect(("acme", "acme-io"), gm._Budget(), now=NOW)
    assert agg["stars"] == 120 + 900 + 30 + 250
    assert agg["repos"] == 4
    assert agg["top_repo"] == "acme/core"
    assert agg["releases_30d"] == 2            # real /releases, not the proxy


def test_collect_falls_back_to_pushed_proxy_when_releases_unreachable(monkeypatch):
    monkeypatch.setattr(gm, "_fetch_repos", lambda org, budget: REPOS_A)
    monkeypatch.setattr(gm, "_fetch_releases", lambda full, budget: None)  # all fail
    agg = gm._collect(("acme",), gm._Budget(), now=NOW)
    assert agg["releases_30d"] == agg["pushed_30d"] == 2


def test_collect_none_when_no_repos(monkeypatch):
    monkeypatch.setattr(gm, "_fetch_repos", lambda org, budget: None)
    assert gm._collect(("dead",), gm._Budget(), now=NOW) is None


# --- pull end-to-end (bindings + upsert seams stubbed) -----------------------
class _Binding:
    def __init__(self, orgs):
        self.github_orgs = tuple(orgs)


def test_pull_writes_one_signal_per_bound_company(monkeypatch):
    monkeypatch.setattr(gm, "bindings", lambda: {
        "acme": _Binding(["acme", "acme-io"]),
        "nogh": _Binding([]),          # no github_orgs -> not a target
    })
    monkeypatch.setattr(gm, "_collect",
                        lambda orgs, budget, now=None: {
                            "stars": 1300, "open_issues": 55, "repos": 4,
                            "top_repo": "acme/core", "pushed_30d": 3, "releases_30d": 2})
    writes = []
    monkeypatch.setattr(gm, "upsert_signal",
                        lambda key, **kw: writes.append((key, kw)))

    stats = gm.pull()
    assert stats["companies"] == 1 and stats["written"] == 1 and stats["orgs"] == 2
    key, kw = writes[0]
    assert key == "alt.github_momentum"
    assert kw["company_id"] == "acme"
    assert kw["value"] == 1300.0
    assert kw["unit"] == "stars" and kw["source"] == "github_metrics"
    assert kw["meta"] == {"repos": 4, "top_repo": "acme/core", "releases_30d": 2,
                          "orgs": ["acme", "acme-io"], "open_issues": 55}


def test_pull_skips_company_with_no_repos(monkeypatch):
    monkeypatch.setattr(gm, "bindings", lambda: {"acme": _Binding(["acme"])})
    monkeypatch.setattr(gm, "_collect", lambda orgs, budget, now=None: None)
    writes = []
    monkeypatch.setattr(gm, "upsert_signal", lambda key, **kw: writes.append(kw))
    stats = gm.pull()
    assert stats["skipped"] == 1 and stats["written"] == 0 and writes == []


def test_pull_limit_slices_targets(monkeypatch):
    monkeypatch.setattr(gm, "bindings", lambda: {
        "a": _Binding(["a"]), "b": _Binding(["b"]), "c": _Binding(["c"])})
    seen = []
    monkeypatch.setattr(gm, "_collect",
                        lambda orgs, budget, now=None: seen.append(orgs) or {
                            "stars": 1, "open_issues": 0, "repos": 1,
                            "top_repo": "x", "pushed_30d": 0, "releases_30d": 0})
    monkeypatch.setattr(gm, "upsert_signal", lambda key, **kw: None)
    stats = gm.pull(limit=2)
    assert stats["companies"] == 2 and len(seen) == 2


def test_pull_swallows_collect_errors(monkeypatch):
    monkeypatch.setattr(gm, "bindings", lambda: {"boom": _Binding(["boom"])})

    def _raise(orgs, budget, now=None):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(gm, "_collect", _raise)
    monkeypatch.setattr(gm, "upsert_signal", lambda key, **kw: None)
    stats = gm.pull()   # must not raise
    assert stats["errors"] == 1 and stats["written"] == 0
