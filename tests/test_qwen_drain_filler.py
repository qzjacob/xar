"""末位源(x/finnhub 存量)保留填充份额回归(2026-07-28 用户裁定)。

严格优先级在本系统等于"末位源永不执行":tier-1 的 edgar 10 年历史回填持续灌入
(实测 6h 灌 1390/抽 687、才走到 168/1062 家),tier 0/1 永不空 → 31.6 万存量拿不到 GPU。
故给末位源保留一小份;高价值源不足时末位吸收全部剩余产能(GPU 不空转)。
"""
from __future__ import annotations

import pytest

from xar.orchestration import qwen_drain as qd


class _S:
    qwen_drain_exclude_sources = ""
    qwen_drain_batch = 8
    qwen_drain_workers = 4
    qwen_drain_model = "qwen3-14b-local"
    qwen_drain_filler_ratio = 0.25


@pytest.fixture
def cap(monkeypatch):
    """记录每次 _claim_where 的 (n, filler),并可控返回量。"""
    calls: list = []
    avail = {"main": 999, "filler": 999}

    def fake(n, *, filler):
        calls.append({"n": n, "filler": filler})
        k = "filler" if filler else "main"
        got = min(n, avail[k])
        return [f"{k}{i}" for i in range(got)]
    monkeypatch.setattr(qd, "_claim_where", fake)
    monkeypatch.setattr(qd, "get_settings", lambda: _S())
    return calls, avail


def test_filler_gets_reserved_share(cap):
    """高价值源充足时:8 篇里 6 篇给高价值源、2 篇(25%)留给末位存量。"""
    calls, _ = cap
    ids = qd._claim(8)
    assert calls[0] == {"n": 6, "filler": False}, "主取应为 batch - 保留份额"
    assert calls[1] == {"n": 2, "filler": True}, "末位应拿到 25% 保留份额"
    assert len(ids) == 8


def test_filler_absorbs_idle_capacity(cap):
    """高价值源不足时:末位吸收全部剩余产能(GPU 不空转)。"""
    calls, avail = cap
    avail["main"] = 1                      # tier 0/1 只剩 1 篇
    ids = qd._claim(8)
    assert calls[1]["filler"] is True and calls[1]["n"] == 7, "末位应吸收剩余 7 篇"
    assert len(ids) == 8


def test_ratio_zero_is_strict_priority(cap, monkeypatch):
    """ratio=0 → 退回严格优先级:末位仅在高价值源取不满时才被取。"""
    class _Strict(_S):
        qwen_drain_filler_ratio = 0.0
    monkeypatch.setattr(qd, "get_settings", lambda: _Strict())
    calls, _ = cap
    qd._claim(8)
    assert calls[0] == {"n": 8, "filler": False}, "严格模式下主取应拿满整批"
    assert calls[1]["n"] == 0, "高价值源取满时末位不应分到份额"


def test_high_value_sources_keep_majority(cap):
    """优先级次序不变:高价值源始终占多数(默认 75%)。"""
    calls, _ = cap
    qd._claim(8)
    assert calls[0]["n"] > calls[1]["n"], "高价值源份额必须大于末位填充份额"
