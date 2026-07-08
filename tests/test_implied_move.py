"""ET-P1:期权隐含波动 provider —— straddle 数学 / expiry 选择 / period_end=今日 / 幂等 / 跳过。
离线:假 tk(options/option_chain/fast_info),无网络。"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from xar.providers.alt import implied_move as im


def test_pick_expiry_first_after_reaction_day():
    exps = ["2099-06-20", "2099-06-27", "2099-07-18"]
    assert im._pick_expiry(exps, dt.date(2099, 6, 25)) == "2099-06-27"
    assert im._pick_expiry(exps, dt.date(2099, 6, 27)) == "2099-06-27"
    # 全在反应日之前 → 取最后一个(最近的更长月)
    assert im._pick_expiry(["2099-06-20"], dt.date(2099, 6, 25)) == "2099-06-20"
    assert im._pick_expiry([], dt.date(2099, 6, 25)) is None


def _chain(spot):
    # ATM 在 strike=100;call mid=(3+5)/2=4,put mid=(2+4)/2=3 → straddle 7
    calls = pd.DataFrame([{"strike": 90, "bid": 11, "ask": 13, "lastPrice": 12, "impliedVolatility": 0.5},
                          {"strike": 100, "bid": 3, "ask": 5, "lastPrice": 4, "impliedVolatility": 0.6},
                          {"strike": 110, "bid": 1, "ask": 2, "lastPrice": 1.5, "impliedVolatility": 0.7}])
    puts = pd.DataFrame([{"strike": 90, "bid": 1, "ask": 2, "lastPrice": 1.5, "impliedVolatility": 0.55},
                         {"strike": 100, "bid": 2, "ask": 4, "lastPrice": 3, "impliedVolatility": 0.62},
                         {"strike": 110, "bid": 9, "ask": 11, "lastPrice": 10, "impliedVolatility": 0.72}])
    return type("Chain", (), {"calls": calls, "puts": puts})()


def test_atm_straddle_math():
    st = im._atm_straddle(_chain(100.0), 100.0)
    assert st is not None
    straddle, atm_iv = st
    assert abs(straddle - 7.0) < 1e-6
    assert abs(atm_iv - 0.61) < 1e-6      # (0.60+0.62)/2


def test_mid_falls_back_to_last():
    assert im._mid(0, 0, 4.2) == 4.2      # 双零 → lastPrice
    assert im._mid(3, 5, 99) == 4.0       # 正常中价
    assert im._mid(0, 0, 0) is None


class _FakeTk:
    options = ("2099-06-27",)
    fast_info = {"last_price": 100.0}

    def option_chain(self, exp):
        return _chain(100.0)


@pytest.fixture()
def _clean(seeded_db):
    from xar.storage import db, kvstate

    def wipe():
        db.execute("DELETE FROM alt_signals WHERE signal_key='alt.options_implied_move' "
                   "AND period_end >= '2099-01-01'")
        db.execute("DELETE FROM event_calendar WHERE company_id='now' AND scheduled_for >= '2099-01-01'")
        kvstate.save_state("earnings_watch", {})
    wipe()
    yield
    wipe()


def test_pull_writes_signal_period_end_today(_clean, monkeypatch):
    from xar.storage import db

    # 桩 _window_names(隔离真实 'now' 日历行:dedup-to-earliest 会挑真实事件而非测试事件)
    fut = dt.date.today() + dt.timedelta(days=4)
    monkeypatch.setattr(im, "_window_names", lambda: [("now", "NOW", fut, "amc")])
    monkeypatch.setattr(im, "available", lambda: True)
    monkeypatch.setattr("xar.providers.yahoo._handle", lambda cid, tk: ("NOW", _FakeTk()))
    out = im.pull()
    assert out["written"] >= 1
    r = db.query("SELECT value, period_end, meta FROM alt_signals "
                 "WHERE signal_key='alt.options_implied_move' AND company_id='now'")
    assert r and abs(float(r[0]["value"]) - 0.07) < 1e-6     # straddle 7 / spot 100
    assert r[0]["period_end"] == dt.date.today()             # 快照日,非财报日
    assert r[0]["meta"]["earnings_date"] == str(fut)
    # 幂等:重拉不新增行
    im.pull()
    n = db.query("SELECT count(*) c FROM alt_signals WHERE signal_key='alt.options_implied_move' "
                 "AND company_id='now' AND period_end=%s", (dt.date.today(),))[0]["c"]
    assert n == 1


def test_pull_skips_when_no_expiry(_clean, monkeypatch):
    from xar.storage import structured

    fut = dt.date.today() + dt.timedelta(days=3)
    structured.upsert_calendar("now", "earnings", fut, title="NOW earnings",
                               status="scheduled", source="yahoo", meta={"session": "amc"})

    class _NoExp:
        options = ()
        fast_info = {"last_price": 100.0}

        def option_chain(self, exp):
            return _chain(100.0)
    monkeypatch.setattr(im, "available", lambda: True)
    monkeypatch.setattr("xar.providers.yahoo._handle", lambda cid, tk: ("NOW", _NoExp()))
    out = im.pull()
    assert out["written"] == 0 and out["skipped"] >= 1
