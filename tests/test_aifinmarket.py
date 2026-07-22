"""AIfinmarket 多账号订阅池 + 另类研报摘要 sweep 离线测试。

验证:AIFINMARKET{i}_TOKEN 席位池去重、轮询铺满、配额触顶 failover(且普通参数错不冷却席位)、
每席每日上限、sweep 四维落库带 scope + 稳定 doc_id、向后兼容。全部离线(monkeypatch httpx/_mcp_call)。
"""
from __future__ import annotations

import json

from xar.config import get_settings
from xar.providers import aifinmarket


class _Resp:
    def __init__(self, payload: dict):
        self.text = json.dumps(payload, ensure_ascii=False)

    def raise_for_status(self):
        pass


def _make_post(calls: list, responder):
    def post(url, headers=None, json=None, timeout=None):   # noqa: A002 - httpx kw name
        tok = (headers or {}).get("Authorization", "").replace("Bearer ", "")
        calls.append(tok)
        return _Resp(responder(tok))
    return post


def _ok(data: dict) -> dict:
    return {"result": {"content": [{"text": json.dumps(data, ensure_ascii=False)}]}}


def _news(n=1):
    return _ok({"data": {"items": [
        {"title": f"研报{i}", "content": "内容" * 30, "date": "2026-07-20", "relevance": 0.9}
        for i in range(n)]}})


class _FakeS:
    def __init__(self, tokens, cap=0):
        self.aifinmarket_tokens = tokens
        self.aifinmarket_daily_calls_per_account = cap
        self.aifinmarket_base_url = ""
        self.aifinmarket_news_top_k = 5
        self.aifinmarket_min_interval_seconds = 0.0


# ── 席位池派生 ────────────────────────────────────────────────────────────────
def test_token_pool_dedup_and_order(monkeypatch):
    for i in (1, 2, 3):
        monkeypatch.setenv(f"AIFINMARKET{i}_TOKEN", f"tok{i}")
    monkeypatch.setenv("AIFINMARKET4_TOKEN", "")        # 空槽被跳过
    monkeypatch.setenv("AIFINMARKET_TOKEN", "tok1")     # legacy 与席位1同值 → 去重
    get_settings.cache_clear()
    try:
        assert get_settings().aifinmarket_tokens == ["tok1", "tok2", "tok3"]
    finally:
        get_settings.cache_clear()


def test_available_reflects_pool(monkeypatch):
    monkeypatch.setattr(aifinmarket, "_pool", lambda: [])
    assert aifinmarket.available() is False
    monkeypatch.setattr(aifinmarket, "_pool", lambda: ["A"])
    assert aifinmarket.available() is True


# ── 轮询 / failover / 上限 ────────────────────────────────────────────────────
def test_round_robin_spreads_calls(monkeypatch):
    aifinmarket._reset_state()
    monkeypatch.setattr(aifinmarket, "_pool", lambda: ["A", "B", "C"])
    monkeypatch.setattr(aifinmarket, "_throttle", lambda: None)
    calls: list = []
    monkeypatch.setattr(aifinmarket.httpx, "post", _make_post(calls, lambda t: _news(0)))
    for _ in range(6):
        aifinmarket._mcp_call("financial_docs", "get_financial_news", {"query": "x"})
    assert calls == ["A", "B", "C", "A", "B", "C"]      # 均匀铺满每个席位


def test_failover_on_quota_cools_seat(monkeypatch):
    aifinmarket._reset_state()
    monkeypatch.setattr(aifinmarket, "_pool", lambda: ["A", "B", "C"])
    monkeypatch.setattr(aifinmarket, "_throttle", lambda: None)
    calls: list = []

    def responder(tok):
        if tok == "A":
            return {"result": {"isError": True, "content": [{"text": "调用频率超过限额"}]}}
        return _news(1)
    monkeypatch.setattr(aifinmarket.httpx, "post", _make_post(calls, responder))
    out = aifinmarket._mcp_call("financial_docs", "get_financial_news", {"query": "x"})
    assert out is not None                              # A 触顶后 failover 到 B 成功
    assert calls == ["A", "B"]
    assert aifinmarket._tok_id("A") in aifinmarket._cooldown
    calls.clear()
    aifinmarket._mcp_call("financial_docs", "get_financial_news", {"query": "y"})
    assert "A" not in calls                             # 冷却后彻底跳过 A


def test_plain_error_does_not_cool_seat(monkeypatch):
    aifinmarket._reset_state()
    monkeypatch.setattr(aifinmarket, "_pool", lambda: ["A"])
    monkeypatch.setattr(aifinmarket, "_throttle", lambda: None)
    calls: list = []
    monkeypatch.setattr(aifinmarket.httpx, "post", _make_post(
        calls, lambda t: {"result": {"isError": True, "content": [{"text": "无效的请求"}]}}))
    assert aifinmarket._mcp_call("financial_docs", "get_financial_news", {"query": "x"}) is None
    assert aifinmarket._tok_id("A") not in aifinmarket._cooldown    # 参数错不是配额错
    assert calls == ["A"]


def test_daily_cap_per_seat(monkeypatch):
    aifinmarket._reset_state()
    monkeypatch.setattr(aifinmarket, "_pool", lambda: ["A"])
    monkeypatch.setattr(aifinmarket, "_throttle", lambda: None)
    monkeypatch.setattr(aifinmarket, "get_settings", lambda: _FakeS(["A"], cap=2))
    calls: list = []
    monkeypatch.setattr(aifinmarket.httpx, "post", _make_post(calls, lambda t: _news(0)))
    assert aifinmarket._mcp_call("financial_docs", "get_financial_news", {"query": "1"}) is not None
    assert aifinmarket._mcp_call("financial_docs", "get_financial_news", {"query": "2"}) is not None
    calls.clear()
    assert aifinmarket._mcp_call("financial_docs", "get_financial_news", {"query": "3"}) is None
    assert calls == []                                  # 达每日上限 → 不再发起调用


# ── sweep 四维 + scope + doc_id ───────────────────────────────────────────────
def test_research_sweep_scoped_docs(monkeypatch):
    aifinmarket._reset_state()
    monkeypatch.setattr(aifinmarket, "_pool", lambda: ["A", "B"])
    monkeypatch.setattr(aifinmarket, "_persist_usage", lambda u: None)

    def fake_mcp(server, tool, args, timeout=90):
        q = args.get("query", "")
        return {"data": {"items": [
            {"title": f"T-{q[:8]}", "content": "内容" * 40, "date": "2026-07-20", "relevance": 0.8}]}}
    monkeypatch.setattr(aifinmarket, "_mcp_call", fake_mcp)

    import xar.ingestion.base as base
    import xar.ingestion.registry as reg
    import xar.providers.aifin_catalog as cat
    saved: list = []
    monkeypatch.setattr(base, "save", lambda doc: saved.append(doc) or doc.id)
    monkeypatch.setattr(reg, "THEMES", {})              # 主题维置空,专测三定性维
    monkeypatch.setattr(cat, "INDUSTRY_QUERIES", ("半导体 行业",))
    monkeypatch.setattr(cat, "STRATEGY_QUERIES", ("A股 策略",))
    monkeypatch.setattr(cat, "MACRO_QUERIES", ("宏观 政策",))

    out = aifinmarket.pull_research_sweep(company_universe=["innolight"])   # 真 registry CN 名
    assert out["counts"]["company_news"] == 1
    assert out["counts"]["company_ann"] == 1            # innolight 是 CN A 股 → 公告维
    assert out["counts"]["industry"] == 1
    assert out["counts"]["strategy"] == 1
    assert out["counts"]["macro"] == 1
    scopes = {d.meta["scope"] for d in saved}
    assert scopes == {"company", "industry", "strategy", "macro"}
    assert all(d.id.startswith("aifinmarket:") for d in saved)
    assert all(d.source == "aifinmarket" and d.permission == "grey" for d in saved)

    ids_run1 = sorted(d.id for d in saved)
    saved.clear()
    aifinmarket.pull_research_sweep(company_universe=["innolight"])
    assert sorted(d.id for d in saved) == ids_run1      # 稳定 doc_id → 幂等,不裂行


# ── 向后兼容 ──────────────────────────────────────────────────────────────────
def test_backcompat_theme_and_news(monkeypatch):
    aifinmarket._reset_state()
    monkeypatch.setattr(aifinmarket, "_pool", lambda: ["A"])
    calls: list = []
    monkeypatch.setattr(aifinmarket, "_mcp_call",
                        lambda *a, **k: {"data": {"items": []}} or calls.append(1))
    # 不落库(items 空),仅验证仍可调用、走 industry/company scope 分支不抛
    assert aifinmarket.pull_theme_news("半导体 产业链") == 0
    assert aifinmarket.pull_news("innolight") == 0
