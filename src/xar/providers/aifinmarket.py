"""AIFINmarket (万得 Wind) — the professional source for CN A-share companies +
supply-chain intelligence/资讯 + 另类研报摘要 (公司/行业/策略/宏观).

Talks to Wind's AIFINmarket MCP servers over HTTP (JSON-RPC `tools/call`, Bearer
auth) — pure Python, no node dependency. The token-accessible surface (verified by
live tools/list on every seat) is 6 servers / 34 tools; this connector uses:
    stock_data.get_stock_price_indicators    -> valuation / market cap (→ FinMetric)
    financial_docs.get_company_announcements  -> official announcements (→ documents)
    financial_docs.get_financial_news         -> 研究·媒体资讯摘要 (→ documents)

There is NO dedicated 券商研报/纪要 server (that surface is served by the gangtise
track); `get_financial_news` is the 另类研报摘要 engine — a NL semantic search over
Wind's news+research corpus that returns dated, relevance-scored 公司/行业/策略/宏观
research summaries. Announcements + summaries land in `documents(source='aifinmarket',
grey)` so they flow through the expert-agent processor + KG (aifinmarket ∈ ALT_SOURCES).

**Multi-account subscription pool**: `.env` provisions numbered seats
AIFINMARKET{1..N}_TOKEN (all seats share identical permissions). `_mcp_call`
round-robins across the pool so every seat's daily quota is used — not just seat 1 —
and fails over to the next seat when one hits a quota/rate-limit error (the response
carries no quota headers, so exhaustion is detected reactively from the error text).
Gated by having ≥1 token; a no-op when the pool is empty. Keys:
https://aifinmarket.wind.com.cn/#/user/overview .
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import threading
import time

import httpx

from ..config import get_settings
from ..ingestion.registry import company_by_id
from ..ontology.standards import FinMetric
from ..storage import structured
from .base import log

_HOST = "mcp.wind.com.cn"
_CJK = re.compile(r"[一-鿿]+")

# get_stock_price_indicators 'indexes' (verbatim Wind names) -> canonical metric
_VAL_INDEXES = {"总市值1": (FinMetric.MARKET_CAP.value, "CNY"),
                "市盈率(TTM)": (FinMetric.PE.value, "x")}

# ── Multi-account dispatch state (in-memory; the worker is long-lived, one sweep/day) ──
# quota/rate-limit markers that justify cooling a seat and failing over (curated so a
# plain 参数错=`无效的请求` does NOT retire a seat).
_QUOTA_MARKERS = ("额度", "配额", "限额", "超过", "超出", "上限", "频率", "太频繁",
                  "权限不足", "无权", "quota", "rate limit", "ratelimit", "too many",
                  "exceed", "limit reached")
_rr = 0                              # round-robin cursor across the seat pool
_cooldown: set[str] = set()          # seat ids that hit quota this process-day (skipped)
_usage_date: str | None = None       # date the counters below belong to
_usage: dict[str, int] = {}          # seat id -> calls made today
_throttle_lock = threading.Lock()
_last_call = [0.0]


def _tok_id(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()[:12]


def _today() -> str:
    return datetime.date.today().isoformat()


def _reset_if_new_day() -> None:
    global _usage_date, _usage, _cooldown
    d = _today()
    if _usage_date != d:
        _usage_date, _usage, _cooldown = d, {}, set()


def _reset_state() -> None:
    """Clear dispatcher state (rotation/usage/cooldown). For tests."""
    global _rr, _cooldown, _usage, _usage_date, _last_call
    _rr, _cooldown, _usage, _usage_date, _last_call = 0, set(), {}, None, [0.0]


def _pool() -> list[str]:
    return get_settings().aifinmarket_tokens


def available() -> bool:
    return bool(_pool())


def _throttle() -> None:
    iv = get_settings().aifinmarket_min_interval_seconds
    if iv <= 0:
        return
    with _throttle_lock:
        wait = iv - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()


def _pick_token() -> str | None:
    """Round-robin the next healthy seat (not cooling, under daily cap). None when all
    seats are exhausted for the day."""
    global _rr
    _reset_if_new_day()
    pool = _pool()
    if not pool:
        return None
    cap = get_settings().aifinmarket_daily_calls_per_account
    n = len(pool)
    for _ in range(n):
        tok = pool[_rr % n]
        _rr += 1
        tid = _tok_id(tok)
        if tid in _cooldown:
            continue
        if cap and _usage.get(tid, 0) >= cap:
            continue
        return tok
    return None


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


def _is_quota_error(payload: dict) -> bool:
    """True only for quota/rate-limit exhaustion (justifies seat failover); a plain
    `无效的请求` (bad params / no data) is NOT quota and must not retire the seat."""
    if not isinstance(payload, dict):
        return False
    err = payload.get("error")
    if isinstance(err, dict):
        msg = str(err.get("message", ""))
        if any(m in msg or m.lower() in msg.lower() for m in _QUOTA_MARKERS):
            return True
    res = payload.get("result") or {}
    if isinstance(res, dict) and res.get("isError"):
        txt = "".join(str(c.get("text", "")) for c in (res.get("content") or [])
                      if isinstance(c, dict))
        if any(m in txt or m.lower() in txt.lower() for m in _QUOTA_MARKERS):
            return True
    return False


def _mcp_call(server_type: str, tool: str, arguments: dict, timeout: float = 90) -> dict | None:
    """JSON-RPC tools/call to a Wind MCP server via the seat pool. Round-robins seats;
    on a quota/rate-limit error cools that seat and fails over to the next. Returns the
    inner tool payload (dict) or None. Never raises."""
    _reset_if_new_day()
    pool = _pool()
    if not pool:
        return None
    for _try in range(len(pool)):
        tok = _pick_token()
        if tok is None:
            log.warning("aifinmarket: all %d seat(s) exhausted (cooldown/daily-cap) for %s.%s",
                        len(pool), server_type, tool)
            return None
        tid = _tok_id(tok)
        _usage[tid] = _usage.get(tid, 0) + 1
        _throttle()
        headers = {"Authorization": f"Bearer {tok}",
                   "Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}
        body = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments}}
        try:
            r = httpx.post(_endpoint(server_type), headers=headers, json=body, timeout=timeout)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001 — transport error: don't burn other seats
            log.warning("aifinmarket %s.%s (seat %s) http failed: %s", server_type, tool, tid, e)
            return None
        payload = _parse_sse(r.text)
        if _is_quota_error(payload):
            _cooldown.add(tid)
            log.warning("aifinmarket seat %s hit quota on %s.%s → cooling + failover",
                        tid, server_type, tool)
            continue
        res = (payload or {}).get("result") or {}
        if isinstance(res, dict) and res.get("isError"):
            return None                              # non-quota per-call error (bad params/no data)
        inner_text = (res.get("content") or [{}])[0].get("text") if res.get("content") else None
        if inner_text:
            try:
                return json.loads(inner_text)
            except Exception:
                return {"raw": inner_text}
        return res or None
    return None


def _persist_usage(usage: dict) -> None:
    """Best-effort snapshot of per-seat daily usage for observability (keeps ~14 days)."""
    try:
        from ..storage import kvstate

        st = kvstate.get_state("aifin_usage", {})
        st[_today()] = usage
        for old in sorted(st)[:-14]:
            st.pop(old, None)
        kvstate.save_state("aifin_usage", st)
    except Exception as e:  # noqa: BLE001
        log.debug("aifin usage persist skipped: %s", e)


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


def _save_docs(company_id: str | None, items: list[dict], doc_type: str,
               *, scope: str = "company", query: str | None = None) -> int:
    """Persist research-summary/announcement rows as documents. A stable `doc_id`
    (scope + hash(title|date)) keeps the same summary on one row across re-fetches;
    meta.scope drives the 公司/行业/策略/宏观 分轨观测。"""
    from ..ingestion.base import Doc, save

    n = 0
    for a in items[:50]:
        text = (a.get("content") or a.get("summary") or a.get("title") or "").strip()
        if len(text) < 40:
            continue
        title = a.get("title") or (doc_type + " · " + text[:30])
        date = a.get("date")
        did = "aifinmarket:%s:%s" % (
            scope, hashlib.sha256(f"{title}|{date}".encode()).hexdigest()[:16])
        save(Doc(company_id=company_id, source="aifinmarket", doc_type=a.get("doc_type") or doc_type,
                 title=title, text=text[:120_000], url=a.get("url"), published_at=date,
                 permission="grey", license_tag="aifinmarket-cn-a-self-use", doc_id=did,
                 meta={"provider": "aifinmarket", "scope": scope, "query": query,
                       "relevance": a.get("relevance")}))
        n += 1
    return n


def _pull_news_docs(company_id: str | None, query: str, *, scope: str, top_k: int | None = None) -> int:
    """One get_financial_news query → scoped documents. Used by the research sweep for
    every dimension (company/industry/strategy/macro)."""
    top_k = top_k or get_settings().aifinmarket_news_top_k
    out = _mcp_call("financial_docs", "get_financial_news", {"query": query, "top_k": top_k})
    items = ((out or {}).get("data") or {}).get("items") or []
    return _save_docs(company_id, items, "news", scope=scope, query=query)


def pull_announcements(company_id: str, top_k: int = 5) -> int:
    name = _cn_name(company_id)
    if not name or not available():
        return 0
    out = _mcp_call("financial_docs", "get_company_announcements",
                    {"query": f"{name} 最新公告", "top_k": top_k})
    items = ((out or {}).get("data") or {}).get("items") or []
    return _save_docs(company_id, items, "announcement", scope="company",
                      query=f"{name} 最新公告")


def pull_news(company_id: str, top_k: int = 5) -> int:
    name = _cn_name(company_id)
    if not name or not available():
        return 0
    return _pull_news_docs(company_id, f"{name} 产业链 业绩", scope="company", top_k=top_k)


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
    processing. Retained for back-compat; the daily research sweep supersedes it."""
    if not available():
        return 0
    return _pull_news_docs(None, theme_query, scope="industry", top_k=top_k)


def pull_research_sweep(*, company_universe: list[str] | None = None) -> dict:
    """Daily 另类研报摘要 sweep across ALL subscription seats. Round-robins seats so each
    seat's daily quota is filled (not just seat 1). Four dimensions:
      公司 — full universe 每日全扫: get_financial_news(资讯) + get_company_announcements(CN A股);
      行业 — THEMES(nameCn) + 策展行业清单;
      策略 — STRATEGY_QUERIES;
      宏观 — MACRO_QUERIES (定性观点;定量 EDB 归 wind_edb 轨).
    Returns per-dimension doc counts + per-seat call usage + seats cooled by quota."""
    if not available():
        return {"skipped": "aifinmarket disabled"}
    _reset_if_new_day()
    from ..ingestion.registry import COMPANIES, THEMES
    from . import aifin_catalog as cat

    counts = {"company_news": 0, "company_ann": 0, "industry": 0, "strategy": 0, "macro": 0}
    ids = company_universe if company_universe is not None else [c["id"] for c in COMPANIES]

    # 1) 公司维:全库 universe 每日全扫 —— 资讯(全部) + 公告(CN A 股)
    for cid in ids:
        c = company_by_id(cid)
        name = _cn_name(cid) or (c or {}).get("name")
        if not name:
            continue
        try:
            counts["company_news"] += _pull_news_docs(
                cid, f"{name} 最新 研报 观点 业绩", scope="company",
                top_k=get_settings().aifinmarket_company_top_k)
        except Exception as e:  # noqa: BLE001
            log.warning("aifin company news %s: %s", cid, str(e)[:120])
        if _cn_code(cid):
            try:
                counts["company_ann"] += pull_announcements(cid)
            except Exception as e:  # noqa: BLE001
                log.warning("aifin company ann %s: %s", cid, str(e)[:120])

    # 2) 行业维:THEMES + 策展行业清单
    theme_qs = [f"{t.get('nameCn') or tid} 产业链 行业 研报 观点" for tid, t in THEMES.items()]
    for q in theme_qs + list(cat.INDUSTRY_QUERIES):
        try:
            counts["industry"] += _pull_news_docs(None, q, scope="industry")
        except Exception as e:  # noqa: BLE001
            log.warning("aifin industry '%s': %s", q[:24], str(e)[:120])

    # 3) 策略维
    for q in cat.STRATEGY_QUERIES:
        try:
            counts["strategy"] += _pull_news_docs(None, q, scope="strategy")
        except Exception as e:  # noqa: BLE001
            log.warning("aifin strategy '%s': %s", q[:24], str(e)[:120])

    # 4) 宏观维(定性)
    for q in cat.MACRO_QUERIES:
        try:
            counts["macro"] += _pull_news_docs(None, q, scope="macro")
        except Exception as e:  # noqa: BLE001
            log.warning("aifin macro '%s': %s", q[:24], str(e)[:120])

    usage = dict(_usage)
    _persist_usage(usage)
    log.info("aifinmarket research sweep: %s | seats=%d usage=%s cooling=%s",
             counts, len(_pool()), usage, sorted(_cooldown))
    return {"counts": counts, "seats": len(_pool()), "usage": usage, "cooling": sorted(_cooldown)}
