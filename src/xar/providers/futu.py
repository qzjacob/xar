"""Futu / moomoo OpenAPI connector (富途) — HK personal retail account.

The `futu` Python SDK talks to a local **OpenD** gateway daemon (default
127.0.0.1:11111) that logs in with the account; the SDK never hits Futu servers
directly. Like Wind, this is OFF by default (XAR_ENABLE_FUTU=true + a running
OpenD to arm) and fully guarded — absent a reachable OpenD it returns nothing
rather than failing.

Value: Futu covers **HK + A-share + US** with Chinese names, valuation, capital
flow and 板块/产业链 classification — the HK/CN breadth FMP/finnhub lack. This
module handles market-data snapshots → canonical metrics + prices. News →
documents lives in `pull_news` here; capital-flow alt-signals live in
`providers/alt/futu_flow.py`; plate→ontology mapping in `ontology/futu_plates.py`.
"""
from __future__ import annotations

import threading
from datetime import date, datetime

from ..config import get_settings
from ..ingestion.registry import company_by_id
from ..ontology.standards import FUTU_SNAPSHOT_MAP, RATIO_METRICS
from ..storage import structured
from .base import log

_LOCK = threading.Lock()
_CTX = None  # cached OpenQuoteContext (OpenD holds one persistent socket)


def _quote_ctx():
    """Cached quote context. OpenD keeps a persistent connection; we reuse one
    context across the batch and rebuild it if the socket dropped. Gated on
    enable_futu + a lazy import so the optional `futu` dep never breaks module load."""
    global _CTX
    if not get_settings().enable_futu:
        return None
    with _LOCK:
        if _CTX is not None:
            return _CTX
        try:
            from futu import OpenQuoteContext

            s = get_settings()
            ctx = OpenQuoteContext(host=s.futu_host, port=s.futu_port)
            _CTX = ctx
            return _CTX
        except Exception as e:  # noqa: BLE001 — no OpenD / SDK absent → graceful skip
            log.warning("futu OpenD unavailable (%s): %s", type(e).__name__, str(e)[:120])
            return None


def close() -> None:
    global _CTX
    with _LOCK:
        if _CTX is not None:
            try:
                _CTX.close()
            except Exception:  # noqa: BLE001
                pass
            _CTX = None


def available() -> bool:
    ctx = _quote_ctx()
    if ctx is None:
        return False
    try:
        from futu import RET_OK

        r, _ = ctx.get_global_state()
        return r == RET_OK
    except Exception as e:  # noqa: BLE001
        log.warning("futu global_state failed (%s): %s", type(e).__name__, str(e)[:120])
        close()
        return False


# ── ticker ↔ Futu code ─────────────────────────────────────────────────────────
def futu_code(company_id: str) -> str | None:
    """Registry ticker → Futu code. HK.xxxxx (5-digit zero-pad), SH.nnnnnn, SZ.nnnnnn,
    US.SYM. Returns None for names Futu can't address here."""
    c = company_by_id(company_id)
    if not c:
        return None
    return code_from_tickers(c.get("tickers", []))


def code_from_tickers(tickers: list[str]) -> str | None:
    # Prefer HK/CN (Futu's edge); fall back to US so the provider can enrich US too.
    for t in tickers:
        if t.endswith(".HK"):
            num = t.split(".")[0].lstrip("0") or "0"
            return f"HK.{int(num):05d}"
        if t.endswith((".SS", ".SH")):
            return f"SH.{t.split('.')[0]}"
        if t.endswith(".SZ"):
            return f"SZ.{t.split('.')[0]}"
    for t in tickers:  # US: plain symbol, no dot
        if "." not in t and t.isascii() and t.isupper():
            return f"US.{t}"
    return None


def _num(v) -> float | None:
    """Futu returns 'N/A' / '' for absent numeric fields."""
    if v in (None, "N/A", "", "nan"):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


# ── market snapshot → canonical valuation metrics + latest price bar ───────────
def pull_snapshot(company_id: str) -> int:
    ctx = _quote_ctx()
    code = futu_code(company_id)
    if ctx is None or not code:
        return 0
    try:
        from futu import RET_OK

        r, df = ctx.get_market_snapshot([code])
    except Exception as e:  # noqa: BLE001
        log.warning("futu snapshot %s: %s", code, str(e)[:120])
        return 0
    if r != RET_OK or df is None or not len(df):
        return 0
    row = df.iloc[0]
    ccy = "HKD" if code.startswith("HK.") else "CNY" if code.startswith(("SH.", "SZ.")) else "USD"
    n = 0
    for field, canon in FUTU_SNAPSHOT_MAP.items():
        val = _num(row.get(field))
        if val is None:
            continue
        unit = "ratio" if canon in RATIO_METRICS else ccy
        structured.upsert_fundamental(company_id, canon, val, period="snapshot",
                                      freq="snapshot", unit=unit, source="futu",
                                      meta={"code": code, "as_of": str(row.get("update_time"))})
        n += 1
    # latest price bar (idempotent on ticker,d,source)
    last = _num(row.get("last_price"))
    d = _snap_date(row.get("update_time"))
    if last is not None and d:
        structured.upsert_prices(company_id, code, [{
            "d": d, "open": _num(row.get("open_price")), "high": _num(row.get("high_price")),
            "low": _num(row.get("low_price")), "close": last, "volume": _num(row.get("volume")),
        }], source="futu")
    return n


def _snap_date(update_time) -> str | None:
    if not update_time:
        return None
    try:
        return datetime.strptime(str(update_time)[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return date.today().isoformat()


# ── news / 资讯 → documents (headline + canonical link, self-use; no body) ─────
def _parse_news_time(raw) -> str | None:
    """Futu publish_time is loose: 'HH:MM' (today), 'M/D' or 'MM-DD' (this year, no
    year → roll back a year if the month is in the future), or a full ISO stamp."""
    s = str(raw or "").strip()
    if not s:
        return None
    now = datetime.now()
    for fmt, has_year, is_time in (("%Y-%m-%d %H:%M:%S", True, False),
                                   ("%Y-%m-%d %H:%M", True, False),
                                   ("%Y-%m-%d", True, False),
                                   ("%m-%d %H:%M", False, False),
                                   ("%m/%d %H:%M", False, False),
                                   ("%m-%d", False, False),
                                   ("%m/%d", False, False),
                                   ("%H:%M", False, True)):
        try:
            t = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if is_time:
            t = t.replace(year=now.year, month=now.month, day=now.day)
        elif not has_year:
            t = t.replace(year=now.year)
            if t > now:                       # 'M/D' in the future → it's last year's
                t = t.replace(year=now.year - 1)
        return t.isoformat(sep=" ")
    return None


def pull_news(company_id: str) -> int:
    ctx = _quote_ctx()
    code = futu_code(company_id)
    if ctx is None or not code:
        return 0
    try:
        from futu import RET_OK

        r, df = ctx.get_search_news(code)
    except Exception as e:  # noqa: BLE001
        log.warning("futu news %s: %s", code, str(e)[:120])
        return 0
    if r != RET_OK or df is None or not len(df):
        return 0
    from ..ingestion.base import Doc, save
    from ..ontology import cn_routing
    from ..storage import db

    limit = get_settings().futu_news_per_stock
    n = 0
    for _, row in df.head(limit).iterrows():
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        if not title or not url:
            continue
        doc_id = save(Doc(
            company_id=company_id, source="futu", doc_type="news",
            title=title, text=title, url=url,           # headline only (no body available)
            published_at=_parse_news_time(row.get("publish_time")),
            permission="grey", license_tag="futu-news-extracted-facts-self-use",
            meta={"code": code, "news_source": str(row.get("source") or ""),
                  "view_count": _num(row.get("view_count"))}))
        # theme-tag from the Chinese headline (only fill when empty)
        themes = cn_routing.theme_hits(title)
        if themes:
            db.execute("UPDATE documents SET theme=COALESCE(theme,%s) WHERE id=%s",
                       (themes[0], doc_id))
        n += 1
    return n


# ── 板块归属 → futu_plates 表(公司行业分类的事实副本 + 本体主题映射)──────────
def pull_plates(company_id: str) -> int:
    ctx = _quote_ctx()
    code = futu_code(company_id)
    if ctx is None or not code:
        return 0
    try:
        from futu import RET_OK

        r, df = ctx.get_owner_plate([code])
    except Exception as e:  # noqa: BLE001
        log.warning("futu owner_plate %s: %s", code, str(e)[:120])
        return 0
    if r != RET_OK or df is None or not len(df):
        return 0
    from ..ontology import futu_plates
    from ..storage import db

    rows = []
    for _, row in df.iterrows():
        pid = str(row.get("plate_code") or "").strip()
        pname = str(row.get("plate_name") or "").strip()
        if not pid or not pname:
            continue
        rows.append((company_id, pid, pname, str(row.get("plate_type") or ""),
                     futu_plates.name_themes(pname)))
    if not rows:
        return 0
    db.executemany(
        """INSERT INTO futu_plates (company_id, plate_id, plate_name, plate_type, themes)
           VALUES (%s,%s,%s,%s,%s)
           ON CONFLICT (company_id, plate_id) DO UPDATE SET
             plate_name=EXCLUDED.plate_name, plate_type=EXCLUDED.plate_type,
             themes=EXCLUDED.themes, observed_at=now()""",
        rows)
    return len(rows)


def plate_theme_gaps(limit: int = 50) -> list[dict]:
    """本体缺口发现:富途板块暗示某公司属于某主题,但该公司当前未被策展为此主题。
    纯 DB 查询(无 OpenD),供 ops/复核用。"""
    from ..storage import db

    return db.query(
        """SELECT fp.company_id, array_agg(DISTINCT t) AS futu_implied, c.themes AS curated
             FROM futu_plates fp
             CROSS JOIN LATERAL unnest(fp.themes) AS t
             JOIN companies c ON c.id = fp.company_id
            WHERE NOT (t = ANY(c.themes))
            GROUP BY fp.company_id, c.themes
            LIMIT %s""", (limit,))


def pull(company_id: str) -> dict:
    if not available():
        return {}
    out = {"snapshot_metrics": pull_snapshot(company_id), "news": pull_news(company_id),
           "plates": pull_plates(company_id)}
    log.info("futu %s: %s", company_id, out)
    return out
