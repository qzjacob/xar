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
from . import news, wcda_api, wechat_search, werss_api
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


# ─── 文章级发现(Phase 1 后端 = wechat-download-api)──────────────────────────
# 本体词 → wcda searchbiz 搜全网公众号 → 逐号取最近文章 → 逐篇解析全文 → save(source='wechat')
# → 现有 triage。无需订阅(wcda 按 fakeid 直取),URL 去重避免重复解析(解析最贵)。


# 赛马实证(2026-07-21,scripts/wechat_discover_race.py):高信噪查询 = **具体资产/技术词**。
#   broad-tech 70% keep / overseas-主题词 53% / overseas-公司名 33%(公司名撞中国同名号:
#   博通→博通集成、英伟达→NVIDIA 营销号)/ 消费泛词 ~0%。据此收敛查询集:只留 tech 主题
#   (剔除 internet/retail/restaurants 泛词)+ 技术路线词 + 海外资产主题词;剔除消费泛词。
#   **公司名剔除规则限定为「歧义/海外」名**(如博通/美满/英伟达 —— 撞同名号或落营销号);
#   国内**无歧义龙头供应链名**(长江存储/长鑫存储 = YMTC/CXMT,搜到的就是目标标的号)例外保留。
_TECH_THEMES = ("ai_optical", "ai_chip", "ai_software", "space_exploration", "humanoid_robotics")
_OVERSEAS_ASSET_TERMS = ("美股AI", "美股科技", "美股财报", "纳斯达克", "英伟达产业链",
                         "海外算力", "北美算力", "超大规模数据中心", "算力集群", "算力租赁",
                         "云计算龙头", "大厂capex", "AI服务器", "液冷服务器", "推理芯片",
                         # 存储(用户点名的海外热门资产);长江存储/长鑫存储=国内龙头,无歧义,
                         # 豁免上文「歧义/海外公司名剔除」规则(它们即目标标的号)
                         "AI存储", "存储芯片", "存储涨价", "闪存", "固态硬盘", "存储模组",
                         "长江存储", "长鑫存储",
                         # 光(用户点名)
                         "光通信设备", "相干光通信")


def _precise_queries() -> list[str]:
    """默认发现查询(赛马收敛版):tech 主题(剔消费泛词)+ 技术路线词 + 海外资产主题词。
    实证:具体技术/资产词(光模块/HBM/AI存储/美股AI)稳命中垂直投研号(keep 53–70%);已剔除
    消费泛词(游戏/客单价/门店,~0 keep)与**歧义/海外**公司名(英伟达→营销号、博通→博通集成,
    33%);国内无歧义龙头名(长江存储/长鑫存储)在 _OVERSEAS_ASSET_TERMS 里例外保留。"""
    seen: set[str] = set()
    out: list[str] = []

    def _add(t: str) -> None:
        t = (t or "").strip()
        if t and _has_cjk(t) and t not in seen:   # 纯 ASCII 术语(800G/HBM)由中文主题词携带
            seen.add(t)
            out.append(t)

    for theme in _TECH_THEMES:                    # tech 主题词(不含 internet/retail/restaurants)
        for t in cn_routing.CN_THEME_TERMS.get(theme, ()):
            _add(t)
    for terms in cn_routing.CN_ROUTE_TERMS.values():   # 全部技术路线词(最具体)
        for t in terms:
            _add(t)
    for t in _OVERSEAS_ASSET_TERMS:               # 海外资产主题词(赛马胜出)
        _add(t)
    return out


def _overseas_queries() -> list[str]:
    """美股/海外资产聚焦策略(赛马胜出的收敛版):海外资产主题词 + 少数 iconic 无歧义 US 名。
    实证:主题词(美股AI/AI存储/英伟达产业链)命中跨境投研号(美股AI投研速递/AI存储数据报,
    53% keep,「三星预测存储荒到2028」「十万卡AI超集群」);US 公司名多撞中国同名号(33%),
    故只留最硬的几个(英伟达/台积电/美光/阿斯麦)。"""
    seen: set[str] = set()
    out: list[str] = []

    def _add(t: str) -> None:
        t = (t or "").strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)

    for t in _OVERSEAS_ASSET_TERMS:               # 海外资产主题词(赛马胜出的核心)
        _add(t)
    for t in ("英伟达", "台积电", "美光", "阿斯麦", "美股", "HBM", "高带宽内存"):
        _add(t)                                   # 少数 iconic 无歧义标的/资产词
    return out


def wcda_available() -> bool:
    """wcda 文章级发现:发现已开启 且 wcda 后端已配置(WCDA_BASE_URL)。"""
    return bool(get_settings().wechat_discover_enabled) and wcda_api.available()


def discover_via_wcda(limit: int | None = None, *, queries: list[str] | None = None,
                      strategy: str = "broad") -> list[str]:
    """跑一轮 wcda 文章级发现。返回落库文档 id。默认关/无后端 → 空。
    queries 显式传入则用之(赛马用),否则默认 broad(_precise_queries 轮转切片);
    strategy 打进 meta.strategy 供赛马按策略对比 keep_rate。"""
    if not (get_settings().wechat_discover_enabled and wcda_api.available()):
        return []
    from datetime import datetime, timezone

    s = get_settings()
    max_articles = limit or s.wechat_discover_max_articles
    if queries is None:
        queries = _slice_for_today(_precise_queries(), s.wechat_discover_queries_per_run)
    aliases = _alias_index()

    # 1) 搜号 → 收集候选账号(按 fakeid 去重),记发现它的 query(供进化引擎按查询算命中率)
    accounts: dict[str, dict] = {}
    for q in queries:
        for acct in wcda_api.search_accounts(q, limit=s.wcda_accounts_per_query):
            if acct["fakeid"] not in accounts:
                acct["_query"] = q
                accounts[acct["fakeid"]] = acct
        if len(accounts) >= s.wcda_accounts_per_run:
            break

    # human-in-the-loop 门控:加载审核态。blocked 永不抓;严格门控(wechat_hitl_gate)只抓 approved,
    # 新号(pending)只记入审核队列不抓,等运营方批准。
    reviewed = {r["gh_id"]: r["review_status"] for r in
                db.query("SELECT gh_id, review_status FROM wechat_discovered")}
    strict = bool(s.wechat_hitl_gate)

    ids: list[str] = []
    for fakeid, acct in list(accounts.items())[:s.wcda_accounts_per_run]:
        _record_discovered_account(fakeid, acct["name"], None)   # 记录(供审核队列,含 pending 新号)
        rv = reviewed.get(fakeid, "pending")
        if rv == "blocked" or (strict and rv != "approved"):
            continue                                             # 门控:不抓(严格模式下等人工批准)
        arts = wcda_api.list_articles(fakeid, limit=s.wcda_articles_per_account)
        seen = _already_ingested([a["url"] for a in arts])   # 已抓过的不再解析(解析最贵)
        for a in arts:
            if len(ids) >= max_articles:
                break
            if a["url"] in seen:
                continue
            parsed = wcda_api.parse_article(a["url"])
            if not parsed or len(parsed["text"]) < s.wechat_discover_min_chars:
                continue
            title = parsed["title"] or a["title"]
            body = f"{title}\n\n{parsed['text']}".strip()
            pub = None
            if parsed.get("publish_time"):
                try:
                    pub = datetime.fromtimestamp(int(parsed["publish_time"]), tz=timezone.utc)
                except Exception:  # noqa: BLE001
                    pub = None
            doc = Doc(
                company_id=_link_company(f"{title}\n{parsed['text']}", aliases, None),
                source="wechat", doc_type="mp_search", title=title or "微信公众号文章",
                text=body[:120_000], url=a["url"], published_at=pub, permission="grey",
                license_tag="wechat-extracted-facts-self-use",
                meta={"platform": "wechat_mp", "via": "discover", "backend": "wcda",
                      "strategy": strategy, "account": acct["name"], "gh_id": fakeid,
                      "query": acct.get("_query", "")},   # 溯源到发现它的查询词(进化裁判)
            )
            ids.append(save(doc))
        if len(ids) >= max_articles:
            break
    log.info("wcda discover [%s]: %d 候选号 → %d 篇入库(source=wechat)", strategy, len(accounts), len(ids))
    return ids
