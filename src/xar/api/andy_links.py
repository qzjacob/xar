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
    TRANSMISSIONS_BY_FROM,
    TRANSMISSIONS_BY_TO,
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


def _theme_sort_key(reg: dict[str, dict]):
    """链面板排序：chain 定向指标优先、platform 殿后（防宏观外环挤掉硅基指标——
    compact_theme_macro 只取前 8 条),再按 hardness、key。"""
    def key(x: MacroLink):
        return (0 if x.scope == "chain" else 1,
                _HARD_ORDER.get((reg.get(x.metric_key) or {}).get("hardness"), 9),
                x.metric_key)
    return key


def link_themes() -> dict:
    """全量勾稽矩阵：8 链 × 关联指标 + 登记簿断言；另附平台级指标。"""
    reg = _registry_rows()
    claim_status = _claim_statuses()
    themes = []
    for tid, meta in THEMES.items():
        links = sorted(THEME_TO_METRICS.get(tid, ()), key=_theme_sort_key(reg))
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
    links = sorted(THEME_TO_METRICS.get(theme, ()), key=_theme_sort_key(reg))
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
        # 宏观传导链(AM):该指标的上下游传导边——Chathy metric 模式随 link_metric 自动获得
        "transmissions": {
            "upstream": [_edge_dict(t) for t in TRANSMISSIONS_BY_TO.get(metric_key, ())],
            "downstream": [_edge_dict(t) for t in TRANSMISSIONS_BY_FROM.get(metric_key, ())],
        },
    }


# ── 宏观传导链（AM 波次）───────────────────────────────────────────────────────
def _edge_dict(t) -> dict:
    return {"from": t.from_key, "to": t.to_key, "sign": t.sign,
            "lag_hint": t.lag_hint, "rationale_zh": t.rationale_zh}


def _chain_node(key: str, reg: dict[str, dict], readings: dict) -> dict:
    """链上一个端点的展示体:metric / theme:{id} / flow:risk_on 三形态。"""
    if key.startswith("theme:"):
        tid = key.removeprefix("theme:")
        meta = THEMES.get(tid) or {}
        return {"kind": "theme", "key": key, "name_cn": meta.get("nameCn", tid),
                "link": "/genny"}
    if key.startswith("flow:"):
        return {"kind": "flow", "key": key, "name_cn": "资金流 risk-on 综合分",
                "link": "/andy/flow"}
    m = reg.get(key) or {}
    r = readings.get(key) or {}
    return {"kind": "metric", "key": key,
            "name_cn": m.get("display_name_zh", key), "family": m.get("family"),
            "hardness": m.get("hardness"), "unit": m.get("unit"),
            "value": r.get("value"), "valid_time": r.get("valid_time"),
            "link": f"/andy/metrics/{key}"}


def link_chain(metric_key: str, as_of: str | None = None, depth: int = 3) -> dict | None:
    """传导链展开:自 metric_key 沿 TRANSMISSIONS 下游 BFS(≤depth 跳)+ 上游 1 跳。
    节点 enrich 注册行 + as_of 视角最新 PIT 读数;theme:/flow: 哨兵端点给深链。"""
    if metric_key not in LINKS_BY_KEY:
        return None
    asof = date.fromisoformat(as_of) if as_of else date.today()
    # 下游 BFS
    edges: list = []
    seen = {metric_key}
    frontier = [metric_key]
    for _hop in range(max(1, depth)):
        nxt: list[str] = []
        for k in frontier:
            for t in TRANSMISSIONS_BY_FROM.get(k, ()):
                edges.append(t)
                if t.to_key not in seen:
                    seen.add(t.to_key)
                    if not t.to_key.startswith(("theme:", "flow:")):
                        nxt.append(t.to_key)
        frontier = nxt
        if not frontier:
            break
    upstream = list(TRANSMISSIONS_BY_TO.get(metric_key, ()))
    for t in upstream:
        seen.add(t.from_key)
    # 节点 enrich(单查询取全部读数;slx 不可用则读数留空)
    metric_keys = [k for k in seen if not k.startswith(("theme:", "flow:"))]
    reg = _registry_rows()
    readings: dict[str, dict] = {}
    try:
        from datetime import datetime, timedelta, timezone

        from slx.db import connect

        # knowledge_time 是 timestamptz:次日 0 点 UTC 排他上界,免裸 date 被会话时区
        # cast 到当日 0 点、漏掉"当日可知"的行(MF 波次同款边界)。
        _nxt = asof + timedelta(days=1)
        pit = datetime(_nxt.year, _nxt.month, _nxt.day, tzinfo=timezone.utc)
        with connect() as conn:
            for k in metric_keys:
                row = conn.execute(
                    "SELECT valid_time, value FROM observation "
                    "WHERE metric_key=%s AND knowledge_time < %s AND value IS NOT NULL "
                    "ORDER BY valid_time DESC, knowledge_time DESC LIMIT 1", (k, pit)).fetchone()
                if row:
                    readings[k] = {"valid_time": str(row[0]), "value": float(row[1])}
    except Exception as e:  # noqa: BLE001
        log.warning("slx PIT unavailable for chain: %s", e)
    return {
        "root": metric_key, "as_of": str(asof), "depth": depth,
        "nodes": {k: _chain_node(k, reg, readings) for k in seen},
        "upstream": [_edge_dict(t) for t in upstream],
        "downstream": [_edge_dict(t) for t in edges],
    }


def sync_events(as_of: str | None = None) -> dict:
    from ..ingestion import macro_bridge

    return macro_bridge.sync(date.fromisoformat(as_of) if as_of else None)


# ── 数据源状态(XAR 原生;不改动 vendored slx)────────────────────────────────
# 连接器 → 所需 key 的 env 名元组(布尔展示,绝不回传值)。None = 零 key 可跑。
# 注意与 slx 连接器的实际 gate 对齐:ember 源 gate 在 EMBER_API_KEY(非 EIA),
# acled 需要 KEY+EMAIL 两者(iea_eia_ember.py / acled.py)。
_SOURCE_KEY_ENV: dict[str, tuple[str, ...] | None] = {
    "fred": ("FRED_API_KEY",), "bea": ("BEA_API_KEY",), "eia": ("EIA_API_KEY",),
    "ember": ("EMBER_API_KEY",), "iea": ("EIA_API_KEY",),
    "acled": ("ACLED_API_KEY", "ACLED_EMAIL"),
    "ticketmaster": ("TICKETMASTER_API_KEY",),
    "sec_edgar": None, "epoch_ai": None, "fhfa": None, "lbnl": None,
    "indeed_hiring_lab": None, "bls": None, "stooq": None, "oecd_tax": None,
    "oecd_ai": None, "doj_ftc": None, "vdem": None, "tsmc": None,
    "cleveland_fed": None, "seed": None, "identification": None,
}


def sources_status() -> dict:
    """Andy 数据源面板:每连接器最近运行 + 每指标观测新鲜度 + key 就绪(仅布尔)。"""
    import os

    from slx.db import connect
    from slx.ingestion.discovery import discover_connectors

    with connect() as conn:
        last_runs = {r[0]: {"status": r[1], "started_at": str(r[2]),
                            "finished_at": str(r[3]) if r[3] else None,
                            "rows_written": r[4], "error": (r[5] or "")[:200]}
                     for r in conn.execute(
                         "SELECT DISTINCT ON (source_id) source_id, status, started_at, "
                         "finished_at, rows_written, error FROM audit_log "
                         "ORDER BY source_id, started_at DESC").fetchall()}
        obs_by_source = dict(conn.execute(
            "SELECT source_id, count(*) FROM observation GROUP BY 1").fetchall())
        metric_sources: dict[str, list[str]] = {}
        for mk, sid in conn.execute(
                "SELECT DISTINCT metric_key, source_id FROM metric_source").fetchall():
            metric_sources.setdefault(sid, []).append(mk)
        freshness = [{
            "metric_key": r[0], "display_name_zh": r[1], "hardness": r[2],
            "observations": int(r[3] or 0),
            "latest_valid_time": str(r[4]) if r[4] else None,
            "latest_knowledge_time": str(r[5]) if r[5] else None,
        } for r in conn.execute(
            "SELECT m.metric_key, m.display_name_zh, m.hardness, count(o.value), "
            "max(o.valid_time)::date, max(o.knowledge_time)::date "
            "FROM metric_registry m LEFT JOIN observation o USING (metric_key) "
            "WHERE m.is_quantifiable GROUP BY 1,2,3 ORDER BY count(o.value) DESC, m.metric_key"
        ).fetchall()]

    connectors = []
    for sid, (_cls, is_primary) in sorted(discover_connectors().items()):
        envs = _SOURCE_KEY_ENV.get(sid)
        connectors.append({
            "source_id": sid, "is_primary": is_primary,
            "key_env": "+".join(envs) if envs else None,
            "key_present": all(os.environ.get(x) for x in envs) if envs else True,
            "last_run": last_runs.get(sid),
            "observations": int(obs_by_source.get(sid, 0)),
            "metrics": sorted(metric_sources.get(sid, [])),
        })
    # audit_log 里可能有发现表之外的源(seed/identification 派生)
    for sid in sorted(set(last_runs) | set(obs_by_source)):
        if not any(c["source_id"] == sid for c in connectors):
            connectors.append({
                "source_id": sid, "is_primary": True, "key_env": None, "key_present": True,
                "last_run": last_runs.get(sid),
                "observations": int(obs_by_source.get(sid, 0)),
                "metrics": sorted(metric_sources.get(sid, [])),
            })
    return {"connectors": connectors, "metrics_freshness": freshness}


__all__ = ["link_themes", "link_theme", "link_metric", "link_chain", "sync_events",
           "MACRO_LINKS", "OVERCLAIM_LINKS"]
