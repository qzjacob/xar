"""ET-P4:盘后回验 + 校准 —— hit/miss / amc-bmo / event_moved / price_missing / abstain / 分桶。
seeded_db + 2099 隔离;桩 reaction_return 隔离取价。"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from xar.config import get_settings
from xar.research import earnings
from xar.storage import db, structured

_PAST = dt.date.today() - dt.timedelta(days=7)     # 已过财报日
_FUT = dt.date.today() + dt.timedelta(days=30)


def _insert_verdict(cid, ed, direction, conviction, expected_move=0.08, outcome=None):
    db.execute(
        "INSERT INTO earnings_verdicts(company_id,event_date,version,direction,conviction,"
        "expected_move,content,quality,model,as_of,outcome,outcome_at) "
        "VALUES(%s,%s,1,%s,%s,%s,'{}'::jsonb,'{}'::jsonb,'test',%s,"
        + ("%s::jsonb, now()" if outcome else "NULL, NULL") + ")",
        ((cid, ed, direction, conviction, expected_move, _PAST)
         + ((json.dumps(outcome),) if outcome else ())))


@pytest.fixture()
def _clean(isolated_db):
    # 事务隔离(isolated_db)下可安全清**全表** earnings_verdicts:calibration() 聚合全部 verdicts,
    # 生产/残留行会污染全局桶计数(test_calibration_buckets 的 4≠3),清干净才能只看本测试插入的;
    # 全在单事务内、teardown 整体 rollback、绝不落库,生产 verdict 复原(K.3.2 测试隔离)。
    db.execute("DELETE FROM earnings_verdicts")
    # ⚠️ 必须按**容差窗**清,不能只清 scheduled_for=_PAST 那一天(2026-07-31 修)。
    # 被测代码 `_occurred_on(cid, ed, tol_days=earnings_outcome_max_days)` 查的是 ed ±5 天,
    # 而这里此前只精确删 ed 当天 —— 窗内的**真实生产行**删不掉,于是
    # `test_event_moved_when_no_occurred_row` 前提("无 occurred 行")在某些日期根本不成立。
    # 实证:今天 _PAST=2026-07-24,而 ServiceNow('now')真有一行 2026-07-22 `occurred` earnings
    # 落在窗内 → occ 非空 → 走打分而非 event_moved 收尾 → 断言 0 == 1 挂掉。
    # 这是**按日期触发**的假失败:只有真实财报日恰好落进 (today-7)±5 才现形,平时全绿,
    # 与被测逻辑无关。清理窗口跟着 tol 走,才和被测代码看的是同一个范围。
    _tol = dt.timedelta(days=get_settings().earnings_outcome_max_days)
    db.execute("DELETE FROM event_calendar WHERE company_id IN ('now','snow','crm') "
               "AND scheduled_for BETWEEN %s AND %s", (_PAST - _tol, _PAST + _tol))
    yield


def test_score_hit_and_abstain(_clean, monkeypatch):
    # occurred earnings + surprise;桩反应 +5%
    structured.upsert_calendar("now", "earnings", _PAST, title="NOW earnings", status="occurred",
                               source="yahoo", meta={"surprise_pct": 4.0, "session": "amc"})
    structured.upsert_calendar("snow", "earnings", _PAST, title="SNOW earnings", status="occurred",
                               source="yahoo", meta={"surprise_pct": -2.0, "session": "bmo"})
    _insert_verdict("now", _PAST, "long", 8.0)         # 反应+5% → 命中
    _insert_verdict("snow", _PAST, "no_trade", 0.0)    # abstain
    monkeypatch.setattr(earnings, "reaction_return",
                        lambda cid, d, s: {"reaction_pct": 5.0, "session": s or "amc"})
    out = earnings.score_outcomes()
    assert out["scored"] == 2
    row = db.query("SELECT outcome FROM earnings_verdicts WHERE company_id='now' AND model='test'")
    o = row[0]["outcome"]
    assert o["direction_hit"] is True and o["status"] == "scored"
    assert abs(o["realized_vs_implied"] - (5.0 / 8.0)) < 1e-6      # |reaction| / (expected*100)
    snow = db.query("SELECT outcome FROM earnings_verdicts WHERE company_id='snow' AND model='test'")
    assert snow[0]["outcome"]["direction_hit"] == "abstain"


def test_short_direction_miss(_clean, monkeypatch):
    structured.upsert_calendar("crm", "earnings", _PAST, title="CRM earnings", status="occurred",
                               source="yahoo", meta={"surprise_pct": 3.0, "session": "amc"})
    _insert_verdict("crm", _PAST, "short", 7.5)        # 反应+5% 但看空 → miss
    monkeypatch.setattr(earnings, "reaction_return",
                        lambda cid, d, s: {"reaction_pct": 5.0, "session": "amc"})
    earnings.score_outcomes()
    o = db.query("SELECT outcome FROM earnings_verdicts WHERE company_id='crm' AND model='test'")[0]["outcome"]
    assert o["direction_hit"] is False


def test_event_moved_when_no_occurred_row(_clean, monkeypatch):
    # 无 occurred earnings 行 + 超期 → event_moved 收尾(不无限挂起)
    _insert_verdict("now", _PAST, "long", 6.0)
    monkeypatch.setattr(earnings, "reaction_return", lambda cid, d, s: None)
    out = earnings.score_outcomes()
    assert out["event_moved"] == 1
    o = db.query("SELECT outcome FROM earnings_verdicts WHERE company_id='now' AND model='test'")[0]["outcome"]
    assert o["status"] == "event_moved"


def test_price_missing_when_occurred_but_no_price(_clean, monkeypatch):
    structured.upsert_calendar("now", "earnings", _PAST, title="NOW earnings", status="occurred",
                               source="yahoo", meta={"surprise_pct": 1.0, "session": "amc"})
    _insert_verdict("now", _PAST, "long", 6.0)
    monkeypatch.setattr(earnings, "reaction_return", lambda cid, d, s: None)  # 有事件无价
    out = earnings.score_outcomes()
    assert out["price_missing"] == 1


def test_calibration_buckets(_clean):
    # 评分裁决:8.0(hit)+ 5.0(miss)+ **8.5 小数**(hit)→ 桶不漏小数(评审 #4/#7/#9)
    _insert_verdict("now", _PAST, "long", 8.0,
                    outcome={"status": "scored", "direction_hit": True, "reaction_pct": 6.0})
    _insert_verdict("snow", _PAST, "short", 5.0,
                    outcome={"status": "scored", "direction_hit": False, "reaction_pct": 3.0})
    _insert_verdict("crm", _PAST, "long", 8.5,      # 半点 conviction 必须落 7-8 桶,不被丢
                    outcome={"status": "scored", "direction_hit": True, "reaction_pct": 4.0})
    cal = earnings.calibration()
    assert cal["7-8"]["n"] == 2 and cal["7-8"]["hit_rate"] == 1.0   # 8.0 + 8.5 都进桶
    assert cal["4-6"]["hit_rate"] == 0.0 and cal["4-6"]["decided"] == 1
    # 全部 scored 计数守恒(无小数落空)
    assert sum(cal[k]["n"] for k in cal) == 3
