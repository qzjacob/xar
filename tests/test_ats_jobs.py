"""Offline tests for the ATS 在招职位 provider (no network, no DB).

Fixtures mirror the real Greenhouse (`{"jobs":[{title, location:{name}}]}`) and
Lever (`[{text, categories:{location}}]`) shapes captured live from datadog /
palantir boards. All parsing is pure; DB write is monkeypatched.
"""
from __future__ import annotations

from datetime import date

from xar.providers.alt import ats_jobs

GH_FIXTURE = {
    "meta": {"total": 4},
    "jobs": [
        {"title": "Senior Machine Learning Engineer", "location": {"name": "New York, USA"}},
        {"title": "Backend Software Engineer", "location": {"name": "New York, USA"}},
        {"title": "AI Research Scientist", "location": {"name": "Paris, France"}},
        {"title": "Account Executive", "location": {"name": "Remote"}},
    ],
}

LEVER_FIXTURE = [
    {"text": "Staff Data Scientist", "categories": {"location": "Palo Alto, CA"}},
    {"text": "Full-Stack Developer", "categories": {"location": "Palo Alto, CA"}},
    {"text": "Recruiting Coordinator", "categories": {"location": "London, UK"}},
    {"text": "LLM Infrastructure Engineer", "categories": {}},  # missing location
]


# --- normalize ---------------------------------------------------------------
def test_normalize_greenhouse():
    jobs = ats_jobs.normalize_jobs("greenhouse", GH_FIXTURE)
    assert len(jobs) == 4
    assert jobs[0] == {"title": "Senior Machine Learning Engineer", "location": "New York, USA"}
    assert jobs[3]["location"] == "Remote"


def test_normalize_lever():
    jobs = ats_jobs.normalize_jobs("lever", LEVER_FIXTURE)
    assert len(jobs) == 4
    assert jobs[0] == {"title": "Staff Data Scientist", "location": "Palo Alto, CA"}
    assert jobs[3]["location"] == ""  # missing categories.location -> empty


def test_normalize_bad_shape_is_empty():
    assert ats_jobs.normalize_jobs("greenhouse", None) == []
    assert ats_jobs.normalize_jobs("greenhouse", []) == []  # list, not dict
    assert ats_jobs.normalize_jobs("lever", None) == []
    assert ats_jobs.normalize_jobs("unknown", GH_FIXTURE) == []


# --- metrics -----------------------------------------------------------------
def test_metrics_greenhouse():
    m = ats_jobs.metrics(ats_jobs.normalize_jobs("greenhouse", GH_FIXTURE))
    assert m["total"] == 4
    # ML engineer + AI research scientist match /(machine learning|AI|data scientist|...)/i
    assert m["ai_roles"] == 2
    # ML engineer + backend software engineer + AI research(no) -> "engineer" x2
    assert m["eng_roles"] == 2
    assert m["locations_top3"][0] == ["New York, USA", 2]  # most common first
    assert ["Remote", 1] in m["locations_top3"]


def test_metrics_lever_and_ai_regex_boundaries():
    m = ats_jobs.metrics(ats_jobs.normalize_jobs("lever", LEVER_FIXTURE))
    assert m["total"] == 4
    # "Data Scientist" + "LLM ..." match AI regex
    assert m["ai_roles"] == 2
    # Full-Stack Developer + LLM Infrastructure Engineer -> eng
    assert m["eng_roles"] == 2
    # empty-location job excluded from top3
    names = [name for name, _ in m["locations_top3"]]
    assert "" not in names
    assert names[0] == "Palo Alto, CA"


def test_ai_regex_word_boundaries():
    # \bAI\b must NOT fire on substrings like "email" / "maintain"
    m = ats_jobs.metrics([{"title": "Email Marketing Maintainer", "location": "X"}])
    assert m["ai_roles"] == 0


def test_metrics_empty():
    m = ats_jobs.metrics([])
    assert m == {"total": 0, "ai_roles": 0, "eng_roles": 0, "locations_top3": []}


# --- ingest_one (fetch + upsert stubbed) -------------------------------------
def test_ingest_one_writes_signal(monkeypatch):
    calls = {}
    monkeypatch.setattr(ats_jobs, "_fetch", lambda kind, slug: GH_FIXTURE)

    def fake_upsert(signal_key, **kw):
        calls["signal_key"] = signal_key
        calls.update(kw)

    monkeypatch.setattr(ats_jobs, "upsert_signal", fake_upsert)
    r = ats_jobs.ingest_one("ddog", "greenhouse", "datadog", period_end=date(2026, 7, 2))

    assert r["total"] == 4 and r["ai_roles"] == 2
    assert calls["signal_key"] == "alt.hiring_velocity"
    assert calls["company_id"] == "ddog"
    assert calls["value"] == 4.0
    assert calls["period_end"] == date(2026, 7, 2)
    assert calls["unit"] == "count" and calls["source"] == "ats_jobs"
    assert calls["meta"]["kind"] == "greenhouse" and calls["meta"]["slug"] == "datadog"
    assert calls["meta"]["locations_top3"][0] == ["New York, USA", 2]


def test_ingest_one_fetch_failure_returns_none(monkeypatch):
    monkeypatch.setattr(ats_jobs, "_fetch", lambda kind, slug: None)
    monkeypatch.setattr(ats_jobs, "upsert_signal",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not write")))
    assert ats_jobs.ingest_one("ddog", "greenhouse", "datadog") is None


def test_ingest_one_never_raises_on_bad_payload(monkeypatch):
    # _fetch returns garbage that would break parsing -> caught, returns None
    monkeypatch.setattr(ats_jobs, "_fetch", lambda kind, slug: 12345)
    written = []
    monkeypatch.setattr(ats_jobs, "upsert_signal", lambda *a, **k: written.append(k))
    # 12345 is not dict/list -> normalize -> [] -> total 0, still writes value 0
    r = ats_jobs.ingest_one("ddog", "greenhouse", "datadog")
    assert r["total"] == 0 and written and written[0]["value"] == 0.0


# --- pull fan-out over bindings (bindings + ingest stubbed) ------------------
def test_pull_iterates_ats_bindings(monkeypatch):
    monkeypatch.setattr(ats_jobs, "_targets",
                        lambda: [("ddog", "greenhouse", "datadog"), ("pltr", "lever", "palantir")])
    monkeypatch.setattr(ats_jobs, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))

    seen = []

    def fake_ingest(cid, kind, slug):
        seen.append((cid, kind, slug))
        return {"total": 10, "ai_roles": 3}

    monkeypatch.setattr(ats_jobs, "ingest_one", fake_ingest)
    stats = ats_jobs.pull()
    assert stats["attempted"] == 2 and stats["ok"] == 2 and stats["skipped"] == 0
    assert stats["postings"] == 20 and stats["ai_roles"] == 6
    assert seen == [("ddog", "greenhouse", "datadog"), ("pltr", "lever", "palantir")]


def test_pull_limit_and_skip(monkeypatch):
    monkeypatch.setattr(ats_jobs, "_targets",
                        lambda: [("a", "greenhouse", "x"), ("b", "lever", "y"), ("c", "greenhouse", "z")])
    monkeypatch.setattr(ats_jobs, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))
    monkeypatch.setattr(ats_jobs, "ingest_one", lambda cid, kind, slug: None)  # all skip
    stats = ats_jobs.pull(limit=2)
    assert stats["companies"] == 2 and stats["attempted"] == 2
    assert stats["ok"] == 0 and stats["skipped"] == 2
