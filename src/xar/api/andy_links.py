"""XAR 原生勾稽 API（/api/andy/link/*）—— 宏观指标 ↔ 产业链的双向查询面。

这些路由由 app.py 以 @app.get 注册在 /api/andy 的 mount 之上（Starlette 按注册序
匹配，装饰器路由压过 mount），因此与 vendored 的 slx 路由共存于同一前缀。

数据来源：逻辑层 = xar.ontology.macro_links（代码即真相）；读数 = slx PIT 引擎
（严格 knowledge_time <= as_of）；事件回声 = kg_events(macro_print)。
"""
from __future__ import annotations

from datetime import date

from ..ingestion.registry import COMPANIES, SEGMENTS, TECH_ROUTES, THEMES
from ..logging import get_logger
from ..ontology.macro_links import (
    LINKS_BY_KEY,
    MACRO_LINKS,
    OVERCLAIM_LINKS,
    PLATFORM_METRICS,
    THEME_TO_METRICS,
    MacroLink,
    theme_overclaims,
)
from ..storage import db

log = get_logger("xar.andy_links")

_ROUTES = {r["id"]: r for r in TECH_ROUTES}
_HARD_ORDER = {"hard": 0, "medium": 1, "soft": 2, "wall": 3}


def _link_dict(link: MacroLink) -> dict:
    return {
        "metric_key": link.metric_key,
        "hardness": None,  # filled from slx registry when available
        "display_name_zh": link.metric_key,
        "family": None,
        "scope": link.scope,
        "good_when": link.good_when,
        "rationale_zh": link.rationale_zh,
        "segments": list(link.segments),
        "tech_routes": list(link.tech_routes),
    }


def _registry_rows() -> dict[str, dict]:
    """slx metric_registry 本体行（含水印块）。slx 不可用时返回空（勾稽 API 仍可答逻辑层）。"""
    try:
        from slx.api.deps import identification
        from slx.db import connect

        with connect() as conn:
            rows = conn.execute(
                "SELECT metric_key, display_name_zh, family, hardness, unit, "
                "identification_strategy, caveat FROM metric_registry"
            ).fetchall()
        out = {}
        for key, name_zh, family, hardness, unit, strategy, caveat in rows:
            out[key] = {
                "display_name_zh": name_zh, "family": family, "hardness": hardness,
                "unit": unit,
                "identification": identification(hardness, strategy, caveat),
            }
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("slx registry unavailable: %s", e)
        return {}


def _enrich(link: MacroLink, reg: dict[str, dict]) -> dict:
    d = _link_dict(link)
    meta = reg.get(link.metric_key)
    if meta:
        d.update({k: meta[k] for k in ("display_name_zh", "family", "hardness")})
    return d


def link_themes() -> dict:
    """全量勾稽矩阵：8 链 × 关联指标 + 登记簿断言；另附平台级指标。"""
    reg = _registry_rows()
    claim_status = _claim_statuses()
    themes = []
    for tid, meta in THEMES.items():
        links = sorted(THEME_TO_METRICS.get(tid, ()),
                       key=lambda x: (_HARD_ORDER.get((reg.get(x.metric_key) or {}).get("hardness"), 9),
                                      x.metric_key))
        themes.append({
            "theme": tid, "name": meta["name"], "name_cn": meta["nameCn"],
            "kind": meta.get("kind", "chain"),
            "metrics": [_enrich(li, reg) for li in links],
            "overclaims": [{"claim_key": c.claim_key,
                            "status": claim_status.get(c.claim_key, "open")}
                           for c in theme_overclaims(tid)],
        })
    return {"themes": themes,
            "platform_metrics": [_enrich(li, reg) for li in PLATFORM_METRICS]}


def _claim_statuses() -> dict[str, str]:
    try:
        from slx.db import connect

        with connect() as conn:
            return dict(conn.execute(
                "SELECT claim_key, status FROM overclaim_registry").fetchall())
    except Exception:  # noqa: BLE001
        return {}


def link_theme(theme: str, as_of: str | None = None) -> dict | None:
    """单链宏观面板：关联指标 + 本体/水印 + PIT 最新值/斜率/短序列 + 涉及断言。"""
    meta = THEMES.get(theme)
    if meta is None:
        return None
    asof = date.fromisoformat(as_of) if as_of else date.today()
    reg = _registry_rows()
    links = sorted(THEME_TO_METRICS.get(theme, ()),
                   key=lambda x: (_HARD_ORDER.get((reg.get(x.metric_key) or {}).get("hardness"), 9),
                                  x.metric_key))
    metrics = []
    readings: dict[str, dict] = {}
    try:
        from slx.db import connect
        from slx.engine.point_in_time import PointInTimeContext

        with connect() as conn:
            ctx = PointInTimeContext(conn, asof)
            for li in links:
                row = conn.execute(
                    "SELECT valid_time, value FROM observation "
                    "WHERE metric_key=%s AND knowledge_time <= %s AND value IS NOT NULL "
                    "ORDER BY valid_time DESC, knowledge_time DESC LIMIT 1",
                    (li.metric_key, asof)).fetchone()
                if row is None:
                    continue
                try:
                    slope = ctx.slope(li.metric_key, 4)
                except Exception:  # noqa: BLE001
                    slope = None
                readings[li.metric_key] = {
                    "valid_time": str(row[0]), "value": float(row[1]), "slope": slope,
                    "series": [{"valid_time": str(d), "value": v}
                               for d, v in ctx.series(li.metric_key, 12)],
                }
    except Exception as e:  # noqa: BLE001
        log.warning("slx PIT unavailable: %s", e)
    for li in links:
        d = _enrich(li, reg)
        m = reg.get(li.metric_key) or {}
        d["unit"] = m.get("unit")
        d["identification"] = m.get("identification")
        r = readings.get(li.metric_key) or {}
        d["value"] = r.get("value")
        d["slope"] = r.get("slope")
        d["valid_time"] = r.get("valid_time")
        d["series"] = r.get("series", [])
        metrics.append(d)
    claim_status = _claim_statuses()
    claim_texts = _claim_texts()
    overclaims = [{
        "claim_key": c.claim_key,
        "claim_text_zh": claim_texts.get(c.claim_key, c.claim_key),
        "status": claim_status.get(c.claim_key, "open"),
        "polarity_on_fixation": c.polarity_on_fixation,
        "polarity_on_falsified": c.polarity_on_falsified,
    } for c in theme_overclaims(theme)]
    return {"theme": theme, "name": meta["name"], "name_cn": meta["nameCn"],
            "as_of": str(asof), "metrics": metrics, "overclaims": overclaims}


def _claim_texts() -> dict[str, str]:
    try:
        from slx.db import connect

        with connect() as conn:
            return dict(conn.execute(
                "SELECT claim_key, claim_text_zh FROM overclaim_registry").fetchall())
    except Exception:  # noqa: BLE001
        return {}


def link_metric(metric_key: str) -> dict | None:
    """反向勾稽：指标 → 链/环节/技术路线/公司名册 + Genny 深链 + macro_print 事件回声。"""
    link = LINKS_BY_KEY.get(metric_key)
    if link is None:
        return None
    reg = _registry_rows()
    meta = reg.get(metric_key) or {}
    themes = [{"theme": t, "name": THEMES[t]["name"], "name_cn": THEMES[t]["nameCn"],
               "genny_link": "/genny"} for t in link.themes if t in THEMES]
    segments = [{"id": s, "name": SEGMENTS[s]["name"], "name_cn": SEGMENTS[s]["nameCn"],
                 "theme": SEGMENTS[s]["theme"], "genny_link": f"/genny/segment/{s}"}
                for s in link.segments if s in SEGMENTS]
    routes = [{"id": r, "name": _ROUTES[r]["name"],
               "name_cn": _ROUTES[r].get("attrs", {}).get("family", _ROUTES[r]["name"])}
              for r in link.tech_routes if r in _ROUTES]
    # 公司名册：命中关联 segment（优先）或关联 theme 的前 12 家
    seg_set, theme_set = set(link.segments), set(link.themes)
    hits, backfill = [], []
    for c in COMPANIES:
        cseg = set((c.get("seg") or {}).values())
        if cseg & seg_set:
            hits.append(c)
        elif theme_set & set(c.get("themes", ())):
            backfill.append(c)
    companies = [{
        "id": c["id"], "name": c["name"],
        "ticker": (c.get("tickers") or [None])[0],
        "theme": (c.get("themes") or [None])[0],
        "genny_link": f"/genny/company/{c['id']}",
    } for c in (hits + backfill)[:12]]
    try:
        events = db.query(
            "SELECT summary, event_date, polarity, theme FROM kg_events "
            "WHERE event_type='macro_print' AND attrs->>'metric_key' = %s "
            "AND invalidated_at IS NULL ORDER BY event_date DESC, id DESC LIMIT 10",
            (metric_key,))
        recent = [{"summary": e["summary"], "event_date": str(e["event_date"]),
                   "polarity": e["polarity"], "theme": e["theme"]} for e in events]
    except Exception:  # noqa: BLE001
        recent = []
    return {
        "metric_key": metric_key,
        "display_name_zh": meta.get("display_name_zh", metric_key),
        "hardness": meta.get("hardness"),
        "scope": link.scope, "good_when": link.good_when,
        "rationale_zh": link.rationale_zh,
        "themes": themes, "segments": segments, "tech_routes": routes,
        "companies": companies, "recent_events": recent,
    }


def sync_events(as_of: str | None = None) -> dict:
    from ..ingestion import macro_bridge

    return macro_bridge.sync(date.fromisoformat(as_of) if as_of else None)


__all__ = ["link_themes", "link_theme", "link_metric", "sync_events",
           "MACRO_LINKS", "OVERCLAIM_LINKS"]
