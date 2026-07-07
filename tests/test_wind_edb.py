"""Wind EDB → alt_signals 离线测试(monkeypatch _mcp_call;seeded_db for upsert)。

验证:多形状序列解析、月末归一、幂等(双跑行数不变)、单指标失败不拖批;
+ altdata 新 EDB 信号谱系的合法性不变式。
"""
from __future__ import annotations

import datetime as dt

import pytest

from xar.ontology.altdata import ALT_SIGNALS, PILLAR_KINDS
from xar.providers.alt import wind_edb
from xar.storage import db


def test_edb_specs_valid():
    edb = [s for s in ALT_SIGNALS if s.source == "wind_edb"]
    assert edb, "no wind_edb specs registered"
    from xar.ingestion.registry import THEMES
    for s in edb:
        assert s.key in wind_edb.EDB_QUESTIONS, f"{s.key} missing question"
        assert all(t in THEMES for t in s.themes), f"{s.key}: bad theme"
        assert all(k in PILLAR_KINDS for k in s.pillar_kinds)
        assert s.scope == "theme"


def test_extract_series_shapes():
    assert wind_edb._extract_series({"data": [{"date": "2099-01", "value": "12.3"}]}) == \
        [(dt.date(2099, 1, 31), 12.3)]
    # items/time/val 变体 + 逗号数字
    s = wind_edb._extract_series({"items": [{"time": "2099-02-28", "val": "1,234.5"}]})
    assert s == [(dt.date(2099, 2, 28), 1234.5)]
    assert wind_edb._extract_series({"nope": 1}) == []
    # Wind 真机形状:嵌套 data.data + 列式并行数组(date/value)——真机捕获
    real = {"data": {"code": 0, "data": [{"meta": {"unit": "十亿美元"},
            "date": ["20250630", "20250731"], "value": [59.91, 62.14]}]}}
    assert wind_edb._extract_series(real) == [(dt.date(2025, 6, 30), 59.91),
                                              (dt.date(2025, 7, 31), 62.14)]


def test_to_period_end():
    assert wind_edb._to_period_end("202901") == dt.date(2029, 1, 31)
    assert wind_edb._to_period_end("2099/03") == dt.date(2099, 3, 31)
    assert wind_edb._to_period_end("bad") is None


@pytest.fixture()
def _clean(seeded_db):
    def wipe():
        db.execute("DELETE FROM alt_signals WHERE source='wind_edb' AND period_end >= '2099-01-01'")
    wipe()
    yield
    wipe()


def test_pull_idempotent(_clean, monkeypatch):
    monkeypatch.setattr(wind_edb, "available", lambda: True)
    series = {"data": [{"date": "2099-01-31", "value": 100.0},
                       {"date": "2099-02-28", "value": 110.0}]}
    monkeypatch.setattr(wind_edb, "_fetch", lambda q, b, e: series)
    out = wind_edb.pull(limit=1)
    assert out["indicators"] == 1 and out["points"] == 2
    n1 = db.query("SELECT count(*) c FROM alt_signals WHERE source='wind_edb' "
                  "AND period_end >= '2099-01-01'")[0]["c"]
    wind_edb.pull(limit=1)                        # 双跑幂等
    n2 = db.query("SELECT count(*) c FROM alt_signals WHERE source='wind_edb' "
                  "AND period_end >= '2099-01-01'")[0]["c"]
    assert n1 == n2 == 2


def test_pull_empty_series_skips(_clean, monkeypatch):
    monkeypatch.setattr(wind_edb, "available", lambda: True)
    monkeypatch.setattr(wind_edb, "_fetch", lambda q, b, e: {"data": []})
    out = wind_edb.pull(limit=1)
    assert out["indicators"] == 0 and len(out["skipped"]) == 1
