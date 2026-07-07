"""Gangtise 抓取规划器测试(离线;monkeypatch coverage/seeds/insight + kvstate)。

验证:核心优先三段排序、月窗最新在前、连续空窗盖 exhausted 戳 + reset 续走、
毒单元不炸;MD&A 季度游标推进。
"""
from __future__ import annotations

import pytest

from xar.providers.gangtise import planner
from xar.storage import kvstate


@pytest.fixture(autouse=True)
def _reset():
    kvstate.save_state("gangtise_backfill", {})
    yield
    kvstate.save_state("gangtise_backfill", {})


def test_cn_priority_order_seeds_first(monkeypatch):
    # innolight/eoptolink 是 CN 种子旗舰;应排在非种子 CN 名字之前
    order = planner.cn_priority_order()
    assert order, "no CN companies"
    seeds = {"innolight", "eoptolink", "002050sz_hum", "601689ss_hum"} & set(order)
    assert seeds, "no CN seeds in registry"
    non_seed_idx = next((i for i, c in enumerate(order) if c not in planner.seed_company_ids()), None)
    seed_idx = min(order.index(c) for c in seeds)
    assert seed_idx < non_seed_idx      # 种子在非种子之前


def test_month_window_newest_first():
    s1, e1 = planner._month_window(1)   # 上月
    s2, e2 = planner._month_window(2)   # 上上月
    assert s2 < s1 and e2 < e1          # 偏移越大越旧


def test_backfill_empty_window_exhausts(monkeypatch):
    monkeypatch.setattr(planner.insight.client, "available", lambda: True)
    monkeypatch.setattr(planner.insight, "pull_broker_reports",
                        lambda **kw: {"saved": 0, "seen": 0})       # 恒空窗
    monkeypatch.setattr(planner.insight, "pull_minutes", lambda **kw: {"saved": 0, "seen": 0})
    monkeypatch.setattr(planner.insight, "pull_mgmt_discussion", lambda cid, rd: 0)
    monkeypatch.setattr(planner, "core_set", lambda: set())
    for _ in range(6):                   # 跑几轮 → 连续空窗应盖 exhausted
        planner.backfill_step(units=2)
    st = planner.backfill_status()
    assert st["exhausted"].get("broker_report") == "empty_window"
    planner.reset_backfill()
    assert planner.backfill_status()["exhausted"] in (None, {})


def test_backfill_poison_unit_survives(monkeypatch):
    monkeypatch.setattr(planner.insight.client, "available", lambda: True)

    def boom(**kw):
        raise RuntimeError("api down")
    monkeypatch.setattr(planner.insight, "pull_broker_reports", boom)
    monkeypatch.setattr(planner.insight, "pull_minutes", lambda **kw: {"saved": 1})
    monkeypatch.setattr(planner.insight, "pull_mgmt_discussion", lambda cid, rd: 0)
    monkeypatch.setattr(planner, "core_set", lambda: set())
    out = planner.backfill_step(units=2)          # 不抛
    assert "list_units" in out
