"""ET-P2:季报 dossier 组装器 —— 各节出现、known_ids 新 kind、数字正确、缺数据节优雅缺席。
seeded_db + 2099 隔离(2099 as_of/period_end/d 在 ORDER BY DESC 下压过真实数据)。"""
from __future__ import annotations

import datetime as dt

import pytest

from xar.research import earnings
from xar.storage import db, structured
from xar.storage.altstore import upsert_signal


@pytest.fixture()
def _seeded(seeded_db):
    ev_date = dt.date(2099, 6, 30)

    def wipe():
        db.execute("DELETE FROM estimates WHERE company_id='now' AND as_of >= '2099-01-01'")
        db.execute("DELETE FROM analyst_ratings WHERE company_id='now' AND as_of >= '2099-01-01'")
        db.execute("DELETE FROM alt_signals WHERE signal_key='alt.options_implied_move' "
                   "AND company_id='now' AND period_end >= '2099-01-01'")
        db.execute("DELETE FROM prices WHERE ticker='NOW' AND d >= '2099-01-01'")
    wipe()
    # 一致预期 + 90 天修订(revenue 上修)
    structured.upsert_estimate("now", "revenue", 1000.0, dt.date(2099, 4, 1), period="0q",
                               n_analysts=20, source="yahoo")
    structured.upsert_estimate("now", "revenue", 1050.0, dt.date(2099, 6, 20), period="0q",
                               n_analysts=22, source="yahoo")
    structured.upsert_estimate("now", "eps_diluted", 3.0, dt.date(2099, 6, 20), period="0q",
                               n_analysts=22, source="yahoo")
    # 评级快照 + 目标价
    structured.upsert_rating("now", dt.date(2099, 6, 25), buy=15, hold=5, sell=1, pt_mean=1200.0,
                             source="yahoo")
    # implied move 序列(本事件)
    for d, v in [(dt.date(2099, 6, 22), 0.06), (dt.date(2099, 6, 28), 0.085)]:
        upsert_signal("alt.options_implied_move", company_id="now", period_end=d, value=v,
                      unit="ratio", source="implied_move",
                      meta={"earnings_date": str(ev_date), "atm_iv": 0.62, "expiry": "2099-07-03"})
    # 价格(近 21 日)
    for i in range(30):
        d = ev_date - dt.timedelta(days=i)
        db.execute("INSERT INTO prices(ticker,d,close,source,company_id) "
                   "VALUES('NOW',%s,%s,'test','now') ON CONFLICT DO NOTHING", (d, 1000 + i))
    yield ev_date
    wipe()


def _event(ev_date):
    return {"id": 99999, "company_id": "now", "scheduled_for": ev_date,
            "meta": {"session": "amc"}, "event_type": "earnings"}


def test_dossier_sections_and_known_ids(_seeded, monkeypatch):
    ev_date = _seeded
    # 桩纯计算子件让断言确定(不依赖真实 'now' 财报/信号数据)
    monkeypatch.setattr(earnings, "beat_stats", lambda cid, n=8: {
        "n": 4, "beat_rate": 0.75, "streak": 2, "avg_abs_surprise_pct": 3.0,
        "rows": [{"date": "2099-03-31", "surprise_pct": 5.0}]})
    monkeypatch.setattr(earnings, "hist_move_stats", lambda cid, n=8: {
        "n": 4, "avg_abs_move_pct": 6.0, "max_abs_move_pct": 11.0, "rows": []})

    d = earnings.dossier_earnings("now", _event(ev_date))
    assert d is not None
    text, known, panel = d["text"], d["known_ids"], d["panel"]

    # 各节标题出现
    for h in ("## 财报事件", "## 预期设定", "## beat 习惯", "## 评级动量",
              "## implied vs 历史波动", "## 价格语境", "## 覆盖缺口"):
        assert h in text, f"missing section {h}"

    # known_ids 含新 kind
    assert f"calendar:{99999}" in known
    assert "estimate:now:revenue" in known
    assert any(k.startswith("ratings:") for k in known)
    assert any(k.startswith("alt:alt.options_implied_move:") for k in known)
    assert "price:now:recent" in known

    # 数字正确:beat 率 / implied 最新 8.5%(修订漂移读真实库,单测另测,见下)
    assert panel["beat_habit"]["beat_rate"] == 0.75
    assert abs(float(panel["implied_move"]["series"][0]["value"]) - 0.085) < 1e-6
    assert d["n_facts"] >= 4


def test_revision_drift_math(monkeypatch):
    # 纯口径单测:桩 estimate_series → 90 天窗内 1000→1050 = +5%
    import datetime as _dt
    rows = [{"as_of": _dt.date.today() - _dt.timedelta(days=60), "value": 1000.0, "n_analysts": 20},
            {"as_of": _dt.date.today() - _dt.timedelta(days=5), "value": 1050.0, "n_analysts": 22}]
    monkeypatch.setattr(earnings.structured, "estimate_series", lambda c, m, p: rows)
    d = earnings._revision_drift("now", "revenue")
    assert d and abs(d["drift_pct"] - 5.0) < 1e-6 and d["n_analysts"] == 22


def test_dossier_graceful_absence(seeded_db, monkeypatch):
    # 无种子数据的公司事件 → 不炸,覆盖缺口如实声明
    db.execute("DELETE FROM alt_signals WHERE signal_key='alt.options_implied_move' "
               "AND company_id='asts_spa' AND period_end >= '2099-01-01'")
    monkeypatch.setattr(earnings, "beat_stats", lambda cid, n=8: {"n": 0, "beat_rate": None,
                        "streak": 0, "avg_abs_surprise_pct": None, "rows": []})
    monkeypatch.setattr(earnings, "hist_move_stats", lambda cid, n=8: {"n": 0, "avg_abs_move_pct": None,
                        "max_abs_move_pct": None, "rows": []})
    ev = {"id": 88888, "company_id": "asts_spa", "scheduled_for": dt.date(2099, 9, 30),
          "meta": {}, "event_type": "earnings"}
    d = earnings.dossier_earnings("asts_spa", ev)
    assert d is not None
    assert any("期权隐含波动" in g for g in d["panel"]["coverage_gaps"])
    assert "## 财报事件" in d["text"]      # 事件头始终在
