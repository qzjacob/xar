"""wechat-download-api(wcda,tmwgsicp)客户端 —— 文章级发现后端(Phase 1 定型)。

wcda 用 **curl_cffi(Chrome TLS 指纹)** 登录微信公众号平台(比 we-mp-rss 的 selector 抓取
更稳,实测能拿到有效会话 / searchbiz 可用),提供三段:

  GET  /api/public/searchbiz?query=kw          搜全网公众号 → [{fakeid, nickname, alias}]
  GET  /api/public/articles?fakeid=&limit=     逐号取文章列表 → [{title, link, update_time}]
  POST /api/article {url}                      解析单篇全文 → {title, plain_content, author, publish_time}

响应统一 `{"success": bool, "data": {...}, "error": null}`。凭据在其容器内 ~4 天有效,过期需
重扫码(它会 webhook 预警)。任何网络/会话错误一律 WARN 后返回空(脆弱环节不炸调用方)。
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from ..config import get_settings
from ..logging import get_logger
from .base import polite

log = get_logger("xar.ingest.wcda")

_TAG = re.compile(r"<[^>]+>")


def available() -> bool:
    return bool(get_settings().wcda_base_url.strip())


def _base() -> str:
    return get_settings().wcda_base_url.rstrip("/")


def _host() -> str:
    return urlparse(_base()).netloc


def _data(payload):
    """取 {success,data,error} 里的 data;非成功/异常形态 → 空。"""
    if isinstance(payload, dict):
        if payload.get("success") is False:
            return {}
        d = payload.get("data")
        if isinstance(d, (dict, list)):
            return d
        return payload
    return payload if isinstance(payload, list) else {}


def search_accounts(kw: str, *, limit: int = 6) -> list[dict]:
    """按关键词搜全网公众号。返回 [{fakeid, name, alias}](可能为空)。"""
    if not available() or not kw.strip():
        return []
    polite(_host())
    try:
        r = httpx.get(f"{_base()}/api/public/searchbiz",
                      params={"query": kw, "limit": limit}, timeout=30)
        r.raise_for_status()
        data = _data(r.json())
    except Exception as e:  # noqa: BLE001
        log.warning("wcda searchbiz %r failed: %s", kw[:30], str(e)[:140])
        return []
    lst = data.get("list") if isinstance(data, dict) else data
    out: list[dict] = []
    for it in lst or []:
        if not isinstance(it, dict):
            continue
        fakeid = str(it.get("fakeid") or "").strip()
        name = str(it.get("nickname") or it.get("name") or "").strip()
        if fakeid and name:
            out.append({"fakeid": fakeid, "name": name, "alias": str(it.get("alias") or "")})
    log.info("wcda searchbiz %r → %d 公众号", kw[:30], len(out))
    return out


def list_articles(fakeid: str, *, limit: int = 6) -> list[dict]:
    """逐号取最近文章列表(仅元数据 + link,无正文)。返回 [{url, title, update_time}]。"""
    if not available() or not fakeid:
        return []
    polite(_host())
    try:
        r = httpx.get(f"{_base()}/api/public/articles",
                      params={"fakeid": fakeid, "limit": limit}, timeout=45)
        r.raise_for_status()
        data = _data(r.json())
    except Exception as e:  # noqa: BLE001
        log.warning("wcda articles %s failed: %s", fakeid, str(e)[:140])
        return []
    arts = (data.get("articles") or data.get("list")) if isinstance(data, dict) else data
    out: list[dict] = []
    for a in arts or []:
        if not isinstance(a, dict):
            continue
        url = str(a.get("link") or a.get("url") or "").strip()
        if url:
            out.append({"url": url, "title": str(a.get("title") or ""),
                        "update_time": a.get("update_time")})
    return out[:limit]      # 后端可能忽略 limit,代码侧强制截断(界定逐篇解析成本)


def parse_article(url: str) -> dict | None:
    """解析单篇全文(curl_cffi)。返回 {title, text, author, publish_time} 或 None。"""
    if not available() or not url:
        return None
    polite(_host())
    try:
        r = httpx.post(f"{_base()}/api/article", json={"url": url}, timeout=60)
        r.raise_for_status()
        data = _data(r.json())
    except Exception as e:  # noqa: BLE001
        log.warning("wcda parse %s failed: %s", url[:50], str(e)[:140])
        return None
    if not isinstance(data, dict):
        return None
    text = (data.get("plain_content") or "").strip()
    if not text and data.get("content"):        # 兜底:content 是 HTML → 去标签
        text = _TAG.sub(" ", data["content"]).strip()
    if not text:
        return None
    return {"title": (data.get("title") or "").strip(), "text": text,
            "author": (data.get("author") or "").strip(), "publish_time": data.get("publish_time")}
