"""财报日历「幽灵日期」回归(2026-07-26 用户报告:Phanny 显示 NOW 7-26 / GOOGL 7-28,实际均 7-22)。

根因:`upsert_calendar` 的 dedup_key 含 scheduled_for → 供应商每日重估日期就插一条**新行**
(yahoo 实测逐日漂移),真实季报开完(status='occurred')后,这些过期的 `scheduled` 估计仍留在库里,
`upcoming_calendar` 把最早的未来幽灵行当成「下一次财报」返回 → Phanny/earnings 拿到错日期。

两道修复各自加锁:
  ① upcoming_calendar 抑制「其前 45 天内已有 occurred」的 scheduled 财报行;
  ② upsert_calendar 对同源同季度的日期修订**原地改期**而非插新行;
"""
from __future__ import annotations

import datetime as dt

import pytest

from xar.storage import structured


@pytest.fixture
def cal(isolated_db):
    """事务隔离:本用例的日历写入全部回滚,不污染生产 event_calendar。"""
    from xar.storage import db
    yield db


def _mk(cid, d, *, status="scheduled", source="yahoo", title=None):
    return structured.upsert_calendar(cid, "earnings", d, status=status, source=source,
                                      title=title or f"{cid} earnings")


def test_occurred_suppresses_stale_future_estimate(cal):
    """NOW 复现:实际 7-22 已 occurred,yahoo 仍留 7-26 估计 → 不得再作为下一次财报返回。"""
    today = dt.date.today()
    occurred = today - dt.timedelta(days=4)
    ghost = today + dt.timedelta(days=1)
    _mk("now", occurred, status="occurred", source="finnhub")
    _mk("now", ghost, status="scheduled", source="yahoo", title="now earnings ghost")
    rows = structured.upcoming_calendar(["now"], days=60, limit=20)
    dates = [r["scheduled_for"] for r in rows if r["event_type"] == "earnings"]
    assert ghost not in dates, f"过期估计 {ghost} 仍被当作下一次财报(应被 occurred {occurred} 抑制)"


def test_genuine_next_quarter_survives(cal):
    """真正的下一季度(~91 天后)不得被误抑制 —— 抑制窗只有 45 天。"""
    today = dt.date.today()
    _mk("googl", today - dt.timedelta(days=4), status="occurred", source="yahoo")
    nxt = today + dt.timedelta(days=95)
    _mk("googl", nxt, status="scheduled", source="finnhub")
    rows = structured.upcoming_calendar(["googl"], days=200, limit=20)
    dates = [r["scheduled_for"] for r in rows if r["event_type"] == "earnings"]
    assert nxt in dates, "真实下一季度财报被误抑制"


def test_same_quarter_multi_source_collapses_to_one(cal):
    """多源对同一次季报给出差几天的估计 → 同 (公司,季度) 只返回一行(下游不重复处理)。"""
    today = dt.date.today()
    d1 = today + dt.timedelta(days=10)
    _mk("msft", d1, source="yahoo")
    _mk("msft", d1 + dt.timedelta(days=3), source="finnhub")
    rows = [r for r in structured.upcoming_calendar(["msft"], days=60, limit=20)
            if r["event_type"] == "earnings"]
    # 两行同属一个季度 → 收敛为 1
    same_q = [r for r in rows if r["scheduled_for"].month == d1.month]
    assert len(same_q) == 1, f"同季度财报未收敛,返回 {len(same_q)} 行: {[r['scheduled_for'] for r in same_q]}"


def test_date_revision_updates_in_place(cal):
    """同源日期修订原地改期(根因修复):yahoo 逐日重估不再堆幽灵行。"""
    from xar.storage import db
    today = dt.date.today()
    _mk("amzn", today + dt.timedelta(days=12), source="yahoo")
    _mk("amzn", today + dt.timedelta(days=13), source="yahoo")   # 次日重估
    _mk("amzn", today + dt.timedelta(days=14), source="yahoo")   # 再次重估
    n = db.query("SELECT count(*) c FROM event_calendar WHERE company_id='amzn' "
                 "AND event_type='earnings' AND source='yahoo' AND status='scheduled'")[0]["c"]
    assert n == 1, f"同源日期修订应原地更新为 1 行,实得 {n} 行(幽灵行仍在堆积)"




# ── 以下为对抗性评审(16 confirmed)暴露出的缺陷回归 ──────────────────────────────
def test_non_earnings_row_does_not_evict_earnings(cal):
    """HIGH:同季度的分红/拆股行不得把财报行整行挤出(旧实现的 SQL 窗口分区漏了 event_type,
    评审在真 PG16 上复现:dividend 抢到 rn=1 → 财报行被 WHERE rn=1 删掉 → 全季度静默失明)。"""
    today = dt.date.today()
    ed = today + dt.timedelta(days=20)
    _mk("msft", ed, source="finnhub")
    # 后写 → as_of 更新,正是旧实现里会抢占 rank 的顺序
    structured.upsert_calendar("msft", "dividend", today + dt.timedelta(days=25),
                               title="msft dividend", importance=1, source="yahoo")
    rows = structured.upcoming_calendar(["msft"], days=90, limit=20)
    assert ed in [r["scheduled_for"] for r in rows if r["event_type"] == "earnings"], \
        "财报行被同季度非财报事件挤出 upcoming_calendar"
    assert any(r["event_type"] == "dividend" for r in rows), "非财报事件不应被抑制"


def test_quarter_boundary_straddle_still_collapses(cal):
    """MEDIUM:同一次财报的两源估计跨日历季度边界(9-30 / 10-01)仍须收敛为一行
    (旧实现按 date_trunc('quarter') 分桶 → 劈成两簇,双份重复漏过)。"""
    from xar.storage import db
    db.execute("DELETE FROM event_calendar WHERE company_id='amd' AND event_type='earnings'")
    y = dt.date.today().year + (1 if dt.date.today() > dt.date(dt.date.today().year, 9, 20) else 0)
    _mk("amd", dt.date(y, 9, 30), source="finnhub")
    _mk("amd", dt.date(y, 10, 1), source="yahoo")
    rows = [r for r in structured.upcoming_calendar(["amd"], days=800, limit=50)
            if r["event_type"] == "earnings"]
    assert len(rows) == 1, f"跨季度边界的同一次财报未收敛: {[str(r['scheduled_for']) for r in rows]}"


def test_authoritative_source_wins_over_recency(cal):
    """MEDIUM:簇内不得只看 as_of(它是"最后被触碰"而非"日期断言")——权威源(finnhub)应压过
    后被重拉的 yahoo 漂移估计。"""
    from xar.storage import db
    db.execute("DELETE FROM event_calendar WHERE company_id='meta' AND event_type='earnings'")
    today = dt.date.today()
    authoritative = today + dt.timedelta(days=15)
    _mk("meta", authoritative, source="finnhub")
    _mk("meta", today + dt.timedelta(days=18), source="yahoo")   # 更晚写入 → as_of 更新
    rows = [r for r in structured.upcoming_calendar(["meta"], days=90, limit=20)
            if r["event_type"] == "earnings"]
    assert len(rows) == 1
    assert rows[0]["source"] == "finnhub" and rows[0]["scheduled_for"] == authoritative, \
        f"应取权威源日期,实得 {rows[0]['source']} {rows[0]['scheduled_for']}"


def test_revision_recomputes_dedup_key(cal):
    """MEDIUM:改期后 dedup_key 必须与新日期自洽,否则同日期重报会探测不到而再插重复行。"""
    from xar.storage import db
    db.execute("DELETE FROM event_calendar WHERE company_id='crm' AND event_type='earnings'")
    today = dt.date.today()
    d1, d2 = today + dt.timedelta(days=11), today + dt.timedelta(days=13)
    _mk("crm", d1, source="yahoo")
    _mk("crm", d2, source="yahoo")            # 改期 → 原地更新 + 重算 key
    _mk("crm", d2, source="yahoo")            # 同日期重报 → 必须命中既有行,不得再插
    n = db.query("SELECT count(*) c FROM event_calendar WHERE company_id='crm' "
                 "AND event_type='earnings'")[0]["c"]
    assert n == 1, f"改期后 key 不自洽导致重复行:{n} 行"


def test_future_garbage_occurred_does_not_suppress(cal):
    """脏 occurred 行(实测 now|2098-10-03)不得抑制真实的未来财报(抑制只看该行之前的实际日)。"""
    from xar.storage import db
    db.execute("DELETE FROM event_calendar WHERE company_id='amzn' AND event_type='earnings'")
    today = dt.date.today()
    real = today + dt.timedelta(days=20)
    db.execute("INSERT INTO event_calendar(company_id,event_type,scheduled_for,status,source,"
               "dedup_key) VALUES('amzn','earnings','2098-10-03','occurred','yahoo','garbage-avgo')")
    _mk("amzn", real, source="finnhub")
    rows = [r for r in structured.upcoming_calendar(["amzn"], days=90, limit=20)
            if r["event_type"] == "earnings"]
    assert real in [r["scheduled_for"] for r in rows], "未来的脏 occurred 行误抑制了真实财报"


