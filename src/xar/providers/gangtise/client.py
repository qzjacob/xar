"""Gangtise 投研 Open API client (open.gangtise.com / openapi.gangtise.com).

AccessKey/SecretKey → a temporary accessToken via loginV2. The data endpoints want the
**raw accessToken** in the Authorization header (NO "Bearer " prefix — a prefixed token
returns code 0000001008 "token is invalid"). Token is cached module-level and re-fetched
on expiry or on an invalid-token response. Turnkey-safe: absent keys/enable → available()
is False and callers no-op (never raises at import).
"""
from __future__ import annotations

import threading
import time

import requests

from ...config import get_settings
from ...logging import get_logger

log = get_logger("xar.gangtise")

_AUTH_URL = "https://open.gangtise.com/application/auth/oauth/open/loginV2"
_FUND = "https://openapi.gangtise.com/application/open-fundamental"
_REF = "https://openapi.gangtise.com/application/open-reference"
_AI = "https://openapi.gangtise.com/application/open-ai"            # agent 投研文本

SECURITIES_SEARCH_URL = f"{_REF}/securities/search"
INCOME_URL = f"{_FUND}/financial-report/income-statement/accumulated"
BALANCE_URL = f"{_FUND}/financial-report/balance-sheet/accumulated"
CASHFLOW_URL = f"{_FUND}/financial-report/cash-flow-statement/accumulated"
VALUATION_URL = f"{_FUND}/valuation-analysis"
EARNING_FORECAST_URL = f"{_FUND}/earning-forecast"
MAIN_BUSINESS_URL = f"{_FUND}/main-business"
TOP_HOLDERS_URL = f"{_FUND}/capital-structure/top-holders"
AGENT_URL = f"{_AI}/agent"                                          # + /one-pager|/investment-logic|/peer-comparison

# ── open-insight 非标语义域(券商研报 / 会议·业绩会·专家纪要 / 首席观点)+ MD&A + 线索 ──
_INSIGHT = "https://openapi.gangtise.com/application/open-insight"
BROKER_REPORT_LIST_URL = f"{_INSIGHT}/broker-report/getList"
SUMMARY_LIST_URL = f"{_INSIGHT}/summary/v2/getList"
CHIEF_OPINION_URL = f"{_INSIGHT}/chief-opinion/getList"
MGMT_DISCUSS_ANN_URL = f"{_AI}/management_discuss/from-announcement"
MGMT_DISCUSS_EC_URL = f"{_AI}/management_discuss/from-earningsCall"
SECURITY_CLUE_URL = f"{_AI}/security-clue/getList"    # 真机核实:非 /agent/security_clue

_LOCK = threading.Lock()
_TOK: dict = {}          # {token, uid, tenant, product, at}
_TOKEN_TTL = 50 * 60     # pre-emptive refresh; post() also re-auths on a 1008 (real expiry)
_UA = "XAR/0.1 (+gangtise-provider)"


class GangtiseError(RuntimeError):
    pass


def _auth(force: bool = False) -> dict | None:
    """Fetch (and cache) the accessToken + uid/tenant/product from AK/SK. Returns a SNAPSHOT
    copy (callers read it lock-free while a concurrent re-auth may mutate _TOK). None on
    missing keys or a failed login. The network call runs OUTSIDE the lock so a cold/slow
    login doesn't serialize every caller behind it."""
    s = get_settings()
    if not (s.gts_access_key and s.gts_secret_key):
        return None
    with _LOCK:
        if not force and _TOK.get("token") and (time.monotonic() - _TOK.get("at", 0)) < _TOKEN_TTL:
            return dict(_TOK)
    try:
        r = requests.post(_AUTH_URL, json={"accessKey": s.gts_access_key,
                                           "secretKey": s.gts_secret_key},
                          headers={"User-Agent": _UA}, timeout=30)
        d = (r.json() or {}).get("data") or {}
        if not d.get("accessToken"):
            log.warning("gangtise auth failed: %s", str(r.text)[:120])
            return None
        tok = {"token": d["accessToken"], "uid": str(d.get("uid", "")),
               "tenant": str(d.get("tenantId", "")),
               "product": str(d.get("productCode", 10018)), "at": time.monotonic()}
    except Exception as e:  # noqa: BLE001 — network/SDK issue → graceful skip
        log.warning("gangtise auth error (%s): %s", type(e).__name__, str(e)[:120])
        return None
    with _LOCK:
        _TOK.update(tok)
        return dict(_TOK)


def _headers(tok: dict) -> dict:
    # RAW token (no 'Bearer ' — the fundamental endpoints reject a prefixed token).
    return {"Authorization": tok["token"], "uid": tok["uid"], "tenantid": tok["tenant"],
            "productcode": tok["product"], "User-Agent": _UA}


def available() -> bool:
    """CHEAP config check (enable + keys present) — no network, matching the provider
    convention that available() is a pure key/config probe. The token is acquired lazily on
    the first post(); a bad key surfaces there (graceful None), not here."""
    s = get_settings()
    return bool(s.enable_gangtise and s.gts_access_key and s.gts_secret_key)


def post(url: str, payload: dict, *, _retry: bool = True) -> dict | None:
    """POST an authed data request; returns body['data'] on code 000000, else None.
    Re-auths once on an invalid-token response (token expiry mid-run)."""
    tok = _auth()
    if tok is None:
        return None
    try:
        r = requests.post(url, headers=_headers(tok), json=payload, timeout=90)
        body = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("gangtise POST %s: %s", url.rsplit("/", 1)[-1], str(e)[:120])
        return None
    code = str(body.get("code", ""))
    if code == "000000" and body.get("status") is not False:
        return body.get("data")
    if code == "0000001008" and _retry:            # token invalid/expired → re-auth once
        _auth(force=True)
        return post(url, payload, _retry=False)
    log.warning("gangtise %s code=%s msg=%s", url.rsplit("/", 1)[-1], code, body.get("msg"))
    return None


def rows(data: dict | None) -> list[dict]:
    """Zip a {fieldList:[names], list:[[positional values]]} response into row dicts.
    Tolerates rows that are already dicts."""
    if not isinstance(data, dict):
        return []
    fields = data.get("fieldList") or []
    out: list[dict] = []
    for row in data.get("list") or []:
        if isinstance(row, dict):
            out.append(row)
        elif isinstance(row, (list, tuple)) and fields:
            out.append(dict(zip(fields, row)))
    return out


def pages(url: str, payload: dict, *, page_size: int = 50, max_pages: int = 2):
    """按 from/size 翻页,逐页 yield 行列表(最多 max_pages 页)。空页或不足一页即停。

    open-insight list 端点用 from/size 分页(页 ≤50,默认帽 100)。行结构可能是
    {fieldList,list}(经 rows() 拉平)或直接 {list:[dicts]}——两者都容忍。"""
    for i in range(max_pages):
        data = post(url, {**payload, "from": i * page_size, "size": page_size})
        rs = rows(data)
        if not rs and isinstance(data, dict):
            rs = [r for r in (data.get("list") or []) if isinstance(r, dict)]
        if not rs:
            return
        yield rs
        if len(rs) < page_size:
            return


def resolve_security(keyword: str) -> str | None:
    """证券名称/代码/拼音 → Gangtise gtsCode (e.g. '600519.SH'). None if unresolved."""
    if not keyword:
        return None
    data = post(SECURITIES_SEARCH_URL, {"keyword": keyword, "top": 10,
                                        "category": ["stock", "dr"]})
    lst = (data or {}).get("list") or []
    return lst[0].get("gtsCode") if lst else None
