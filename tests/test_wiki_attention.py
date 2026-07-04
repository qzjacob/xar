"""Offline tests for the 维基注意力 provider (no network, no DB).

Parsing is pure; the per-company/pull flows are exercised with fixtures by
stubbing the paced HTTP getter and the storage upsert.
"""
from __future__ import annotations

from datetime import date

import pytest

from xar.providers.alt import wiki_attention as wa


# --- fixtures ----------------------------------------------------------------
# A page-summary response for a redirected title (e.g. request "NVIDIA").
SUMMARY_JSON = {
    "type": "standard",
    "title": "Nvidia",
    "titles": {"canonical": "Nvidia", "normalized": "Nvidia", "display": "Nvidia"},
    "extract": "Nvidia Corporation is an American technology company.",
}
# Canonical with a space in the human title but underscores in canonical.
SUMMARY_MULTIWORD = {
    "title": "Taiwan Semiconductor Manufacturing Company",
    "titles": {"canonical": "TSMC"},
}


def _pv_items(views: list[int], start=date(2026, 6, 6)):
    """Build a pageviews payload with `len(views)` consecutive daily points."""
    from datetime import timedelta
    items = []
    for i, v in enumerate(views):
        d = start + timedelta(days=i)
        items.append({
            "project": "en.wikipedia", "article": "Nvidia", "granularity": "daily",
            "timestamp": d.strftime("%Y%m%d") + "00", "access": "all-access",
            "agent": "user", "views": v,
        })
    return {"items": items}


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


# --- pure parsing ------------------------------------------------------------
def test_parse_summary_canonical():
    assert wa.parse_summary(SUMMARY_JSON) == "Nvidia"


def test_parse_summary_underscores_spaces():
    # falls back to `title` when no canonical, spaces → underscores
    assert wa.parse_summary({"title": "Meta Platforms"}) == "Meta_Platforms"
    assert wa.parse_summary(SUMMARY_MULTIWORD) == "TSMC"


def test_parse_summary_empty_is_none():
    assert wa.parse_summary({}) is None


def test_parse_pageviews_sorts_and_maps():
    payload = _pv_items([10, 20, 30])
    series = wa.parse_pageviews(payload)
    assert series == [
        (date(2026, 6, 6), 10), (date(2026, 6, 7), 20), (date(2026, 6, 8), 30),
    ]


def test_parse_pageviews_skips_malformed():
    payload = {"items": [
        {"timestamp": "2026060600", "views": 5},
        {"timestamp": "bad", "views": 9},          # unparseable date
        {"timestamp": "2026060700"},                # missing views
        {"timestamp": "2026060800", "views": None},  # null views
    ]}
    assert wa.parse_pageviews(payload) == [(date(2026, 6, 6), 5)]


def test_parse_pageviews_empty():
    assert wa.parse_pageviews({}) == []
    assert wa.parse_pageviews({"items": []}) == []


def test_summarize_windows():
    # 28 ascending points: 1..28
    series = [(date(2026, 6, 1), v) for v in range(1, 29)]
    agg = wa.summarize(series)
    assert agg["last_7d"] == sum(range(22, 29))   # last 7: 22..28
    assert agg["prev_7d"] == sum(range(15, 22))   # prev 7: 15..21
    assert agg["days"] == 28
    assert agg["avg_28d"] == round(sum(range(1, 29)) / 28, 1)


def test_summarize_empty():
    agg = wa.summarize([])
    assert agg == {"last_7d": 0, "prev_7d": 0, "avg_28d": 0.0, "days": 0}


# --- per-company flow (HTTP + storage stubbed) -------------------------------
def _stub_get(monkeypatch, responses):
    """responses: list of _Resp | None, returned in call order."""
    calls = {"urls": []}
    seq = iter(responses)

    def fake_get(url):
        calls["urls"].append(url)
        return next(seq)

    monkeypatch.setattr(wa, "_get", fake_get)
    return calls


def _capture_upsert(monkeypatch):
    saved = []
    monkeypatch.setattr(
        wa, "upsert_signal",
        lambda key, **kw: saved.append((key, kw)))
    return saved


def test_pull_company_landed(monkeypatch):
    saved = _capture_upsert(monkeypatch)
    calls = _stub_get(monkeypatch, [
        _Resp(200, SUMMARY_JSON),
        _Resp(200, _pv_items(list(range(1, 29)))),  # 28 days 1..28
    ])
    status = wa.pull_company("nvidia", "NVIDIA", today=date(2026, 7, 2))
    assert status == "landed"
    # summary URL encodes the requested title; pageviews URL uses canonical
    assert "summary/NVIDIA" in calls["urls"][0]
    assert "/Nvidia/daily/" in calls["urls"][1]
    assert calls["urls"][1].endswith("/20260604/20260702")  # 28-day window to today
    assert len(saved) == 1
    key, kw = saved[0]
    assert key == "alt.wiki_attention"
    assert kw["company_id"] == "nvidia"
    assert kw["period_end"] == date(2026, 7, 2)
    assert kw["unit"] == "views" and kw["source"] == "wiki_attention"
    assert kw["value"] == float(sum(range(22, 29)))  # last 7 days
    assert kw["meta"]["prev_7d"] == sum(range(15, 22))
    assert kw["meta"]["resolved_title"] == "Nvidia"
    assert kw["meta"]["days"] == 28


def test_pull_company_unresolved_404(monkeypatch):
    saved = _capture_upsert(monkeypatch)
    _stub_get(monkeypatch, [_Resp(404, {})])
    assert wa.pull_company("obscure", "Nonexistent Heuristic Title") == "unresolved"
    assert saved == []


def test_pull_company_no_views_when_pageviews_404(monkeypatch):
    saved = _capture_upsert(monkeypatch)
    _stub_get(monkeypatch, [_Resp(200, SUMMARY_JSON), _Resp(404, {})])
    assert wa.pull_company("nvidia", "NVIDIA") == "no_views"
    assert saved == []


def test_pull_company_no_views_when_series_empty(monkeypatch):
    saved = _capture_upsert(monkeypatch)
    _stub_get(monkeypatch, [_Resp(200, SUMMARY_JSON), _Resp(200, {"items": []})])
    assert wa.pull_company("nvidia", "NVIDIA") == "no_views"
    assert saved == []


def test_pull_company_error_on_transport_none(monkeypatch):
    saved = _capture_upsert(monkeypatch)
    _stub_get(monkeypatch, [None])  # summary transport failure
    assert wa.pull_company("nvidia", "NVIDIA") == "error"
    assert saved == []


def test_pull_company_error_on_bad_status(monkeypatch):
    _capture_upsert(monkeypatch)
    _stub_get(monkeypatch, [_Resp(500, {})])
    assert wa.pull_company("nvidia", "NVIDIA") == "error"


# --- pull() aggregation ------------------------------------------------------
def test_pull_aggregates_and_caps_limit(monkeypatch):
    _capture_upsert(monkeypatch)
    # Deterministic bindings: three companies with wiki titles.
    class _B:
        def __init__(self, t):
            self.wiki_title = t
    monkeypatch.setattr(wa, "bindings", lambda: {
        "a": _B("Alpha"), "b": _B("Beta"), "c": _B("Gamma"),
    })
    # a → landed, b → unresolved (limit=2 stops before c)
    seq = iter([
        _Resp(200, SUMMARY_JSON), _Resp(200, _pv_items([1, 2, 3, 4, 5, 6, 7, 8])),
        _Resp(404, {}),
    ])
    monkeypatch.setattr(wa, "_get", lambda url: next(seq))

    stats = wa.pull(limit=2)
    assert stats["companies"] == 2
    assert stats["landed"] == 1
    assert stats["resolved"] == 1
    assert stats["unresolved"] == 1
    assert stats["errors"] == 0


def test_pull_per_item_exception_never_raises(monkeypatch):
    _capture_upsert(monkeypatch)

    class _B:
        wiki_title = "Alpha"
    monkeypatch.setattr(wa, "bindings", lambda: {"a": _B()})

    def boom(url):
        raise RuntimeError("network exploded")
    monkeypatch.setattr(wa, "_get", boom)

    stats = wa.pull()  # must not raise
    assert stats["companies"] == 1 and stats["errors"] == 1 and stats["landed"] == 0


def test_available_is_true():
    assert wa.available() is True


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
