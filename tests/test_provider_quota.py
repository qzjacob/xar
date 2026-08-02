"""沪日额度账本 `storage/quota.py` 的回归(2026-08-02)。

守的是三条不变量,每条都对应一次真实事故或一个真实陷阱:

① **累加在数据库端做**。alphapai/aifinmarket 有两个调用进程(glmworker 抓取链 +
   dagster 夜批分片),各自数各自的再合并没有正确语义 —— 「每账号每日 N 次」的帽
   只有 DB 端 `calls + EXCLUDED.calls` 才管得住。
② **日界进主键 ⇒ 换日即换行**。没有「重置」这个动作,也就没有重置竞态。
   昨天的耗尽状态绝不能影响今天,而这不靠任何重置代码保证,靠主键。
③ **读 fail-open**。额度门是优化信号(省调用),不是预算帽(省钱)——
   DB 读不到时必须放行,不能因为读不到额度状态就把抓取链停掉。
"""
from __future__ import annotations

import pytest

from xar.storage import db, quota

_P = "zz_test_provider"


@pytest.fixture()
def _clean(isolated_db):
    quota.snapshot(_P)                       # 触发 _ensure() —— 裸 CLI 场景下表可能还不存在
    db.execute("DELETE FROM provider_quota WHERE provider=%s", (_P,))
    yield _P


# ── ① 累加与席位隔离 ──────────────────────────────────────────────────────────
def test_bump_accumulates_in_db(_clean):
    assert quota.bump(_P)["calls"] == 1
    assert quota.bump(_P, n=4)["calls"] == 5


def test_bump_returns_post_write_value(_clean):
    """必须返回**写入后**的值 —— 调用方拿它判日帽。返回写前值会导致刚好超一发。"""
    quota.bump(_P, n=9)
    assert quota.bump(_P)["calls"] == 10


def test_seats_are_isolated(_clean):
    """多账号:每个席位一行,互不干扰。aifinmarket 的日帽是**每账号**的。"""
    quota.bump(_P, seat="a", n=3)
    quota.bump(_P, seat="b")
    s = quota.snapshot(_P)
    assert s["a"]["calls"] == 3 and s["b"]["calls"] == 1


def test_exhausting_one_seat_does_not_touch_others(_clean):
    """核心多账号语义:一个账号触顶不等于全部触顶,链不该因此交棒。"""
    quota.bump(_P, seat="a")
    quota.bump(_P, seat="b")
    quota.mark_exhausted(_P, seat="a", code="quota")
    s = quota.snapshot(_P)
    assert s["a"]["exhausted"] is True and s["b"]["exhausted"] is False


# ── ② 日界 ────────────────────────────────────────────────────────────────────
def test_cn_date_is_computed_by_the_database(_clean):
    """沪日必须由 DB 算 —— 五个容器对『今天』只能有一个定义,不受容器时区影响。"""
    quota.bump(_P)
    rows = db.query(
        "SELECT cn_date = (now() AT TIME ZONE 'Asia/Shanghai')::date AS ok "
        "FROM provider_quota WHERE provider=%s", (_P,))
    assert rows and rows[0]["ok"] is True


def test_yesterday_exhaustion_does_not_leak_into_today(_clean):
    """换日即换行:昨天耗尽了,今天照样满血 —— 且这不靠任何重置代码,靠主键。"""
    db.execute(
        "INSERT INTO provider_quota(provider, seat, cn_date, calls, exhausted) "
        "VALUES (%s,'-', (now() AT TIME ZONE 'Asia/Shanghai')::date - 1, 999, true)", (_P,))
    assert quota.snapshot(_P) == {}                    # 今天还没有行
    assert quota.bump(_P)["calls"] == 1                # 从 1 开始,不是 1000
    assert quota.snapshot(_P)["-"]["exhausted"] is False


# ── ③ 语义:耗尽 vs 退避 ──────────────────────────────────────────────────────
def test_backoff_is_not_exhaustion(_clean):
    """204/42900 是瞬时节流,**不是**当日耗尽 —— 混淆这两者正是额度剩在桌上的主因。"""
    quota.set_backoff(_P, seconds=60, code="42900")
    s = quota.snapshot(_P)["-"]
    assert s["backing_off"] is True and s["exhausted"] is False


def test_expired_backoff_stops_backing_off(_clean):
    """退避到期自动失效(SQL 端与 now() 比较),不需要任何清理任务。"""
    quota.set_backoff(_P, seconds=-5, code="42900")    # 已过期
    assert quota.snapshot(_P)["-"]["backing_off"] is False


def test_exhausted_survives_further_bumps(_clean):
    """置位后继续调用不该把它冲掉 —— bump 只碰 calls 列。"""
    quota.mark_exhausted(_P, code="203")
    quota.bump(_P)
    assert quota.snapshot(_P)["-"]["exhausted"] is True


def test_last_code_is_truncated(_clean):
    quota.mark_exhausted(_P, code="x" * 500)
    rows = db.query("SELECT length(last_code) n FROM provider_quota WHERE provider=%s", (_P,))
    assert rows[0]["n"] <= 160


# ── ③ fail-open ───────────────────────────────────────────────────────────────
def test_snapshot_fails_open(monkeypatch):
    """DB 抖动时返回空 dict 而不是抛 —— 调用方据此回落进程内镜像继续抓。

    额度门是优化信号,不是预算帽:读不到就放行,绝不能把抓取链停掉。
    (对照 `api_spend` 是 fail-closed —— 那边拦的是花钱,方向相反,刻意不同。)
    """
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(quota.db, "query", boom)
    assert quota.snapshot(_P) == {}


def test_snapshot_shape(_clean):
    """形状契约:{seat: {calls,int / exhausted,bool / backing_off,bool}}。"""
    quota.bump(_P, seat="s1", n=2)
    v = quota.snapshot(_P)["s1"]
    assert set(v) == {"calls", "exhausted", "backing_off"}
    assert isinstance(v["calls"], int) and isinstance(v["exhausted"], bool)
