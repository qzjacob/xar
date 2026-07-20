"""微信公众号「全网」搜索客户端 —— 后端无关的薄适配层。

XAR 保持薄连接器哲学:真正的搜索/反爬留在一个自托管的搜索服务里(we-mp-rss 内置
关键词搜索 / tmwgsicp/wechat-download-api 代理池反风控 / weixin_search_mcp 等),本模块
只把一个关键词发给它的 HTTP 接口,并把五花八门的返回体**归一化**成:

    {title, url, account, gh_id, date, snippet}

只保留 mp.weixin.qq.com 的文章永久链接(其余卡片/广告丢弃)。任何网络/解析错误都
WARN 后返回 [](绝不炸调用方 —— 搜索是脆弱的一环,失败 = 本轮少发现几篇,不是崩溃)。

后端契约在构建期做一次 spike 定型:换后端只改 `_endpoint()` / `_normalize()` 这一层,
`search()` 的对外签名不变(镜像 wechat.py 对 JSON Feed 多形态的宽容解析)。
"""
from __future__ import annotations

from urllib.parse import urlparse

import httpx

from ..config import get_settings
from ..logging import get_logger
from .base import polite

log = get_logger("xar.ingest.wechat_search")

_MP_HOST = "mp.weixin.qq.com"


def available() -> bool:
    """搜索服务已配置?未配置 → 发现连接器整体 no-op(turnkey-safe)。"""
    return bool(get_settings().wechat_search_base_url.strip())


def _endpoint(base: str) -> str:
    """搜索端点。后端 spike 定型后如路径不同,只改这里一处。"""
    return base.rstrip("/") + "/api/search"


def _is_article_url(url: str) -> bool:
    try:
        return urlparse(url).netloc.endswith(_MP_HOST)
    except Exception:  # noqa: BLE001
        return False


def _items(payload) -> list[dict]:
    """从多形态返回体里取条目列表(镜像 wechat._items_from_json 的宽容)。"""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("items", "results", "list", "articles", "data"):
            v = payload.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):  # {data: {list: [...]}}
                inner = v.get("list") or v.get("items") or v.get("articles")
                if isinstance(inner, list):
                    return [x for x in inner if isinstance(x, dict)]
    return []


def _first(it: dict, *keys: str) -> str:
    for k in keys:
        v = it.get(k)
        if v:
            return str(v).strip()
    return ""


def _normalize(it: dict) -> dict | None:
    """一条搜索结果 → 归一化 dict;非文章链接 → None。"""
    url = _first(it, "url", "link", "content_url", "id")
    if not url or not _is_article_url(url):
        return None
    return {
        "title": _first(it, "title", "name"),
        "url": url,
        "account": _first(it, "account", "nickname", "author", "source", "mp_name"),
        "gh_id": _first(it, "gh_id", "biz", "fakeid", "user_name", "ghid"),
        "date": _first(it, "date", "publish_time", "datetime", "pubDate", "date_published"),
        "snippet": _first(it, "snippet", "digest", "summary", "description"),
    }


def search(query: str, *, since_days: int | None = None, limit: int | None = None) -> list[dict]:
    """把 `query` 发给搜索服务,返回归一化的文章结果列表(可能为空)。"""
    s = get_settings()
    if not available() or not query.strip():
        return []
    params: dict = {"q": query, "keyword": query}   # 两个常见键名都带上,后端各取所需
    if since_days:
        params["days"] = since_days
    if limit:
        params["limit"] = limit
    headers = {"User-Agent": s.http_user_agent}
    if s.wechat_search_api_token:
        headers["Authorization"] = f"Bearer {s.wechat_search_api_token}"
    url = _endpoint(s.wechat_search_base_url)
    polite(urlparse(url).netloc)
    try:
        r = httpx.get(url, params=params, headers=headers, timeout=30, follow_redirects=True)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("wechat search %r failed: %s", query[:40], e)
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for it in _items(payload):
        norm = _normalize(it)
        if norm and norm["url"] not in seen:
            seen.add(norm["url"])
            out.append(norm)
    log.info("wechat search %r → %d articles", query[:40], len(out))
    return out
