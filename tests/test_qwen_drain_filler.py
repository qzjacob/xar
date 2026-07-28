"""GPU 算力分配回归(2026-07-28 用户裁定)。

① 严格头部 **100% 抢占**:alphapai > gangtise > aifinmarket —— 靠前的源有货就吃满整批;
② 尾部(edgar/x/finnhub/…)只分配头部取完后的**剩余产能**,按**信息质量**(实测 kept_rate)成比例切分,
   某源没货时份额自动流给其他源(GPU 不空转)。
"""
from __future__ import annotations

import pytest

from xar.orchestration import qwen_drain as qd
from xar.pipeline_priority import STRICT_PRIORITY_ORDER, tail_weight, tier_order_sql


class _S:
    qwen_drain_exclude_sources = ""
    qwen_drain_batch = 8
    qwen_drain_workers = 4
    qwen_drain_model = "qwen3-14b-local"


@pytest.fixture
def claims(monkeypatch):
    """记录每次 _claim_sql(n, only=…) 并按各源可用量返回。"""
    calls: list = []
    avail: dict = {}

    def fake(n, *, only=None, exclude_sources=None):
        calls.append({"n": n, "only": list(only) if only else None})
        got: list = []
        pool = only if only else [s for s in avail if s not in (exclude_sources or [])]
        for s in pool:
            take = min(avail.get(s, 0), n - len(got))
            avail[s] = avail.get(s, 0) - take
            got += [f"{s}{i}" for i in range(take)]
            if len(got) >= n:
                break
        return got
    monkeypatch.setattr(qd, "_claim_sql", fake)
    monkeypatch.setattr(qd, "get_settings", lambda: _S())
    monkeypatch.setattr(qd, "_tail_sources_pending",
                        lambda: {s: c for s, c in avail.items()
                                 if s not in STRICT_PRIORITY_ORDER and c > 0})
    return calls, avail


# ── ① 严格头部 100% 抢占 ────────────────────────────────────────────────────────
def test_strict_head_order():
    """档位序必须是 alphapai(0) < gangtise(1) < aifinmarket(2) < 尾部(3)。"""
    assert STRICT_PRIORITY_ORDER == ("alphapai", "gangtise", "aifinmarket")
    sql = tier_order_sql("source")
    assert "'alphapai' THEN 0" in sql and "'gangtise' THEN 1" in sql
    assert "'aifinmarket' THEN 2" in sql and "ELSE 3" in sql


def test_head_takes_100_percent_when_available(claims):
    """alphapai 有货 → 整批 8 篇全给它,尾部一篇也拿不到。"""
    calls, avail = claims
    avail.update({"alphapai": 50, "edgar": 999, "finnhub": 999})
    ids = qd._claim(8)
    assert len(ids) == 8 and all(i.startswith("alphapai") for i in ids)
    assert len(calls) == 1, "头部取满后不应再向尾部领取"


def test_gangtise_before_aifinmarket_and_tail(claims):
    """alphapai 空但 gangtise 有货 → 仍 100% 归头部(组内由 tier_order_sql 保证 gangtise 先于 aifin)。"""
    calls, avail = claims
    avail.update({"gangtise": 20, "aifinmarket": 20, "edgar": 999})
    ids = qd._claim(8)
    assert len(ids) == 8 and all(i.startswith("gangtise") for i in ids)
    assert calls[0]["only"] == list(STRICT_PRIORITY_ORDER)


# ── ② 尾部按信息质量比例分配剩余产能 ────────────────────────────────────────────
def test_tail_split_proportional_to_quality(claims):
    """头部无货 → 剩余产能按 kept_rate 权重切分(edgar 6.0 > finnhub 5.9 > x 3.5 > rss 2.3)。"""
    _, avail = claims
    avail.update({"edgar": 999, "finnhub": 999, "x": 999, "rss": 999})
    quota = qd._split_by_quality(100, {"edgar": 999, "finnhub": 999, "x": 999, "rss": 999})
    assert sum(quota.values()) == 100
    assert quota["edgar"] > quota["x"] > quota["rss"], f"未按质量排序: {quota}"
    total_w = sum(tail_weight(s) for s in ("edgar", "finnhub", "x", "rss"))
    assert abs(quota["edgar"] - 100 * tail_weight("edgar") / total_w) <= 2


def test_tail_share_reflows_when_source_empty(claims):
    """某尾部源没货 → 它的份额自动流给其他源,总量不缩水(GPU 不空转)。"""
    quota = qd._split_by_quality(20, {"edgar": 3, "finnhub": 999})
    assert quota["edgar"] == 3, "有多少取多少"
    assert quota["finnhub"] == 17, f"剩余份额应流给 finnhub: {quota}"
    assert sum(quota.values()) == 20


def test_head_partial_then_tail_fills_remainder(claims):
    """头部只有 3 篇 → 其余 5 篇按质量比例分给尾部,整批不浪费。"""
    _, avail = claims
    avail.update({"alphapai": 3, "edgar": 999, "finnhub": 999})
    ids = qd._claim(8)
    assert len([i for i in ids if i.startswith("alphapai")]) == 3
    assert len(ids) == 8, f"剩余产能未被尾部填满: {ids}"
