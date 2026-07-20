"""X 数据源月度总限额(providers/twitter.py 咽喉闸;2026-07-20 裁定 $20/月)。

离线测试:httpx 全程 mock,账本经 monkeypatch 替身 —— 断言闸门语义与记账数学,
不碰真 DB/真 API。"""
from __future__ import annotations

import pytest

from xar.providers import twitter


@pytest.fixture(autouse=True)
def _fresh_warn_state():
    twitter._budget_warned.clear()
    yield
    twitter._budget_warned.clear()


def _summary(usd, cap=20.0):
    return {"month": "2026-07", "usd": usd, "requests": 0, "items": 0, "cap_usd": cap}


def test_budget_gate_blocks_search_without_http(monkeypatch):
    """触顶后 _search 直接返回空,绝不发起 HTTP(fail-closed 同理:账本不可读 usd=None)。"""
    def boom(*a, **k):
        raise AssertionError("HTTP must not be called when budget exhausted")

    monkeypatch.setattr(twitter.httpx, "get", boom)
    monkeypatch.setattr(twitter, "_key", lambda: "k")
    for usd in (20.0, 25.0, None):          # 触顶 / 超顶 / 账本不可读
        monkeypatch.setattr(twitter, "spend_summary", lambda usd=usd: _summary(usd))
        assert twitter._search("q") == []
    # cap<=0 = 数据源禁用
    monkeypatch.setattr(twitter, "spend_summary", lambda: _summary(0.0, cap=0.0))
    assert twitter._search("q") == []


def test_budget_under_cap_allows_and_records(monkeypatch):
    """限额内放行;每页(每 HTTP 请求)记账一次,项数=返回推文数。"""
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(twitter, "spend_summary", lambda: _summary(1.0))
    monkeypatch.setattr(twitter, "_record_spend", lambda req, items: calls.append((req, items)))
    monkeypatch.setattr(twitter, "_key", lambda: "k")
    monkeypatch.setattr(twitter, "_use_tapi", lambda: True)
    monkeypatch.setattr(twitter, "polite", lambda host: None)

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"tweets": [{"id": "1", "text": "hello world", "author": {"userName": "a"},
                                "createdAt": "Wed Jun 17 13:12:32 +0000 2026"}],
                    "has_next_page": False}

    monkeypatch.setattr(twitter.httpx, "get", lambda *a, **k: _Resp())
    out = twitter._search("q", max_results=5)
    assert len(out) == 1 and out[0]["id"] == "1"
    assert calls == [(1, 1)]


def test_spend_math_and_upsert(monkeypatch):
    """估算成本 = items×每千推费率/1000 + requests×每请求费率;UPSERT 到 (provider, month)。"""
    recorded: dict = {}

    def fake_execute(sql, params):
        recorded["sql"] = sql
        recorded["params"] = params

    from xar.storage import db
    monkeypatch.setattr(db, "execute", fake_execute)
    twitter._record_spend(2, 1000)
    prov, month, usd, req, items = recorded["params"]
    assert prov == "twitterapi" and req == 2 and items == 1000
    from xar.config import get_settings
    s = get_settings()
    assert usd == pytest.approx(1000 * s.x_usd_per_1k_tweets / 1000 + 2 * s.x_usd_per_request)
    assert "ON CONFLICT (provider, month)" in recorded["sql"]


def test_budget_warns_once_per_month(monkeypatch, caplog):
    """触顶告警每月一次(1h 节拍防刷屏)。"""
    monkeypatch.setattr(twitter, "spend_summary", lambda: _summary(20.0))
    with caplog.at_level("WARNING"):
        assert twitter.budget_ok() is False
        assert twitter.budget_ok() is False
    warns = [r for r in caplog.records if "budget exhausted" in r.getMessage()]
    assert len(warns) == 1
