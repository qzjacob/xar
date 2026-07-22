"""we-mp-rss 管理 API 客户端 —— 账号级发现的后端(Phase 1)。

feed 端点(`/feed/{id}.json`、`/rss`)是**公开**的(ingestion/wechat.py 免鉴权消费);但
**搜索公众号 + 添加订阅**端点需鉴权。we-mp-rss 支持 **AK/SK 非交互凭据**
(`Authorization: AK-SK <ak>:<sk>`,在其后台生成)或 JWT Bearer。本模块封两个动作:

  search_accounts(kw)  → GET  {base}/api/v1/wx/search/{kw}   → 用登录会话打 WeChat searchbiz,
                          返回全网匹配的公众号列表(fakeid/nickname/…)。
  subscribe(account)   → POST {base}/api/v1/wx/mps           → 添加订阅(mp_id=base64(fakeid)),
                          we-mp-rss 建 Feed `MP_WXS_{fakeid}` 并首次抓文;返回 feed id。

之后该 feed 由 roster 名册 + glm_worker 逐号轮询接管(`/feed/{feed_id}.json`),文章走现有
triage。任何网络/鉴权/会话错误一律 WARN 后返回空(搜索/订阅是脆弱环节,失败=本轮少发现
几个号,绝不炸调用方)。会话过期(base_resp.ret≠0)时 search 返回 [] —— 需运营方重扫码。
"""
from __future__ import annotations

import base64
from urllib.parse import quote

import httpx

from ..config import get_settings
from ..logging import get_logger
from .base import polite

log = get_logger("xar.ingest.werss_api")

_API = "/api/v1/wx"


def available() -> bool:
    """base url 已配置且有鉴权凭据(AK/SK 或 token)—— search/add 需鉴权。"""
    s = get_settings()
    return bool(s.werss_base_url.strip()) and bool(
        (s.werss_ak and s.werss_sk) or s.werss_api_token)


def _auth_header() -> dict:
    s = get_settings()
    if s.werss_ak and s.werss_sk:                 # 首选:非交互 AK/SK
        return {"Authorization": f"AK-SK {s.werss_ak}:{s.werss_sk}"}
    if s.werss_api_token:                         # 回退:JWT Bearer
        return {"Authorization": f"Bearer {s.werss_api_token}"}
    return {}


def _headers() -> dict:
    return {"User-Agent": get_settings().http_user_agent, **_auth_header()}


def _unwrap(payload):
    """success_response 包了一层 {code,data,message};取 data(无则原样)。"""
    if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], (dict, list)):
        return payload["data"]
    return payload


def search_accounts(kw: str, *, limit: int = 10) -> list[dict]:
    """按关键词搜全网公众号。返回归一化 [{fakeid,name,avatar,intro}](可能为空)。"""
    s = get_settings()
    if not available() or not kw.strip():
        return []
    base = s.werss_base_url.rstrip("/")
    url = f"{base}{_API}/mps/search/{quote(kw)}"   # 搜索公众号在 /mps 路由下
    polite(_host(base))
    try:
        r = httpx.get(url, params={"limit": limit, "offset": 0},
                      headers=_headers(), timeout=30, follow_redirects=True)
        r.raise_for_status()
        data = _unwrap(r.json())
    except Exception as e:  # noqa: BLE001
        log.warning("werss search_accounts %r failed: %s", kw[:30], e)
        return []
    items = data.get("list") if isinstance(data, dict) else data
    out: list[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        fakeid = str(it.get("fakeid") or it.get("faker_id") or "").strip()
        name = str(it.get("nickname") or it.get("name") or it.get("mp_name") or "").strip()
        if not fakeid or not name:
            continue
        out.append({"fakeid": fakeid, "name": name,
                    "avatar": str(it.get("round_head_img") or it.get("avatar") or ""),
                    "intro": str(it.get("signature") or it.get("mp_intro") or "")[:255]})
    log.info("werss search_accounts %r → %d 公众号", kw[:30], len(out))
    return out


def subscribe(account: dict) -> str | None:
    """订阅一个公众号(add_mp)。成功返回 feed id(MP_WXS_…),失败/会话过期返回 None。"""
    s = get_settings()
    if not available():
        return None
    fakeid = account.get("fakeid")
    name = account.get("name")
    if not fakeid or not name:
        return None
    base = s.werss_base_url.rstrip("/")
    body = {"mp_name": name[:255],
            "mp_id": base64.b64encode(str(fakeid).encode()).decode(),   # add_mp 期望 base64(fakeid)
            "avatar": (account.get("avatar") or "")[:500],
            "mp_intro": (account.get("intro") or "")[:255]}
    polite(_host(base))
    try:
        r = httpx.post(f"{base}{_API}/mps", json=body, headers=_headers(),
                       timeout=30, follow_redirects=True)
        r.raise_for_status()
        data = _unwrap(r.json()) if r.content else {}
        feed_id = data.get("id") if isinstance(data, dict) else None
        if not feed_id:
            log.warning("werss subscribe %s(%s) 返回无 feed id — 判为失败", name, fakeid)
            return None
        return str(feed_id)
    except Exception as e:  # noqa: BLE001
        log.warning("werss subscribe %s(%s) failed: %s", name, fakeid, str(e)[:160])
        return None


def unsubscribe(feed_id: str) -> bool:
    """退订一个 feed(DELETE add_mp)。成功/号本就不存在 → True;网络/鉴权/会话错误 → False。
    止损(prune_accounts)用:真正从 we-mp-rss 移除废号,避免其在服务端堆积继续抓文。"""
    s = get_settings()
    if not available() or not (feed_id or "").strip():
        return False
    base = s.werss_base_url.rstrip("/")
    polite(_host(base))
    try:
        r = httpx.delete(f"{base}{_API}/mps/{quote(str(feed_id))}", headers=_headers(),
                         timeout=30, follow_redirects=True)
        if r.status_code == 404:               # 已不存在 = 目标态达成,幂等成功
            return True
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("werss unsubscribe %s failed: %s", feed_id, str(e)[:160])
        return False


def _host(base_url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(base_url).netloc
