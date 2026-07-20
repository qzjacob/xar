"""微信公众号「全网发现」连接器 —— 混合漏斗的发现段。

现状(ingestion/wechat.py)只轮询**手动订阅**的号,你没订阅的高价值号永远进不来。
本模块补上「全网发现」:用**本体种子词**(公司中文别名 + 主题/路线关键词)驱动一个
自托管搜索服务(wechat_search),把 mp.weixin.qq.com 文章逐篇抓正文,落成和订阅文章
**同一条路**的 Doc(source='wechat')—— 于是自动进现有 triage 去噪 → kg 抽取,零改动。

    ① 查询生成(本体种子, 游标轮转)
      → ② wechat_search.search()  (全网, 自托管服务)
      → ③ 去重 vs documents + 抓正文(复用 news._fetch/_extract)+ save(source=wechat)
      → [现有 triage 去噪 → kg_extract] → ④ 高产号晋升订阅(mining/wechat_promote)

查询用**按天轮转**切片(无状态、确定性):每日 daily 跑一片,ceil(总数/每轮)天覆盖全集。
default 关闭(wechat_discover_enabled=False);搜索服务未配置 → 整体 no-op。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ..config import get_settings
from ..logging import get_logger
from ..ontology import cn_routing
from ..storage import db
from . import news, wechat_search, werss_api
from .base import Doc, save
from .registry import COMPANIES
from .wechat import _alias_index, _link_company, _parse_date

log = get_logger("xar.ingest.wechat_discover")

_CJK = re.compile(r"[一-鿿]")


def available() -> bool:
    """发现已开启且搜索服务已配置?否则整体 no-op(默认关,turnkey-safe)。"""
    return bool(get_settings().wechat_discover_enabled) and wechat_search.available()


def _has_cjk(s: str) -> bool:
    return bool(_CJK.search(s or ""))


def _queries() -> list[str]:
    """本体驱动的查询全集(确定性顺序,精度从高到低):
    公司中文别名 → 技术路线中文词 → 主题中文词。去重保序。"""
    seen: set[str] = set()
    out: list[str] = []

    def _add(term: str) -> None:
        t = (term or "").strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)

    # 1) 公司中文别名(最精确 —— 点名个股)
    for c in COMPANIES:
        for a in [c.get("name", ""), *c.get("aliases", [])]:
            if _has_cjk(a):
                _add(a)
    # 2) 技术路线中文词(800G / CPO / HBM …,精确到技术节点)
    for terms in cn_routing.CN_ROUTE_TERMS.values():
        for t in terms:
            if _has_cjk(t):        # 纯 ASCII(如 "800G")留给别名/主题携带,避免搜索噪音
                _add(t)
    # 3) 主题中文词(较宽,兜底覆盖)
    for terms in cn_routing.CN_THEME_TERMS.values():
        for t in terms:
            if _has_cjk(t):
                _add(t)
    return out


def _slice_for_today(queries: list[str], per_run: int) -> list[str]:
    """按 UTC 日序轮转取一片(无状态、确定性)。per_run<=0 或空 → 全量。"""
    if not queries or per_run <= 0 or per_run >= len(queries):
        return queries
    n_slices = (len(queries) + per_run - 1) // per_run
    idx = datetime.now(timezone.utc).toordinal() % n_slices
    start = idx * per_run
    return queries[start:start + per_run]


def _already_ingested(urls: list[str]) -> set[str]:
    """已在 documents 里的 URL(跳过重复抓取,省成本)。"""
    if not urls:
        return set()
    rows = db.query("SELECT url FROM documents WHERE url = ANY(%s)", (urls,))
    return {r["url"] for r in rows if r.get("url")}


def _ingest_url(hit: dict, aliases) -> str | None:
    """抓一篇 mp.weixin.qq.com 文章正文 → save(source='wechat')。太短/抓不到 → None。"""
    s = get_settings()
    url = hit["url"]
    html = news._fetch(url)
    if not html:
        return None
    title, text = news._extract(html)
    title = (title or hit.get("title") or "").strip()
    text = (text or "").strip()
    if len(text) < s.wechat_discover_min_chars:   # 图片/视频号 → 跳过(triage 也会地板掉)
        return None
    body = f"{title}\n\n{text}".strip()
    company_id = _link_company(f"{title}\n{text}", aliases, None)
    doc = Doc(
        company_id=company_id, source="wechat", doc_type="mp_search",
        title=title or "微信公众号文章", text=body[:120_000], url=url,
        published_at=_parse_date(hit.get("date")), permission="grey",
        license_tag="wechat-extracted-facts-self-use",
        meta={"platform": "wechat_mp", "via": "discover",
              "account": hit.get("account") or "", "gh_id": hit.get("gh_id") or "",
              "query": hit.get("_query") or ""},
    )
    return save(doc)


def discover(limit: int | None = None) -> list[str]:
    """跑一轮全网发现。返回落库的文档 id。search 失败/去重后无新文 → 空列表(不炸)。"""
    if not available():
        return []
    s = get_settings()
    max_articles = limit or s.wechat_discover_max_articles
    queries = _slice_for_today(_queries(), s.wechat_discover_queries_per_run)

    # ② 搜索 → 汇总候选(按 URL 去重,记录首个命中它的 query 供溯源)
    candidates: dict[str, dict] = {}
    for q in queries:
        for hit in wechat_search.search(q, since_days=s.wechat_discover_lookback_days):
            u = hit["url"]
            if u not in candidates:
                hit["_query"] = q
                candidates[u] = hit
            if len(candidates) >= max_articles:
                break
        if len(candidates) >= max_articles:
            break

    # ③ 去掉已抓过的 → 抓正文 + 落库
    fresh = [u for u in candidates if u not in _already_ingested(list(candidates))]
    aliases = _alias_index()
    ids: list[str] = []
    for u in fresh[:max_articles]:
        try:
            did = _ingest_url(candidates[u], aliases)
        except Exception as e:  # noqa: BLE001
            log.warning("wechat discover ingest %s failed: %s", u, str(e)[:160])
            continue
        if did:
            ids.append(did)
    log.info("wechat discover: %d queries → %d candidates → %d fresh → %d ingested",
             len(queries), len(candidates), len(fresh), len(ids))
    return ids


# ─── 账号级发现(Phase 1 后端=we-mp-rss search_Biz)────────────────────────────
# 本体词 → 搜全网公众号 → 去重 vs 名册 → 有界自动订阅 → roster.register → 现有逐号轮询 + triage。
# 与文章级 discover() 并存:配了 WECHAT_SEARCH_BASE_URL 走文章级,配了 we-mp-rss AK/SK 走账号级。


def accounts_available() -> bool:
    """账号级发现:发现已开启 且 we-mp-rss 管理 API 可鉴权(AK/SK 或 token)。"""
    return bool(get_settings().wechat_discover_enabled) and werss_api.available()


def _existing_feed_ids() -> set[str]:
    """已订阅(roster active)或已发现过的 feed_id —— 避免重复订阅。"""
    from ..mining import roster

    ids = {f["feed_id"] for f in roster.active_feeds()}
    rows = db.query("SELECT feed_id FROM wechat_discovered WHERE feed_id IS NOT NULL")
    ids |= {r["feed_id"] for r in rows if r.get("feed_id")}
    return ids


def _record_discovered_account(fakeid: str, name: str, feed_id: str) -> None:
    db.execute(
        "INSERT INTO wechat_discovered (gh_id, name, promoted_at, feed_id) "
        "VALUES (%s,%s,now(),%s) ON CONFLICT (gh_id) DO UPDATE SET "
        "name=EXCLUDED.name, promoted_at=now(), feed_id=EXCLUDED.feed_id, updated_at=now()",
        (fakeid, name, feed_id))


def discover_accounts(limit: int | None = None) -> dict:
    """跑一轮账号级发现:本体词搜公众号 → 有界订阅新号。返回统计。默认关/无凭据 → skip。"""
    if not accounts_available():
        return {"skipped": "account discovery unavailable (未开启 或 we-mp-rss 无 AK/SK)"}
    s = get_settings()
    cap = limit or s.wechat_promote_max_per_day        # 每轮新订阅上限(防打爆会话限流)
    existing = _existing_feed_ids()
    queries = _slice_for_today(_queries(), s.wechat_discover_queries_per_run)
    seen: set[str] = set()
    subscribed: list[dict] = []
    for q in queries:
        if len(subscribed) >= cap:
            break
        for acct in werss_api.search_accounts(q, limit=10):
            fakeid = acct["fakeid"]
            feed_guess = f"MP_WXS_{fakeid}"
            if fakeid in seen or feed_guess in existing:
                continue
            seen.add(fakeid)
            feed_id = werss_api.subscribe(acct)        # 订阅(会话过期→None,跳过)
            if not feed_id:
                continue
            from ..mining import roster

            roster.register(feed_id, name=acct["name"], tier=2)   # 落策展名册 → 逐号轮询接管
            _record_discovered_account(fakeid, acct["name"], feed_id)
            existing.add(feed_id)
            existing.add(feed_guess)
            subscribed.append({"feed_id": feed_id, "name": acct["name"], "query": q})
            if len(subscribed) >= cap:
                break
    log.info("wechat account discover: %d queries → %d 新订阅号", len(queries), len(subscribed))
    return {"queries": len(queries), "subscribed": len(subscribed), "accounts": subscribed}
