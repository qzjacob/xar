"""M9:Phanny 覆盖范围扩到 Genny 全覆盖库(用户 2026-07-29 裁定)。

要点:
  · `registry` 模式下「够不够格」不再靠硬编码名单预判,由数据可得性在管线里把关
    (无财报日历行走不到 dossier;接地事实 <4 → no_data),且每次跳过都进台账可追问;
  · **配额闸**是全库模式的前提:单名完整辩论约 40 次订阅调用,财报季大量公司同时进窗,
    没有闸一次 book 就能吃干三家订阅额度、饿死 thesis 重建与 link 道;
  · 截断必须留声 —— 静默截断会被读成「今天只有这几家有财报」;
  · ET 的 EARNINGS_UNIVERSE 不受影响(两模块的 universe 与 conviction 刻度一样彼此隔离)。
"""
from __future__ import annotations

import datetime as dt

import pytest

from xar.ontology import phanny_events as pe


class _S:
    phanny_universe_mode = "registry"
    phanny_universe_cap = 40
    phanny_book_max_per_cycle = 12
    phanny_watch_days = 45
    phanny_verdict_lead_days = 3


def test_registry_mode_covers_whole_coverage_library(monkeypatch):
    monkeypatch.setattr(pe, "get_settings", lambda: _S(), raising=False)
    from xar import config
    monkeypatch.setattr(config, "get_settings", lambda: _S())
    ids = pe.universe_ids()
    from xar.ingestion.registry import COMPANIES
    assert len(ids) == len(COMPANIES) > len(pe.PHANNY_UNIVERSE)


def test_list_mode_keeps_curated_names(monkeypatch):
    class _L(_S):
        phanny_universe_mode = "list"
    from xar import config
    monkeypatch.setattr(config, "get_settings", lambda: _L())
    assert pe.universe_ids() == pe.PHANNY_UNIVERSE


def test_et_universe_is_untouched():
    """刻度隔离的同族纪律:两个模块的 universe 也不共享变更。"""
    from xar.ontology.earnings_events import EARNINGS_UNIVERSE
    assert len(EARNINGS_UNIVERSE) == len(pe.PHANNY_UNIVERSE)


def test_book_cap_defers_by_earnings_proximity(monkeypatch):
    """闸住每轮名额,且**按财报临近度**排序 —— 先做最紧迫的,其余顺延(裁决幂等加锁)。"""
    from xar.phanny import engine

    today = dt.date.today()
    rows = [{"company_id": f"c{i}", "event_type": "earnings",
             "scheduled_for": today + dt.timedelta(days=40 - i)} for i in range(20)]
    from xar.storage import structured
    monkeypatch.setattr(structured, "upcoming_calendar", lambda *a, **k: rows)
    monkeypatch.setattr(engine, "get_settings", lambda: _S())
    monkeypatch.setattr(pe, "universe_ids", lambda: tuple(f"c{i}" for i in range(20)),
                        raising=False)
    logged: list = []
    monkeypatch.setattr(engine.buildlog, "record",
                        lambda *a, **k: logged.append(k.get("status")))
    seen: dict = {}
    from xar.phanny import book
    monkeypatch.setattr(book, "run_book",
                        lambda cids, **k: seen.update({"cids": cids}) or {"status": "normal"})
    out = engine.judge_due(run_id="phanny-t")

    assert len(seen["cids"]) == 12                      # 闸生效
    assert seen["cids"][0] == "c19"                     # 最近的财报排最前(40-19=21 天)
    assert out["deferred_to_next_cycle"] == 8
    assert logged.count("skipped") == 8                 # 每个被顺延的名字都留声,不静默丢弃


def test_no_cap_when_within_budget(monkeypatch):
    from xar.phanny import engine

    today = dt.date.today()
    rows = [{"company_id": f"c{i}", "event_type": "earnings",
             "scheduled_for": today + dt.timedelta(days=10 + i)} for i in range(3)]
    from xar.storage import structured
    monkeypatch.setattr(structured, "upcoming_calendar", lambda *a, **k: rows)
    monkeypatch.setattr(engine, "get_settings", lambda: _S())
    from xar.phanny import book
    monkeypatch.setattr(book, "run_book", lambda cids, **k: {"status": "normal", "n": len(cids)})
    out = engine.judge_due(run_id="phanny-t2")
    assert "deferred_to_next_cycle" not in out
