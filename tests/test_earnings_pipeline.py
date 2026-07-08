"""ET-P1:earnings.py 第一批 + upsert_calendar meta 合并。seeded_db,2099 隔离,合成 prices。"""
from __future__ import annotations

import datetime as dt

import pytest

from xar.research import earnings
from xar.storage import db, structured


@pytest.fixture()
def _clean(seeded_db):
    def wipe():
        db.execute("DELETE FROM event_calendar WHERE company_id='now' AND scheduled_for >= '2099-01-01'")
        db.execute("DELETE FROM prices WHERE ticker='NOW' AND d >= '2099-01-01'")
    wipe()
    yield
    wipe()


def test_upcoming_calendar_includes_meta_session(seeded_db):
    # 评审 #1:upcoming_calendar 必须带 meta,否则前瞻路径 session(amc/bmo)恒 None
    fut = dt.date.today() + dt.timedelta(days=40)     # 独特远期,避开真实 'now' 财报聚簇
    db.execute("DELETE FROM event_calendar WHERE company_id='now' AND scheduled_for=%s", (fut,))
    try:
        structured.upsert_calendar("now", "earnings", fut, title="ET meta test", status="scheduled",
                                   source="test", meta={"session": "amc"})
        rows = structured.upcoming_calendar(["now"], days=60)
        r = next((x for x in rows if x["scheduled_for"] == fut), None)
        assert r is not None and "meta" in r
        assert (r["meta"] or {}).get("session") == "amc"
    finally:
        db.execute("DELETE FROM event_calendar WHERE company_id='now' AND scheduled_for=%s", (fut,))


def test_upsert_calendar_meta_merges(_clean):
    d = dt.date(2099, 3, 15)
    # 源 A 写 hour(如 finnhub);源 B(yahoo)重拉写 surprise_pct —— 两键都要在
    structured.upsert_calendar("now", "earnings", d, title="NOW earnings",
                               status="scheduled", source="finnhub", meta={"hour": "amc"})
    structured.upsert_calendar("now", "earnings", d, title="NOW earnings",
                               status="occurred", source="yahoo", meta={"surprise_pct": 5.0})
    r = db.query("SELECT meta FROM event_calendar WHERE company_id='now' AND scheduled_for=%s", (d,))
    assert r and r[0]["meta"].get("hour") == "amc"          # 未被抹掉
    assert r[0]["meta"].get("surprise_pct") == 5.0          # 新键合并进来


def test_reaction_return_amc_and_bmo(monkeypatch):
    # 纯口径单测:桩 _closes → 合成连续交易日收盘(隔离 catalyst_returns 未来日安全门 + 真实 NOW 价)
    closes = [(dt.date(2099, 6, 29), 100.0), (dt.date(2099, 6, 30), 102.0), (dt.date(2099, 7, 1), 108.0)]
    monkeypatch.setattr(earnings, "_closes", lambda cid, s, e: closes)
    amc = earnings.reaction_return("now", dt.date(2099, 6, 30), "amc")
    assert amc and abs(amc["reaction_pct"] - (108 / 102 - 1) * 100) < 1e-2   # close(D+1)/close(D)
    bmo = earnings.reaction_return("now", dt.date(2099, 6, 30), "bmo")
    assert bmo and abs(bmo["reaction_pct"] - (102 / 100 - 1) * 100) < 1e-2   # close(D)/close(D-1)
    none_sess = earnings.reaction_return("now", dt.date(2099, 6, 30), None)
    assert none_sess and "inferred" in none_sess["session"]                  # 默认 amc 口径


def test_beat_stats(monkeypatch):
    # 纯口径单测:桩 _occurred_earnings → 最新在前 [+3,+2,-1,+4] → beat 3/4;streak=2(最新起连续 beat)
    rows = [{"scheduled_for": dt.date(2099, 6, 30) - dt.timedelta(days=90 * i),
             "meta": {"surprise_pct": sp}} for i, sp in enumerate([3.0, 2.0, -1.0, 4.0])]
    monkeypatch.setattr(earnings, "_occurred_earnings", lambda cid, n: rows)
    bs = earnings.beat_stats("now", n=8)
    assert bs["n"] == 4 and bs["beat_rate"] == 0.75 and bs["streak"] == 2
    assert abs(bs["avg_abs_surprise_pct"] - 2.5) < 1e-6      # (3+2+1+4)/4
