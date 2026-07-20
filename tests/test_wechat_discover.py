"""微信「全网发现」混合漏斗(ingestion/wechat_discover + wechat_search + mining/wechat_promote)。

离线测试:httpx / db / save 全程 mock —— 断言查询生成、轮转、URL 去重、短文跳过、
落库 source='wechat'(确保命中现有 triage 门控)、晋升门阈值 + 每日上限。不碰真 DB/真 API。
"""
from __future__ import annotations

import pytest

from xar.ingestion import wechat_discover as wd
from xar.ingestion import wechat_search as ws
from xar.mining import wechat_promote as wp

_HIT = {"url": "https://mp.weixin.qq.com/s/AAA", "title": "旭创 800G 放量",
        "account": "格隆汇", "gh_id": "gh_1", "date": "2026-07-15"}


# ── 查询生成 + 轮转 ──────────────────────────────────────────────────────────
def test_queries_cover_ontology_and_dedupe():
    """查询全集覆盖 公司别名/主题词/路线词,全 CJK,去重保序。"""
    from xar.ontology import cn_routing

    qs = wd._queries()
    assert qs == list(dict.fromkeys(qs))          # 去重保序
    assert all(wd._has_cjk(q) for q in qs)        # 全含中文(纯 ASCII 术语不单独成查询)
    assert "中际旭创" in qs                          # 公司中文别名
    assert "光模块" in qs                            # 主题中文词
    cjk_route_terms = [t for terms in cn_routing.CN_ROUTE_TERMS.values()
                       for t in terms if wd._has_cjk(t)]
    assert any(t in qs for t in cjk_route_terms)   # 至少一个路线中文词


def test_slice_rotation_is_bounded():
    qs = [f"q{i}" for i in range(80)]
    s = wd._slice_for_today(qs, 40)
    assert len(s) == 40 and set(s) <= set(qs)      # 80/40=2 片整除,每片正好 40
    assert wd._slice_for_today(qs, 999) == qs      # per_run>=总数 → 全量
    assert wd._slice_for_today([], 40) == []


# ── discover:去重 / 落库 source=wechat / 短文跳过 ───────────────────────────
def _wire_discover(monkeypatch, *, extract, already=None, save_sink=None):
    monkeypatch.setattr(wd, "available", lambda: True)
    monkeypatch.setattr(wd, "_alias_index", lambda: [])
    monkeypatch.setattr(wd.wechat_search, "search", lambda q, **k: [dict(_HIT)])
    monkeypatch.setattr(wd, "_already_ingested", already or (lambda urls: set()))
    monkeypatch.setattr(wd.news, "_fetch", lambda url: "<html>x</html>")
    monkeypatch.setattr(wd.news, "_extract", extract)
    if save_sink is not None:
        monkeypatch.setattr(wd, "save", lambda doc: save_sink.append(doc) or doc.id)


def test_discover_dedupes_urls_and_saves_wechat(monkeypatch):
    """多查询命中同一 URL → 去重成 1;落库 source='wechat' doc_type='mp_search' via=discover。"""
    saved: list = []
    _wire_discover(monkeypatch, extract=lambda html: ("旭创 800G 放量", "正" * 300),
                   save_sink=saved)
    ids = wd.discover()
    assert len(ids) == 1 and len(saved) == 1
    doc = saved[0]
    assert doc.source == "wechat" and doc.doc_type == "mp_search"
    assert doc.permission == "grey"
    assert doc.meta["via"] == "discover" and doc.meta["gh_id"] == "gh_1"
    assert doc.meta["account"] == "格隆汇" and doc.url == _HIT["url"]


def test_discover_skips_short_text(monkeypatch):
    """正文短于 min_chars(图片/视频号)→ 不落库。"""
    def _no_save(doc):
        pytest.fail("短文不得落库")

    _wire_discover(monkeypatch, extract=lambda html: ("t", "短正文"))
    monkeypatch.setattr(wd, "save", _no_save)
    assert wd.discover() == []


def test_discover_skips_already_ingested(monkeypatch):
    """URL 已在 documents → 不抓正文、不落库(省成本)。"""
    def _no_fetch(url):
        pytest.fail("已抓过的 URL 不得再 fetch")

    _wire_discover(monkeypatch, extract=lambda html: ("t", "正" * 300),
                   already=lambda urls: set(urls))
    monkeypatch.setattr(wd.news, "_fetch", _no_fetch)
    monkeypatch.setattr(wd, "save", lambda doc: pytest.fail("不得落库"))
    assert wd.discover() == []


def test_discover_noop_when_unavailable(monkeypatch):
    """发现未开启 → 整体 no-op,绝不发起搜索。"""
    monkeypatch.setattr(wd, "available", lambda: False)
    monkeypatch.setattr(wd.wechat_search, "search",
                        lambda *a, **k: pytest.fail("未开启不得搜索"))
    assert wd.discover() == []


# ── wechat_search 归一化 ────────────────────────────────────────────────────
def test_search_normalizes_and_filters_non_articles(monkeypatch):
    """多形态返回体归一化;只留 mp.weixin.qq.com 文章链接。"""
    monkeypatch.setattr(ws, "available", lambda: True)
    monkeypatch.setattr(ws, "polite", lambda host: None)

    class _Resp:
        content = b"x"

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"list": [
                {"title": "A", "url": "https://mp.weixin.qq.com/s/X",
                 "nickname": "格隆汇", "biz": "gh_1", "publish_time": "2026-07-01"},
                {"title": "junk", "url": "https://other.com/x"},   # 非文章 → 丢
            ]}}

    monkeypatch.setattr(ws.httpx, "get", lambda *a, **k: _Resp())
    out = ws.search("光模块")
    assert len(out) == 1
    assert out[0]["gh_id"] == "gh_1" and out[0]["account"] == "格隆汇"
    assert out[0]["url"].startswith("https://mp.weixin.qq.com/")


# ── 晋升漏斗:阈值 / 每日上限 / dry-run / 失败计数 ────────────────────────────
def _wire_promote(monkeypatch, cands, subscribed_today=0):
    monkeypatch.setattr(wp, "_sync_candidates", lambda: None)
    registered: list = []
    monkeypatch.setattr(wp.roster, "register", lambda feed_id, **k: registered.append(feed_id))

    def _query(sql, params=None):
        if "ORDER BY keep_rate" in sql:
            return [dict(c) for c in cands]
        return [{"n": subscribed_today}]           # _promoted_today

    monkeypatch.setattr(wp.db, "query", _query)
    return registered


def test_promote_respects_thresholds_and_subscribes(monkeypatch):
    """够格的号按 keep_rate 排序自动订阅 + 登记进策展名册(roster);每次写一次 promoted_at。"""
    cands = [{"gh_id": "gh_a", "name": "A", "articles_seen": 10, "articles_kept": 8, "keep_rate": 0.8},
             {"gh_id": "gh_b", "name": "B", "articles_seen": 5, "articles_kept": 3, "keep_rate": 0.6}]
    registered = _wire_promote(monkeypatch, cands)
    updates: list = []
    monkeypatch.setattr(wp.db, "execute", lambda sql, params=None: updates.append(params))
    subs: list = []
    out = wp.promote_candidates(subscribe_fn=lambda gh, name: subs.append(gh) or f"feed_{gh}")
    assert out["eligible"] == 2 and out["promoted"] == 2 and out["failed"] == 0
    assert subs == ["gh_a", "gh_b"] and len(updates) == 2
    assert registered == ["feed_gh_a", "feed_gh_b"]   # 晋升 → 登记进策展名册


def test_promote_daily_cap(monkeypatch):
    """今天已订阅数逼近上限 → 本轮只订阅剩余名额。"""
    cands = [{"gh_id": f"gh_{i}", "name": str(i), "articles_seen": 4,
              "articles_kept": 4, "keep_rate": 1.0} for i in range(5)]
    _wire_promote(monkeypatch, cands, subscribed_today=4)   # 默认上限 5 → cap_left=1
    monkeypatch.setattr(wp.db, "execute", lambda *a, **k: None)
    out = wp.promote_candidates(subscribe_fn=lambda gh, name: "f")
    assert out["cap_left"] == 1 and out["promoted"] == 1


def test_promote_dry_run_does_not_subscribe(monkeypatch):
    cands = [{"gh_id": "gh_a", "name": "A", "articles_seen": 4, "articles_kept": 4, "keep_rate": 1.0}]
    _wire_promote(monkeypatch, cands)
    out = wp.promote_candidates(dry_run=True,
                                subscribe_fn=lambda *a: pytest.fail("dry-run 不得订阅"))
    assert out["dry_run"] and out["promoted"] == 0


def test_werss_subscribe_rejects_missing_feed_id(monkeypatch):
    """we-mp-rss 返回 200 但 body 无 feed_id/id → 返回 None(不拿 gh_id 冒充,幽灵订阅防护)。"""
    class _S:
        werss_base_url = "http://werss:8001"
        werss_api_token = ""
        http_user_agent = "x"

    monkeypatch.setattr(wp, "get_settings", lambda: _S())

    class _Resp:
        content = b"{}"

        def raise_for_status(self):
            pass

        def json(self):
            return {}                       # 200 但无 feed_id/id

    monkeypatch.setattr(wp.httpx, "post", lambda *a, **k: _Resp())
    assert wp._werss_subscribe("gh_x", "X") is None      # 不落假 feed → 下轮重试

    class _Resp2(_Resp):
        def json(self):
            return {"feed_id": "f_123"}

    monkeypatch.setattr(wp.httpx, "post", lambda *a, **k: _Resp2())
    assert wp._werss_subscribe("gh_x", "X") == "f_123"   # 有 feed_id 才算订阅成功


def test_promote_subscribe_failure_no_update(monkeypatch):
    """订阅失败(返回 None)→ 计 failed,不写 subscribed_at(候选保留,下轮重试)。"""
    cands = [{"gh_id": "gh_a", "name": "A", "articles_seen": 4, "articles_kept": 4, "keep_rate": 1.0}]
    _wire_promote(monkeypatch, cands)
    monkeypatch.setattr(wp.db, "execute", lambda *a, **k: pytest.fail("失败订阅不得写库"))
    out = wp.promote_candidates(subscribe_fn=lambda gh, name: None)
    assert out["failed"] == 1 and out["promoted"] == 0
