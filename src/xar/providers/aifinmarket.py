"""AIFINmarket (万得 Wind) — the professional source for CN A-share companies +
supply-chain intelligence/资讯.

Talks to Wind's AIFINmarket MCP servers over HTTP (JSON-RPC `tools/call`, Bearer
auth) — pure Python, no node dependency:
    stock_data.get_stock_price_indicators   -> valuation / market cap (→ FinMetric)
    financial_docs.get_company_announcements -> official announcements (→ documents)
    financial_docs.get_financial_news        -> industry 资讯 / news (→ documents)

Announcements + news land in `documents(source='aifinmarket', grey)` so they flow
through the expert-agent processor + KG. Only acts on CN A-share names. Gated by
AIFINMARKET_TOKEN (the Wind API key); a no-op when unset. Get a key at
https://aifinmarket.wind.com.cn/#/user/overview .
"""
from __future__ import annotations

import json
import re

import httpx

from ..config import get_settings
from ..ingestion.base import polite
from ..ingestion.registry import company_by_id
from ..ontology.standards import FinMetric
from ..storage import structured
from .base import log

_HOST = "mcp.wind.com.cn"
_CJK = re.compile(r"[一-鿿]+")

# get_stock_price_indicators 'indexes' (verbatim Wind names) -> canonical metric
_VAL_INDEXES = {"总市值1": (FinMetric.MARKET_CAP.value, "CNY"),
                "市盈率(TTM)": (FinMetric.PE.value, "x")}


def available() -> bool:
    return bool(get_settings().aifinmarket_token)


def _endpoint(server_type: str) -> str:
    base = get_settings().aifinmarket_base_url.rstrip("/") if get_settings().aifinmarket_base_url else \
        "https://mcp.wind.com.cn"
    return f"{base}/vserver_{server_type}/mcp/"


def _parse_sse(text: str) -> dict:
    for ln in text.splitlines():
        ln = ln.strip()
        if ln.startswith("data:"):
            try:
                o = json.loads(ln[5:].strip())
            except Exception:
                continue
            if "result" in o or "error" in o:
                return o
    try:
        return json.loads(text)
    except Exception:
        return {}


def _mcp_call(server_type: str, tool: str, arguments: dict, timeout: float = 90) -> dict | None:
    """JSON-RPC tools/call to a Wind MCP server. Returns the inner tool payload
    (dict) or None. Never raises."""
    key = get_settings().aifinmarket_token
    if not key:
        return None
    polite(_HOST)
    headers = {"Authorization": f"Bearer {key}",
               "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    body = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments}}
    try:
        r = httpx.post(_endpoint(server_type), headers=headers, json=body, timeout=timeout)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("aifinmarket %s.%s failed: %s", server_type, tool, e)
        return None
    payload = _parse_sse(r.text)
    res = (payload or {}).get("result") or {}
    inner_text = (res.get("content") or [{}])[0].get("text") if res.get("content") else None
    if inner_text:
        try:
            return json.loads(inner_text)
        except Exception:
            return {"raw": inner_text}
    return res or None


def _cn_code(company_id: str) -> str | None:
    c = company_by_id(company_id)
    if not c:
        return None
    return next((t for t in c.get("tickers", []) if t.endswith((".SZ", ".SS", ".SH"))), None)


def _cn_name(company_id: str) -> str | None:
    c = company_by_id(company_id)
    if not c:
        return None
    m = _CJK.search(c["name"])
    if m:
        return m.group(0)
    return next((a for a in c.get("aliases", []) if _CJK.search(a)), None)


def _num(s) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def pull_fundamentals(company_id: str) -> int:
    code = _cn_code(company_id)
    if not code or not available():
        return 0
    out = _mcp_call("stock_data", "get_stock_price_indicators",
                    {"windcode": code, "indexes": ",".join(_VAL_INDEXES)})
    data = (out or {}).get("data") or {}
    cols = [c["name"] for c in data.get("columns", [])]
    rows = data.get("rows") or []
    if not rows:
        return 0
    row = dict(zip(cols, rows[0]))
    n = 0
    for wind_name, (canon, unit) in _VAL_INDEXES.items():
        v = _num(row.get(wind_name))
        if v is None:
            continue
        structured.upsert_fundamental(company_id, canon, v, period="latest", freq="snapshot",
                                      unit=unit, source="aifinmarket")
        n += 1
    return n


def _save_docs(company_id: str, items: list[dict], doc_type: str) -> int:
    from ..ingestion.base import Doc, save

    n = 0
    for a in items[:8]:
        text = (a.get("content") or a.get("summary") or a.get("title") or "").strip()
        if len(text) < 40:
            continue
        save(Doc(company_id=company_id, source="aifinmarket", doc_type=a.get("doc_type") or doc_type,
                 title=a.get("title") or (doc_type + " · " + (text[:30])), text=text[:120_000],
                 url=a.get("url"), published_at=a.get("date"), permission="grey",
                 license_tag="aifinmarket-cn-a-self-use",
                 meta={"provider": "aifinmarket", "relevance": a.get("relevance")}))
        n += 1
    return n


def pull_announcements(company_id: str, top_k: int = 5) -> int:
    name = _cn_name(company_id)
    if not name or not available():
        return 0
    out = _mcp_call("financial_docs", "get_company_announcements",
                    {"query": f"{name} 最新公告", "top_k": top_k})
    items = ((out or {}).get("data") or {}).get("items") or []
    return _save_docs(company_id, items, "announcement")


def pull_news(company_id: str, top_k: int = 5) -> int:
    name = _cn_name(company_id)
    if not name or not available():
        return 0
    out = _mcp_call("financial_docs", "get_financial_news",
                    {"query": f"{name} 产业链 业绩", "top_k": top_k})
    items = ((out or {}).get("data") or {}).get("items") or []
    return _save_docs(company_id, items, "news")


def pull(company_id: str) -> dict:
    if not available() or not _cn_code(company_id):
        return {}
    out = {"fundamentals": pull_fundamentals(company_id),
           "announcements": pull_announcements(company_id),
           "news": pull_news(company_id)}
    log.info("aifinmarket %s: %s", company_id, out)
    return out


def pull_theme_news(theme_query: str, top_k: int = 8) -> int:
    """Industry-chain 资讯 sweep (not company-scoped) → documents for expert
    processing. Used for broad supply-chain intelligence."""
    if not available():
        return 0
    out = _mcp_call("financial_docs", "get_financial_news", {"query": theme_query, "top_k": top_k})
    items = ((out or {}).get("data") or {}).get("items") or []
    return _save_docs(None, items, "news")
