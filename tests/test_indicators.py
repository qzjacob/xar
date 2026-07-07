"""衍生指标计算引擎测试(seeded_db,零 LLM)。

验证:同比精确、增速二阶导符号(减速<0)、幂等(双跑行数不变)、min_points 跳过、
derived 产物不被当作输入(无自反馈)。
"""
from __future__ import annotations

import datetime as dt

import pytest

from xar.research import indicators
from xar.storage import db, structured

_METRICS = ["crpo", "crpo_yoy", "crpo_yoy_accel"]
_Q = [dt.date(2024, 3, 31), dt.date(2024, 6, 30), dt.date(2024, 9, 30),
      dt.date(2024, 12, 31), dt.date(2025, 3, 31), dt.date(2025, 6, 30)]
# 100→110→120→130→125→132:2025Q1 同比 125/100-1=0.25,2025Q2 同比 132/110-1=0.20(减速)
_VALS = [100.0, 110.0, 120.0, 130.0, 125.0, 132.0]


@pytest.fixture()
def _clean(seeded_db):
    db.execute("DELETE FROM fundamentals WHERE company_id = ANY(%s) AND metric = ANY(%s)",
               (["now", "snow"], _METRICS))
    yield
    db.execute("DELETE FROM fundamentals WHERE company_id = ANY(%s) AND metric = ANY(%s)",
               (["now", "snow"], _METRICS))


def _seed(cid: str, dates, vals):
    for d, v in zip(dates, vals):
        structured.upsert_fundamental(cid, "crpo", v, period=f"Q-{d}", period_end=d,
                                      freq="quarter", unit="USD", source="pytest")


def _latest(cid: str, metric: str) -> dict | None:
    rows = db.query("SELECT value, period_end FROM fundamentals WHERE company_id=%s AND metric=%s "
                    "AND source='derived' ORDER BY period_end DESC LIMIT 1", (cid, metric))
    return rows[0] if rows else None


def test_crpo_yoy_exact(_clean):
    _seed("now", _Q, _VALS)
    indicators.compute_company("now")
    latest = _latest("now", "crpo_yoy")
    assert latest is not None
    assert latest["period_end"] == dt.date(2025, 6, 30)
    assert abs(latest["value"] - 0.20) < 1e-6
    # 2025Q1 点应为 0.25
    q1 = db.query("SELECT value FROM fundamentals WHERE company_id='now' AND metric='crpo_yoy' "
                  "AND source='derived' AND period_end=%s", (dt.date(2025, 3, 31),))
    assert q1 and abs(q1[0]["value"] - 0.25) < 1e-6


def test_crpo_yoy_accel_decelerating(_clean):
    _seed("now", _Q, _VALS)
    indicators.compute_company("now")
    accel = _latest("now", "crpo_yoy_accel")
    assert accel is not None
    # 0.20 - 0.25 = -0.05 < 0(增速在减速)
    assert abs(accel["value"] - (-0.05)) < 1e-6
    assert accel["value"] < 0


def test_idempotent_double_run(_clean):
    _seed("now", _Q, _VALS)
    indicators.compute_company("now")
    n1 = db.query("SELECT count(*) c FROM fundamentals WHERE company_id='now' "
                  "AND metric='crpo_yoy' AND source='derived'")[0]["c"]
    indicators.compute_company("now")
    n2 = db.query("SELECT count(*) c FROM fundamentals WHERE company_id='now' "
                  "AND metric='crpo_yoy' AND source='derived'")[0]["c"]
    assert n1 == n2 == 2


def test_min_points_skips(_clean):
    # 只 4 点 < min_points(5)→ 不产出任何衍生
    _seed("snow", _Q[:4], _VALS[:4])
    indicators.compute_company("snow")
    assert _latest("snow", "crpo_yoy") is None


def test_freq_homogenized_no_cross_freq_yoy(_clean):
    # 评审 #1:年报(全年)与季报混在同一序列会把季度值同比全年值 → 荒谬同比。
    # 播 6 季 crpo + 2 个年末 annual 全年值;_series 应只保留 quarter,annual 被剔除。
    _seed("now", _Q, _VALS)
    structured.upsert_fundamental("now", "crpo", 999.0, period="FY2024",
                                  period_end=dt.date(2024, 12, 31), freq="annual", source="gangtise")
    s = indicators._series("now", "crpo")           # prefer quarter
    assert all(r["freq"] == "quarter" for r in s)
    assert len(s) == 6                                # annual 行被同质化剔除


def test_derived_not_fed_back(_clean):
    _seed("now", _Q, _VALS)
    indicators.compute_company("now")
    # 二次计算若把 derived crpo_yoy 当作 base,会产出 crpo_yoy 的衍生(不存在这种指标)——
    # 断言:base 序列排除了 source='derived'(crpo 的原始序列仍是 6 点,不含衍生)。
    base = indicators._series("now", "crpo")
    assert len(base) == 6
    assert all("derived" != r["source"] for r in base)
