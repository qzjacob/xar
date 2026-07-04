"""另类数据本体 + 高频校正引擎:绑定派生、z-score、支柱映射、信号→事件幂等。
离线部分纯计算;DB 部分用 seeded_db 夹具,自清合成信号。"""
from __future__ import annotations

from datetime import date

import pytest

from xar.ontology import altdata
from xar.research import thesis_signals


# ── 离线:本体口径 ─────────────────────────────────────────────────────────────
def test_signal_specs_valid():
    from xar.ontology.thesis import PILLAR_KINDS

    keys = [s.key for s in altdata.ALT_SIGNALS]
    assert len(keys) == len(set(keys)), "duplicate signal_key"
    for s in altdata.ALT_SIGNALS:
        assert s.cadence in ("daily", "weekly", "monthly")
        assert s.scope in ("company", "theme")
        assert s.good_when in ("rising", "falling", None)
        assert all(k in PILLAR_KINDS for k in s.pillar_kinds)
        assert s.source and s.rationale_zh


def test_tw_code_derivation():
    assert altdata._tw_code({"tickers": ["2330.TW"]}) == "2330"
    assert altdata._tw_code({"tickers": ["6488.TWO"]}) == "6488"
    assert altdata._tw_code({"tickers": ["NVDA"]}) is None
    assert altdata._tw_code({"tickers": []}) is None


def test_binding_signals_from_shape():
    b = altdata.AltBinding(company_id="x", tw_code="2330", wiki_title="TSMC",
                           github_orgs=("tsmc",), pypi_packages=("foo",),
                           ats=("greenhouse", "tsmc"))
    sigs = set(b.signals())
    assert sigs == {"alt.tw_monthly_revenue", "alt.github_momentum",
                    "alt.pkg_downloads", "alt.hiring_velocity", "alt.wiki_attention"}
    empty = altdata.AltBinding(company_id="y")
    assert empty.signals() == ()


def test_bindings_derive_over_universe():
    bs = altdata.bindings()
    assert len(bs) > 100
    tw = [b for b in bs.values() if b.tw_code]
    assert len(tw) >= 100  # ~143 TW-listed


# ── 离线:z-score + 方向语义 ───────────────────────────────────────────────────
def test_zscore_and_direction():
    # rising series with a spike -> high positive z
    series = [{"period_end": date(2025, m, 28), "value": v}
              for m, v in zip(range(8, 0, -1), [1800, 1210, 1180, 1150, 1120, 1090, 1060, 1000])]
    z = thesis_signals._zscore(series, min_history=6)
    assert z is not None and z["z"] > 1.5 and z["n"] == 8

    spec_rising = altdata.SIGNALS_BY_KEY["alt.tw_monthly_revenue"]  # good_when=rising
    assert thesis_signals._contribution(spec_rising, 3.0) == 1.0     # +z, rising -> +1
    # good_when=None -> zero contribution regardless of z
    wiki = altdata.SIGNALS_BY_KEY["alt.wiki_attention"]
    assert wiki.good_when is None
    assert thesis_signals._contribution(wiki, 3.0) == 0.0


def test_zscore_insufficient_history():
    series = [{"period_end": date(2025, 1, 28), "value": 100.0}]
    assert thesis_signals._zscore(series, min_history=6) is None


# ── 离线:orchestrator 优雅跳过缺失 provider ───────────────────────────────────
def test_alt_orchestrator_skips_missing():
    from xar.ingestion import alt

    r = alt.pull_source("definitely_not_a_provider")
    assert "skipped" in r


# ── DB:信号→支柱→事件 全链幂等 ───────────────────────────────────────────────
@pytest.fixture
def alt_company(seeded_db):
    from xar.ingestion.registry import COMPANIES
    from xar.storage import altstore, db

    cid = next(c["id"] for c in COMPANIES if altdata._tw_code(c))
    db.execute("DELETE FROM alt_signals WHERE company_id=%s AND source='pytest'", (cid,))
    db.execute("DELETE FROM kg_events WHERE license_tag='alt' AND company_id=%s", (cid,))
    for i, m in enumerate(range(1, 9)):
        val = 1000.0 * (1 + 0.03 * i)
        if i == 7:
            val = 1900.0  # spike
        altstore.upsert_signal("alt.tw_monthly_revenue", period_end=date(2025, m, 28),
                               value=val, company_id=cid, unit="TWD", source="pytest")
    yield cid
    db.execute("DELETE FROM alt_signals WHERE company_id=%s AND source='pytest'", (cid,))
    db.execute("DELETE FROM kg_events WHERE license_tag='alt' AND company_id=%s", (cid,))


def test_snapshot_and_pillar_scores(alt_company):
    snap = thesis_signals.signal_snapshot(alt_company)
    rev = next(s for s in snap if s["signal_key"] == "alt.tw_monthly_revenue")
    assert rev["z"] >= 2.0 and rev["contribution"] > 0.5
    scores = thesis_signals.pillar_signal_scores(alt_company)
    assert scores["demand"]["score"] > 0.5 and scores["financials"]["score"] > 0.5


def test_sync_alt_events_idempotent(alt_company):
    from xar.storage import db

    first = thesis_signals.sync_alt_events()
    assert first["inserted"] >= 1
    second = thesis_signals.sync_alt_events()
    # the spike month already emitted -> no new insert for it
    rows = db.query("SELECT polarity, category FROM semantic_facts "
                    "WHERE company_id=%s AND category='alt_signal'", (alt_company,))
    assert rows and rows[0]["polarity"] == "positive"
    assert second["inserted"] == 0 or second["skipped"] >= first["inserted"]
