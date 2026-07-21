"""微信「全网发现」混合漏斗(ingestion/wechat_discover + wechat_search + mining/wechat_promote)。

离线测试:httpx / db / save 全程 mock —— 断言查询生成、轮转、URL 去重、短文跳过、
落库 source='wechat'(确保命中现有 triage 门控)、晋升门阈值 + 每日上限。不碰真 DB/真 API。
"""
from __future__ import annotations

import pytest

from xar.ingestion import wcda_api as wc
from xar.ingestion import wechat_discover as wd
from xar.ingestion import wechat_search as ws
from xar.ingestion import werss_api as wa
from xar.mining import wechat_promote as wp


class _WerssS:
    """we-mp-rss API 测试用 settings 替身(AK/SK 已配)。"""
    werss_base_url = "http://werss:8001"
    werss_ak = "ak1"
    werss_sk = "sk1"
    werss_api_token = ""
    http_user_agent = "x"

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


# ── 账号级发现:we-mp-rss search_Biz → subscribe → roster → prune ─────────────
def test_werss_search_accounts_normalizes(monkeypatch):
    """搜索全网公众号:归一化 {fakeid,name,avatar,intro};无 fakeid/name 的丢弃。"""
    monkeypatch.setattr(wa, "get_settings", lambda: _WerssS())
    monkeypatch.setattr(wa, "polite", lambda h: None)

    class _R:
        content = b"x"

        def raise_for_status(self):
            pass

        def json(self):
            return {"code": 0, "data": {"list": [
                {"fakeid": "MzA1", "nickname": "格隆汇", "round_head_img": "http://a", "signature": "财经"},
                {"fakeid": "", "nickname": "空号"},          # 无 fakeid → 丢
            ], "total": 1}}

    cap: dict = {}
    monkeypatch.setattr(wa.httpx, "get", lambda url, **k: cap.update(url=url) or _R())
    out = wa.search_accounts("光模块")
    assert "/api/v1/wx/mps/search/" in cap["url"]      # 搜索路由在 /mps 下(回归:曾漏 /mps)
    assert len(out) == 1
    assert out[0]["fakeid"] == "MzA1" and out[0]["name"] == "格隆汇" and out[0]["avatar"] == "http://a"


def test_werss_subscribe_base64_mp_id(monkeypatch):
    """订阅:mp_id = base64(fakeid);返回 feed id;缺 fakeid 直接 None。"""
    import base64

    monkeypatch.setattr(wa, "get_settings", lambda: _WerssS())
    monkeypatch.setattr(wa, "polite", lambda h: None)
    cap: dict = {}

    class _R:
        content = b"{}"

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"id": "MP_WXS_123"}}

    monkeypatch.setattr(wa.httpx, "post", lambda url, json=None, **k: cap.update(json=json) or _R())
    fid = wa.subscribe({"fakeid": "123", "name": "号", "avatar": "", "intro": ""})
    assert fid == "MP_WXS_123"
    assert cap["json"]["mp_id"] == base64.b64encode(b"123").decode()
    assert cap["json"]["mp_name"] == "号"
    assert wa.subscribe({"fakeid": "", "name": "x"}) is None      # 缺 fakeid → None


def _wire_account_discover(monkeypatch, accts, existing=None, cap=5):
    monkeypatch.setattr(wd, "accounts_available", lambda: True)
    monkeypatch.setattr(wd.werss_api, "search_accounts", lambda q, **k: [dict(a) for a in accts])
    monkeypatch.setattr(wd, "_existing_feed_ids", lambda: set(existing or []))
    monkeypatch.setattr(wd.werss_api, "subscribe", lambda a: f"MP_WXS_{a['fakeid']}")
    monkeypatch.setattr(wd, "_record_discovered_account", lambda *a: None)
    from xar.mining import roster
    reg: list = []
    monkeypatch.setattr(roster, "register", lambda fid, **k: reg.append(fid))
    return reg


def test_discover_accounts_dedups_and_subscribes(monkeypatch):
    """多查询同一批号 → 去重;已订阅的号(existing)跳过;新号订阅 + roster.register。"""
    accts = [{"fakeid": "AAA", "name": "A", "avatar": "", "intro": ""},
             {"fakeid": "BBB", "name": "B", "avatar": "", "intro": ""}]
    reg = _wire_account_discover(monkeypatch, accts, existing={"MP_WXS_AAA"})
    r = wd.discover_accounts(limit=5)
    assert r["subscribed"] == 1 and reg == ["MP_WXS_BBB"]   # AAA 已订阅 → 只订 BBB


def test_discover_accounts_respects_cap(monkeypatch):
    accts = [{"fakeid": f"F{i}", "name": str(i), "avatar": "", "intro": ""} for i in range(10)]
    reg = _wire_account_discover(monkeypatch, accts)
    r = wd.discover_accounts(limit=3)
    assert r["subscribed"] == 3 and len(reg) == 3          # 每日上限 3


def test_discover_accounts_noop_when_unavailable(monkeypatch):
    monkeypatch.setattr(wd, "accounts_available", lambda: False)
    monkeypatch.setattr(wd.werss_api, "search_accounts",
                        lambda *a, **k: pytest.fail("未开启不得搜索"))
    assert wd.discover_accounts().get("skipped")


def test_prune_accounts_deactivates_low_keeprate(monkeypatch):
    """发现订阅号:样本足(seen≥8)且 keep_rate<0.15 → 停用;样本不足或高信噪 → 留。"""
    rows = [{"name": "X", "feed_id": "MP_WXS_X", "seen": 10, "kept": 0},    # 0.0 → prune
            {"name": "Y", "feed_id": "MP_WXS_Y", "seen": 3, "kept": 0},     # 样本不足 → 留
            {"name": "Z", "feed_id": "MP_WXS_Z", "seen": 10, "kept": 8}]    # 0.8 → 留
    monkeypatch.setattr(wp.db, "query", lambda sql, params=None: rows)
    deact: list = []
    monkeypatch.setattr(wp.roster, "deactivate", lambda fid: deact.append(fid))
    out = wp.prune_accounts()
    assert out["pruned"] == 1 and deact == ["MP_WXS_X"]


def test_prune_accounts_dry_run(monkeypatch):
    rows = [{"name": "X", "feed_id": "MP_WXS_X", "seen": 10, "kept": 0}]
    monkeypatch.setattr(wp.db, "query", lambda sql, params=None: rows)
    monkeypatch.setattr(wp.roster, "deactivate", lambda fid: pytest.fail("dry-run 不得停用"))
    out = wp.prune_accounts(dry_run=True)
    assert out["dry_run"] and out["pruned"] == 1


# ── 文章级发现:wechat-download-api (wcda) ────────────────────────────────────
class _WcdaS:
    wcda_base_url = "http://wcda:5000"
    http_user_agent = "x"


def test_wcda_search_accounts_normalizes(monkeypatch):
    monkeypatch.setattr(wc, "get_settings", lambda: _WcdaS())
    monkeypatch.setattr(wc, "polite", lambda h: None)

    class _R:
        def raise_for_status(self): pass

        def json(self):
            return {"success": True, "data": {"list": [
                {"fakeid": "F1", "nickname": "光通信女人", "alias": "nini"},
                {"fakeid": "", "nickname": "空号"},           # 无 fakeid → 丢
            ]}}

    monkeypatch.setattr(wc.httpx, "get", lambda *a, **k: _R())
    out = wc.search_accounts("光模块")
    assert len(out) == 1 and out[0]["fakeid"] == "F1" and out[0]["name"] == "光通信女人"


def test_wcda_list_articles_slices_to_limit(monkeypatch):
    monkeypatch.setattr(wc, "get_settings", lambda: _WcdaS())
    monkeypatch.setattr(wc, "polite", lambda h: None)

    class _R:
        def raise_for_status(self): pass

        def json(self):
            return {"success": True, "data": {"articles": [
                {"title": f"t{i}", "link": f"https://mp.weixin.qq.com/s/{i}"} for i in range(10)]}}

    monkeypatch.setattr(wc.httpx, "get", lambda *a, **k: _R())
    out = wc.list_articles("F1", limit=3)      # 后端返回 10,代码截到 3
    assert len(out) == 3 and out[0]["url"].startswith("https://mp.weixin.qq.com/")


def test_wcda_parse_article_plain_and_html_fallback(monkeypatch):
    monkeypatch.setattr(wc, "get_settings", lambda: _WcdaS())
    monkeypatch.setattr(wc, "polite", lambda h: None)

    class _R:
        def raise_for_status(self): pass

        def json(self):
            return {"success": True, "data": {"title": "T", "plain_content": "正文内容",
                                              "author": "号", "publish_time": 1784504293}}

    monkeypatch.setattr(wc.httpx, "post", lambda *a, **k: _R())
    p = wc.parse_article("https://mp.weixin.qq.com/s/X")
    assert p["title"] == "T" and p["text"] == "正文内容" and p["author"] == "号"

    class _R2(_R):
        def json(self):
            return {"success": True, "data": {"title": "T", "content": "<p>hello world</p>"}}

    monkeypatch.setattr(wc.httpx, "post", lambda *a, **k: _R2())
    assert "hello world" in wc.parse_article("https://mp.weixin.qq.com/s/Y")["text"]  # HTML 去标签兜底


class _WcdaDiscoverS:
    wechat_discover_enabled = True
    wechat_discover_queries_per_run = 40
    wechat_discover_max_articles = 200
    wechat_discover_min_chars = 5
    wcda_accounts_per_query = 6
    wcda_accounts_per_run = 12
    wcda_articles_per_account = 6


def _wire_wcda_discover(monkeypatch):
    monkeypatch.setattr(wd, "get_settings", lambda: _WcdaDiscoverS())
    monkeypatch.setattr(wd.wcda_api, "available", lambda: True)
    monkeypatch.setattr(wd, "_alias_index", lambda: [])
    monkeypatch.setattr(wd, "_record_discovered_account", lambda *a: None)
    monkeypatch.setattr(wd.wcda_api, "search_accounts",
                        lambda q, **k: [{"fakeid": "F1", "name": "光通信女人", "alias": ""}])


def test_discover_via_wcda_saves_wechat_docs(monkeypatch):
    """搜号→取文→解析→落库 source='wechat' doc_type='mp_search' backend='wcda'。"""
    _wire_wcda_discover(monkeypatch)
    monkeypatch.setattr(wd, "_already_ingested", lambda urls: set())
    monkeypatch.setattr(wd.wcda_api, "list_articles",
                        lambda fid, **k: [{"url": "https://mp.weixin.qq.com/s/A",
                                           "title": "旭创800G", "update_time": 1784504267}])
    monkeypatch.setattr(wd.wcda_api, "parse_article",
                        lambda url: {"title": "旭创800G放量", "text": "正文" * 50,
                                     "author": "光通信女人", "publish_time": 1784504293})
    saved: list = []
    monkeypatch.setattr(wd, "save", lambda doc: saved.append(doc) or doc.id)
    ids = wd.discover_via_wcda()
    assert len(ids) == 1 and len(saved) == 1
    doc = saved[0]
    assert doc.source == "wechat" and doc.doc_type == "mp_search"
    assert doc.meta["backend"] == "wcda" and doc.meta["gh_id"] == "F1" and doc.meta["via"] == "discover"
    assert doc.url == "https://mp.weixin.qq.com/s/A" and doc.published_at is not None


def test_discover_via_wcda_skips_seen_url(monkeypatch):
    """URL 已在库 → 不解析(解析最贵)、不落库。"""
    _wire_wcda_discover(monkeypatch)
    monkeypatch.setattr(wd.wcda_api, "list_articles",
                        lambda fid, **k: [{"url": "https://mp.weixin.qq.com/s/A", "title": "x"}])
    monkeypatch.setattr(wd, "_already_ingested", lambda urls: set(urls))
    monkeypatch.setattr(wd.wcda_api, "parse_article", lambda url: pytest.fail("已抓过不得解析"))
    monkeypatch.setattr(wd, "save", lambda doc: pytest.fail("不得落库"))
    assert wd.discover_via_wcda() == []


def test_precise_queries_excludes_ambiguous_company_aliases():
    """wcda 账号搜索查询 = 主题+路线中文词;剔除短公司别名(避免 searchbiz 命中无关号)。"""
    q = wd._precise_queries()
    assert "光模块" in q and "人形机器人" in q and "硅光" in q     # 主题/路线词在
    assert "华通" not in q and "旭创" not in q and "联华" not in q  # 歧义短别名不在
    assert all(wd._has_cjk(x) for x in q)                        # 全 CJK
    assert q == list(dict.fromkeys(q))                          # 去重保序


def test_overseas_queries_are_us_focused():
    """海外策略查询 = US 公司中文名 + 海外资产主题词;去重。"""
    q = wd._overseas_queries()
    assert "英伟达" in q and "美光" in q and "台积电" in q     # US 公司中文名
    assert "AI存储" in q and "HBM" in q and "美股AI" in q     # 海外资产主题词
    assert len(q) >= 20 and q == list(dict.fromkeys(q))


def test_discover_via_wcda_uses_explicit_queries_and_strategy_tag(monkeypatch):
    """赛马:显式 queries 被采用,strategy 打进 meta.strategy。"""
    monkeypatch.setattr(wd, "get_settings", lambda: _WcdaDiscoverS())
    monkeypatch.setattr(wd.wcda_api, "available", lambda: True)
    monkeypatch.setattr(wd, "_alias_index", lambda: [])
    monkeypatch.setattr(wd, "_record_discovered_account", lambda *a: None)
    monkeypatch.setattr(wd, "_already_ingested", lambda urls: set())
    used: list = []
    monkeypatch.setattr(wd.wcda_api, "search_accounts",
                        lambda q, **k: used.append(q) or [{"fakeid": "F1", "name": "x", "alias": ""}])
    monkeypatch.setattr(wd.wcda_api, "list_articles",
                        lambda fid, **k: [{"url": "https://mp.weixin.qq.com/s/Z", "title": "t"}])
    monkeypatch.setattr(wd.wcda_api, "parse_article",
                        lambda url: {"title": "t", "text": "正" * 50, "author": "x", "publish_time": 1})
    saved: list = []
    monkeypatch.setattr(wd, "save", lambda doc: saved.append(doc) or doc.id)
    wd.discover_via_wcda(queries=["英伟达", "美光"], strategy="overseas_race")
    assert used[:2] == ["英伟达", "美光"]                    # 用了显式 queries(非默认 broad)
    assert saved and saved[0].meta["strategy"] == "overseas_race"
