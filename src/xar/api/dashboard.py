"""Dashboard builder — computes the front-end's domain shapes (Segment / Company /
Signal / Regime / Decision / Catalyst / Coverage + detail bundles) directly from
the REAL database, scoped to a chain THEME (ai_optical | ai_chip | ...).

A company carries `themes[]` and a per-theme segment (companies.meta.segments);
the same name can appear in several chains with a theme-appropriate segment
(e.g. NVIDIA = optical customer AND chip GPU). Everything below is derived from
real rows; documented proxies (e.g. crowding) are computed from real signals.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from ..ingestion.registry import SEGMENTS, THEMES
from ..ontology import cycle
from ..retrieval import graphrag
from ..storage import db, structured

DEFAULT_THEME = "ai_optical"
# rough FX -> USD so market caps across listings are comparable
FX = {"US": 1.0, "CN": 0.140, "HK": 0.128, "KR": 0.00073, "JP": 0.0064,
      "EU": 1.08, "TW": 0.031, "SG": 0.74, "SE": 0.095, "GB": 1.27, "NO": 0.092}
_CJK = re.compile(r"[一-鿿]")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _split_name(full: str) -> tuple[str, str | None]:
    m = _CJK.search(full or "")
    if not m:
        return full, None
    en = full[: m.start()].strip(" ·-") or full
    return en, full[m.start():].strip()


def _us_ticker(tickers: list[str]) -> str:
    return next((t for t in (tickers or []) if "." not in t), (tickers or [""])[0])


def _company_seg(c: dict, theme: str) -> str | None:
    return ((c.get("meta") or {}).get("segments") or {}).get(theme)


def _theme_kind(theme: str) -> str:
    """'chain' (supply-chain tier axis) | 'cycle' (economic-cycle position axis)."""
    return THEMES.get(theme, {}).get("kind", "chain")


def _first_seg(theme: str) -> str:
    """A theme's first segment by tier — the safe fallback for a company whose
    per-theme segment is missing (replaces a hardcoded optical default so cycle
    themes don't borrow 'module_maker')."""
    segs = sorted((s for s, m in SEGMENTS.items() if m.get("theme") == theme),
                  key=lambda s: SEGMENTS[s].get("tier", 0))
    return segs[0] if segs else "module_maker"


def _spark(closes: list[float], n: int = 12) -> list[float]:
    closes = [c for c in closes if c is not None and math.isfinite(c)]
    if not closes:
        return []
    tail = closes[-60:]
    if len(tail) <= n:
        return [round(float(c), 2) for c in tail]
    step = (len(tail) - 1) / (n - 1)
    return [round(float(tail[round(i * step)]), 2) for i in range(n)]


def _pct(series: list[float], lookback: int) -> float:
    s = [c for c in series if c is not None and math.isfinite(c)]
    if len(s) < 2:
        return 0.0
    past = s[-(lookback + 1)] if len(s) > lookback else s[0]
    return (s[-1] / past - 1.0) * 100.0 if past else 0.0


# --- bulk loaders ----------------------------------------------------------
def _load() -> dict:
    companies = db.query(
        "SELECT id,name,aliases,tickers,region,chain_role,themes,meta FROM companies ORDER BY name"
    )
    prices: dict[str, list[float]] = {}
    for r in db.query(
        "SELECT company_id, close FROM prices WHERE close IS NOT NULL ORDER BY company_id, d"
    ):
        c = r["close"]
        if c is not None and math.isfinite(c):  # Yahoo can emit NaN bars
            prices.setdefault(r["company_id"], []).append(c)
    funds: dict[str, dict[str, float]] = {}
    for r in db.query("SELECT company_id, metric, value FROM fundamentals WHERE value IS NOT NULL"):
        v = r["value"]
        if v is not None and math.isfinite(v):
            funds.setdefault(r["company_id"], {})[r["metric"]] = v
    events: dict[str, list[dict]] = {}
    for r in db.query(
        "SELECT company_id, event_type, polarity, event_date, summary, confidence "
        "FROM kg_events WHERE invalidated_at IS NULL AND company_id IS NOT NULL "
        "ORDER BY company_id, event_date DESC NULLS LAST"
    ):
        events.setdefault(r["company_id"], []).append(r)
    return {"companies": companies, "prices": prices, "funds": funds, "events": events}


def _theme_companies(data: dict, theme: str) -> list[dict]:
    return [c for c in data["companies"] if theme in (c.get("themes") or [])]


def _est_revision(evs: list[dict]) -> float:
    recent = evs[:10]
    net = sum(
        (1 if e["polarity"] == "positive" else -1 if e["polarity"] == "negative" else 0)
        for e in recent
        if e["event_type"] in ("earnings", "capex_guidance", "order", "qualification", "product_ramp")
    )
    return _clamp(net * 22, -100, 100)


def _company_row(c: dict, data: dict, val_pctile: dict[str, float], theme: str) -> dict:
    cid = c["id"]
    series = data["prices"].get(cid, [])
    f = data["funds"].get(cid, {})
    evs = data["events"].get(cid, [])
    change_m = round(_pct(series, 21), 2)
    change_w = round(_pct(series, 5), 2)
    momentum = round(_clamp(change_m * 4, -100, 100))
    est_rev = round(_est_revision(evs))
    net_pol = sum(1 if e["polarity"] == "positive" else -1 if e["polarity"] == "negative" else 0 for e in evs[:12])
    conviction = int(_clamp(round(3 + momentum / 50 + net_pol * 0.3), 1, 5))
    seg = _company_seg(c, theme) or _first_seg(theme)
    en, cn = _split_name(c["name"])
    mcap = f.get("market_cap")
    market = c.get("region") or "US"
    sigs: list[str] = []
    for e in evs:
        if e["event_type"] not in sigs:
            sigs.append(e["event_type"])
        if len(sigs) >= 4:
            break
    return {
        "id": cid,
        "ticker": _us_ticker(c.get("tickers") or []),
        "name": en,
        "nameCn": cn,
        "segmentId": seg,
        "market": market if market in FX else "US",
        "marketCap": round((mcap or 0) * FX.get(market, 1.0) / 1e9, 1) if mcap else 0.0,
        "priceChange": change_m,
        "revGrowth": round((f.get("revenue_growth") or 0) * 100, 1),
        "grossMargin": round((f.get("gross_margin") or 0) * 100, 1),
        "estRevision": est_rev,
        "conviction": conviction,
        "watched": True,
        "signals": sigs,
        "spark": _spark(series),
        "role": SEGMENTS.get(seg, {}).get("name", seg),
        "_pe": f.get("pe_ratio"), "_ps": f.get("ps_ratio"),
        "_valPctile": round(val_pctile.get(cid, 50)), "_events": len(evs),
        "_momentum": momentum, "_changeW": change_w,
    }


def _public_company(r: dict) -> dict:
    return {k: v for k, v in r.items() if not k.startswith("_")}


def _kpi_block(company: dict, fvals: dict) -> list[dict]:
    """The company's sector-appropriate operating metrics (ARR/NRR/RPO for SaaS,
    NIM/CET1 for a bank, $/kg for a launcher…), annotated with unit + direction.
    Filters the already-loaded fundamentals map by the metric pack — no new query."""
    from ..ontology import kpis_for_company

    out = []
    for spec in kpis_for_company(company):
        if spec.key in fvals:
            out.append({"key": spec.key, "label": spec.label, "value": fvals[spec.key],
                        "unit": spec.unit, "higherIsBetter": spec.higher_is_better})
    return out


def _valuation_pctiles(companies: list[dict], funds: dict) -> dict[str, float]:
    """Percentile-rank valuation WITHIN each ratio type — PE vs PE, PS vs PS — never
    mixing the two (a PE of 30 and a PS of 30 are not comparable). A company is ranked
    on PE when available, else PS, against only its same-ratio peers."""
    pe, ps = [], []
    for c in companies:
        f = funds.get(c["id"], {})
        if f.get("pe_ratio") and f["pe_ratio"] > 0:
            pe.append((c["id"], f["pe_ratio"]))
        elif f.get("ps_ratio") and f["ps_ratio"] > 0:
            ps.append((c["id"], f["ps_ratio"]))
    out: dict[str, float] = {}
    for pool in (pe, ps):
        pool.sort(key=lambda x: x[1])
        n = len(pool)
        for i, (cid, _) in enumerate(pool):
            out[cid] = 100 * i / (n - 1) if n > 1 else 50
    return out


# --- public builders (theme-scoped) ----------------------------------------
def companies(theme: str = DEFAULT_THEME) -> list[dict]:
    data = _load()
    val = _valuation_pctiles(data["companies"], data["funds"])
    rows = [_company_row(c, data, val, theme) for c in _theme_companies(data, theme)]
    rows.sort(key=lambda r: (-r["conviction"], -r["marketCap"]))
    return [_public_company(r) for r in rows]


def _phase(momentum: float) -> str:
    if momentum >= 55:
        return "accelerating"
    if momentum >= 18:
        return "expansion"
    if momentum >= -2:
        return "peaking"
    if momentum >= -25:
        return "cooling"
    return "trough"


def _theme_segment_ids(theme: str) -> list[str]:
    ids = [s for s, m in SEGMENTS.items() if m["theme"] == theme]
    return sorted(ids, key=lambda s: SEGMENTS[s].get("tier", 0))


def _segments_internal(data: dict, val: dict, theme: str) -> list[dict]:
    rows = [_company_row(c, data, val, theme) for c in _theme_companies(data, theme)]
    by_seg: dict[str, list[dict]] = {}
    for r in rows:
        by_seg.setdefault(r["segmentId"], []).append(r)
    out: list[dict] = []
    for seg_id in _theme_segment_ids(theme):
        members = by_seg.get(seg_id, [])
        if not members:
            continue
        meta = SEGMENTS[seg_id]
        n = len(members)
        momentum = round(sum(m["_momentum"] for m in members) / n)
        change_m = round(sum(m["priceChange"] for m in members) / n, 2)
        change_w = round(sum(m["_changeW"] for m in members) / n, 2)
        est_rev = round(sum(m["estRevision"] for m in members) / n)
        val_pctile = round(sum(m["_valPctile"] for m in members) / n)
        ev_density = sum(m["_events"] for m in members)
        supply_ev = _seg_event_count(seg_id, rows, data, ("supply_constraint",))
        cap_ev = _seg_event_count(seg_id, rows, data, ("capacity_expansion", "order"))
        supply_tight = round(_clamp(40 + supply_ev * 16 + cap_ev * 6, 0, 100))
        crowding = round(_clamp(val_pctile * 0.6 + min(ev_density, 40) / 40 * 100 * 0.4, 0, 100))
        alpha = round(_clamp(50 + momentum * 0.35 + est_rev * 0.15, 0, 100))
        out.append({
            "id": seg_id, "name": meta["name"], "nameCn": meta["nameCn"], "tier": meta.get("tier", 0),
            "cycle": cycle.as_dict(meta.get("cycle")), "axis": _theme_kind(theme),
            "thesisCn": meta.get("thesisCn", ""),
            "alpha": alpha, "momentum": momentum, "changeW": change_w, "changeM": change_m,
            "valuationPctile": val_pctile, "crowding": crowding, "supplyTightness": supply_tight,
            "earningsRevision": est_rev, "companies": n, "regime": _phase(momentum),
            "spark": _avg_spark([m["spark"] for m in members]),
            "markets": sorted({m["market"] for m in members}),
            "note": f"{n} names · {ev_density} catalysts · momentum {momentum:+d}",
        })
    return out


def _seg_event_count(seg_id: str, rows: list[dict], data: dict, types: tuple[str, ...]) -> int:
    ids = {r["id"] for r in rows if r["segmentId"] == seg_id}
    return sum(1 for cid in ids for e in data["events"].get(cid, []) if e["event_type"] in types)


def _avg_spark(sparks: list[list[float]]) -> list[float]:
    sparks = [s for s in sparks if len(s) == 12]
    if not sparks:
        return []
    norm = [[v / (s[0] or 1) * 100 for v in s] for s in sparks]
    return [round(sum(col) / len(norm), 2) for col in zip(*norm)]


def segments(theme: str = DEFAULT_THEME) -> list[dict]:
    data = _load()
    val = _valuation_pctiles(data["companies"], data["funds"])
    return _segments_internal(data, val, theme)


def regime(theme: str = DEFAULT_THEME) -> dict:
    segs = segments(theme)
    if not segs:
        return {"label": "Initializing", "labelCn": "数据初始化", "phase": "peaking",
                "score": 0, "trend": 0, "breadth": 0, "drivers": [], "updatedAt": _now()}
    avg_mom = sum(s["momentum"] for s in segs) / len(segs)
    score = round(sum(s["alpha"] for s in segs) / len(segs))
    breadth = round(100 * sum(1 for s in segs if s["momentum"] > 0) / len(segs))
    trend = round(sum(s["changeM"] for s in segs) / len(segs))
    phase = _phase(avg_mom)
    label = {"accelerating": "Accelerating Up-Cycle", "expansion": "Mid-Cycle Expansion",
             "peaking": "Late-Cycle Plateau", "cooling": "Cooling", "trough": "Cycle Trough"}[phase]
    label_cn = {"accelerating": "加速上行", "expansion": "中周期扩张", "peaking": "周期高位",
                "cooling": "降温", "trough": "周期底部"}[phase]
    return {"label": label, "labelCn": label_cn, "phase": phase, "score": score,
            "trend": trend, "breadth": breadth, "drivers": _drivers(theme), "updatedAt": _now()}


def _drivers(theme: str) -> list[dict]:
    ids = [c["id"] for c in _theme_companies(_load(), theme)]
    rows = db.query(
        "SELECT event_type, SUM(CASE WHEN polarity='positive' THEN 1 ELSE 0 END) AS pos, "
        "SUM(CASE WHEN polarity='negative' THEN 1 ELSE 0 END) AS neg, COUNT(*) AS n "
        "FROM kg_events WHERE invalidated_at IS NULL AND company_id = ANY(%s) "
        "AND event_date >= (CURRENT_DATE - INTERVAL '180 days') "
        "GROUP BY event_type ORDER BY n DESC LIMIT 5",
        (ids or [""],),
    )
    labels = {"capex_guidance": "Capex guidance", "order": "New orders", "qualification": "Customer quals",
              "product_ramp": "Product ramps", "accelerator_launch": "Accelerator launches",
              "capacity_expansion": "Capacity adds", "supply_constraint": "Supply tightness",
              "earnings": "Earnings trend", "equity_investment": "Strategic stakes",
              "tech_substitution": "Tech substitution",
              # sector-agnostic core + event backbone
              "guidance_change": "Guidance change", "mna": "M&A", "partnership": "Partnerships",
              "contract_win": "Contract wins", "pricing_change": "Pricing", "management_change": "Mgmt change",
              "buyback": "Buybacks", "dividend": "Dividends", "regulatory_action": "Regulatory",
              "litigation": "Litigation", "index_inclusion": "Index changes", "short_report": "Short reports",
              "macro_print": "Macro prints", "stock_split": "Stock splits", "secondary_offering": "Equity raises"}
    out = []
    for r in rows:
        pol = "positive" if (r["pos"] or 0) > (r["neg"] or 0) else "negative" if (r["neg"] or 0) > (r["pos"] or 0) else "neutral"
        if r["event_type"] == "tech_substitution":
            pol = "negative"
        out.append({"label": labels.get(r["event_type"], r["event_type"]), "polarity": pol})
    return out


def decision(theme: str = DEFAULT_THEME) -> dict:
    segs = sorted(segments(theme), key=lambda s: -s["alpha"])
    reg_ids = {c["id"] for c in _theme_companies(_load(), theme)}
    risks_edges = [e for e in graphrag.single_source_risks()
                   if e["src_id"] in reg_ids or e["dst_id"] in reg_ids]
    top = segs[0] if segs else None
    bot = segs[-1] if segs else None
    reg = regime(theme)
    n_cat = db.query("SELECT count(*) c FROM kg_events WHERE invalidated_at IS NULL "
                     "AND company_id = ANY(%s)", (list(reg_ids) or [""],))[0]["c"]
    kind = _theme_kind(theme)
    if top and bot:
        unit = "consumer-cycle basket" if kind == "cycle" else "chain"
        lead_en = "Leading segment" if kind == "cycle" else "Strongest link"
        lag_en = "lagging segment" if kind == "cycle" else "laggard"
        tail_en = ("cycle positioning maps trade-down vs high-beta exposure."
                   if kind == "cycle" else f"{len(risks_edges)} single-source risks flagged.")
        house = (f"{THEMES[theme]['name']} {unit} in {reg['label'].lower()}. "
                 f"{lead_en}: {top['name']} (alpha {top['alpha']}, momentum {top['momentum']:+d}); "
                 f"{lag_en}: {bot['name']} (alpha {bot['alpha']}). "
                 f"{n_cat} live catalysts; {tail_en}")
        unit_cn = "消费周期组合" if kind == "cycle" else "产业链"
        lead_cn = "最强周期段" if kind == "cycle" else "最强环节"
        lag_cn = "最弱周期段" if kind == "cycle" else "最弱"
        tail_cn = ("周期定位刻画降级受益 vs 高弹性敞口。" if kind == "cycle"
                   else f"{len(risks_edges)} 处单一供应风险。")
        house_cn = (f"{THEMES[theme]['nameCn']}（{unit_cn}）处于{reg['labelCn']}。{lead_cn}：{top['nameCn'] or top['name']}"
                    f"（Alpha {top['alpha']}）；{lag_cn}：{bot['nameCn'] or bot['name']}。"
                    f"追踪 {n_cat} 条催化剂，{tail_cn}")
    else:
        house = house_cn = "Awaiting data."
    opportunities = [{"id": f"op_{s['id']}", "title": f"{s['name']} — momentum {s['momentum']:+d}",
                      "detail": s["note"], "segmentId": s["id"], "score": s["alpha"]} for s in segs[:3]]
    rich = sorted(segs, key=lambda s: -s["valuationPctile"])[:1]
    risks = []
    if rich:
        r = rich[0]
        risks.append({"id": "rk_val", "title": f"{r['name']} valuation stretched",
                      "detail": f"Valuation percentile {r['valuationPctile']} with crowding {r['crowding']}.",
                      "severity": "high" if r["valuationPctile"] > 75 else "medium"})
    for e in risks_edges[:2]:
        risks.append({"id": f"rk_{e['id']}", "title": f"Single-source risk: {e.get('src_name', '?')}",
                      "detail": f"{e.get('src_name', '?')} → {e.get('dst_name', '?')} flagged single-source.",
                      "severity": "medium"})
    if not risks:
        if kind == "cycle":
            risks.append({"id": "rk_gen", "title": "Consumer-cycle sensitivity",
                          "detail": "Basket spans early-cycle high-beta to counter-cyclical trade-down names; "
                                    "rotation risk as the consumer cycle turns.", "severity": "medium"})
        else:
            risks.append({"id": "rk_gen", "title": "Cyclicality / demand concentration",
                          "detail": "Chain levered to a narrow set of AI-capex demand drivers.", "severity": "medium"})
    cats = catalysts(theme)[:4]
    kindmap = {"earnings": "review", "order": "add", "tech_substitution": "rerate",
               "supply_constraint": "review", "capex_guidance": "review"}
    actions = [{"id": f"ac_{c['id']}", "label": f"Review {c.get('ticker') or ''} {c['title'][:48]}".strip(),
                "kind": kindmap.get(c["type"], "review"), "ticker": c.get("ticker"), "done": False}
               for c in cats]
    return {"houseView": house, "houseViewCn": house_cn, "opportunities": opportunities,
            "risks": risks[:3], "actions": actions}


def _company_segment_index(theme: str) -> dict[str, str]:
    out = {}
    for c in db.query("SELECT id, meta FROM companies"):
        seg = _company_seg(c, theme)
        if seg:
            out[c["id"]] = seg
    return out


def _company_ticker_index() -> dict[str, str]:
    return {c["id"]: _us_ticker(c["tickers"]) for c in db.query("SELECT id, tickers FROM companies")}


def _theme_ids(theme: str) -> list[str]:
    return [c["id"] for c in db.query("SELECT id, themes FROM companies") if theme in (c["themes"] or [])]


def signals(theme: str = DEFAULT_THEME, limit: int = 60) -> list[dict]:
    comp_index = _company_segment_index(theme)
    tick_index = _company_ticker_index()
    rows = db.query(
        "SELECT v.id, v.company_id, v.event_type, v.polarity, v.event_date, v.observed_at, "
        "v.summary, v.magnitude, v.confidence, v.license_tag, d.source AS doc_source "
        "FROM kg_events v LEFT JOIN documents d ON d.id=v.source_doc_id "
        "WHERE v.invalidated_at IS NULL AND v.company_id = ANY(%s) "
        "ORDER BY COALESCE(v.event_date, v.observed_at::date) DESC, v.id DESC LIMIT %s",
        (_theme_ids(theme) or [""], limit),
    )
    return [_signal_shape(r, comp_index, tick_index) for r in rows]


def _signal_shape(r: dict, comp_index: dict, tick_index: dict) -> dict:
    summary = (r["summary"] or "").lower()
    if r["doc_source"] in ("wechat", "social"):
        source = "wechat"
    elif r["doc_source"] in ("news",):
        source = "news"
    elif r["license_tag"] == "signal":
        source = ("prediction_market" if "prediction market" in summary else
                  "insider" if "insider" in summary else "estimate")
    else:
        source = "filing"
    ts = r["observed_at"] or r["event_date"]
    return {"id": f"ev{r['id']}", "type": r["event_type"], "polarity": r["polarity"] or "neutral",
            "source": source, "companyId": r["company_id"], "ticker": tick_index.get(r["company_id"]),
            "segmentId": comp_index.get(r["company_id"]), "title": r["summary"] or r["event_type"],
            "magnitude": r["magnitude"],
            "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "confidence": float(r["confidence"] or 0.6)}


def catalysts(theme: str = DEFAULT_THEME, limit: int = 18) -> list[dict]:
    comp_index = _company_segment_index(theme)
    tick_index = _company_ticker_index()
    rows = db.query(
        "SELECT id, company_id, event_type, polarity, event_date, summary, confidence "
        "FROM kg_events WHERE invalidated_at IS NULL AND event_date IS NOT NULL "
        "AND company_id = ANY(%s) ORDER BY event_date DESC LIMIT %s",
        (_theme_ids(theme) or [""], limit),
    )
    out = []
    for r in rows:
        conf = float(r["confidence"] or 0.6)
        out.append({"id": f"cat{r['id']}",
                    "date": r["event_date"].isoformat() if hasattr(r["event_date"], "isoformat") else str(r["event_date"]),
                    "type": r["event_type"], "polarity": r["polarity"] or "neutral",
                    "title": r["summary"] or r["event_type"], "ticker": tick_index.get(r["company_id"]),
                    "segmentId": comp_index.get(r["company_id"]),
                    "importance": 3 if conf >= 0.8 else 2 if conf >= 0.6 else 1})
    return out


def calendar(theme: str = DEFAULT_THEME, days: int = 90, limit: int = 40) -> list[dict]:
    """Forward scheduled events for the theme's companies (earnings, launches,
    conferences, …) — the 'what's coming' rail, distinct from past catalysts."""
    comp_index = _company_segment_index(theme)
    tick_index = _company_ticker_index()
    rows = structured.upcoming_calendar(_theme_ids(theme), days=days, limit=limit)
    out = []
    for r in rows:
        sd, we = r["scheduled_for"], r.get("window_end")
        out.append({
            "id": f"cal{r['id']}",
            "date": sd.isoformat() if hasattr(sd, "isoformat") else str(sd),
            "windowEnd": we.isoformat() if we is not None and hasattr(we, "isoformat") else None,
            "type": r["event_type"], "status": r["status"],
            "title": r["title"] or r["event_type"], "source": r["source"],
            "ticker": tick_index.get(r["company_id"]), "companyId": r["company_id"],
            "segmentId": comp_index.get(r["company_id"]), "importance": r["importance"],
        })
    return out


def landscape(theme: str = DEFAULT_THEME) -> dict:
    """Industry structure (行业格局) per chain segment: market-cap share of each
    player and the segment's HHI concentration. Share is FX-normalized market-cap
    based (a documented proxy); explicit revenue `market_share` fundamentals, when
    present, are surfaced alongside."""
    data = _load()
    val = _valuation_pctiles(data["companies"], data["funds"])
    rows = [_company_row(c, data, val, theme) for c in _theme_companies(data, theme)]
    by_seg: dict[str, list[dict]] = {}
    for r in rows:
        by_seg.setdefault(r["segmentId"], []).append(r)
    out: list[dict] = []
    for seg_id in _theme_segment_ids(theme):
        members = by_seg.get(seg_id, [])
        if not members:
            continue
        total = sum(m["marketCap"] for m in members) or 0.0
        players = []
        for m in sorted(members, key=lambda x: -x["marketCap"]):
            share = (m["marketCap"] / total) if total else 0.0
            ex_share = data["funds"].get(m["id"], {}).get("market_share")
            players.append({"id": m["id"], "name": m["name"], "ticker": m["ticker"],
                            "marketCap": m["marketCap"], "shareOfSegment": round(share, 4),
                            "reportedShare": round(ex_share, 4) if ex_share is not None else None})
        hhi = round(sum((p["shareOfSegment"] * 100) ** 2 for p in players)) if total else 0
        meta = SEGMENTS[seg_id]
        out.append({"id": seg_id, "name": meta["name"], "nameCn": meta["nameCn"],
                    "tier": meta.get("tier", 0), "cycle": cycle.as_dict(meta.get("cycle")),
                    "companies": len(members), "hhi": hhi,
                    "concentration": "concentrated" if hhi >= 2500 else "moderate" if hhi >= 1500 else "fragmented",
                    "topPlayers": players[:8]})
    return {"theme": theme, "shareBasis": "fx_market_cap", "segments": out, "updatedAt": _now()}


def coverage(theme: str = DEFAULT_THEME) -> dict:
    allc = db.query("SELECT themes FROM companies")
    themes = []
    for tid, tinfo in THEMES.items():
        cnt = sum(1 for c in allc if tid in (c["themes"] or []))
        segc = sum(1 for s in SEGMENTS.values() if s["theme"] == tid)
        themes.append({"id": tid, "name": tinfo["name"], "nameCn": tinfo["nameCn"],
                       "active": True, "kind": tinfo.get("kind", "chain"),
                       "segmentCount": segc, "companyCount": cnt})
    cur = next((t for t in themes if t["id"] == theme), None)
    return {"themes": themes, "companyCount": cur["companyCount"] if cur else 0,
            "segmentCount": cur["segmentCount"] if cur else 0, "theme": theme, "updatedAt": _now()}


def overview(theme: str = DEFAULT_THEME) -> dict:
    return {"regime": regime(theme), "segments": segments(theme),
            "decision": decision(theme), "coverage": coverage(theme)}


# --- detail bundles --------------------------------------------------------
def company_detail(cid: str, theme: str | None = None) -> dict | None:
    data = _load()
    c = next((x for x in data["companies"] if x["id"] == cid), None)
    if not c:
        return None
    theme = theme or (c.get("themes") or [DEFAULT_THEME])[0]
    val = _valuation_pctiles(data["companies"], data["funds"])
    company = _public_company(_company_row(c, data, val, theme))
    seg_id = company["segmentId"]
    series = db.query("SELECT d, close FROM prices WHERE company_id=%s ORDER BY d", (cid,))
    prices = [{"d": r["d"].isoformat(), "close": round(r["close"], 2)}
              for r in series if r["close"] is not None][-180:]
    funds = db.query("SELECT metric, value, unit FROM fundamentals WHERE company_id=%s ORDER BY metric", (cid,))
    comp_index = _company_segment_index(theme)
    tick_index = _company_ticker_index()
    sig_rows = db.query(
        "SELECT v.id, v.company_id, v.event_type, v.polarity, v.event_date, v.observed_at, "
        "v.summary, v.magnitude, v.confidence, v.license_tag, d.source AS doc_source "
        "FROM kg_events v LEFT JOIN documents d ON d.id=v.source_doc_id "
        "WHERE v.invalidated_at IS NULL AND v.company_id=%s "
        "ORDER BY COALESCE(v.event_date, v.observed_at::date) DESC LIMIT 40", (cid,))
    sigs = [_signal_shape(r, comp_index, tick_index) for r in sig_rows]
    sc = graphrag.supply_chain(cid)
    supply_chain = {
        "suppliers": [_edge_lite(e, "src") for e in sc["suppliers"]],
        "customers": [_edge_lite(e, "dst") for e in sc["customers"]],
        "invests_in": [_edge_lite(e, "other", cid) for e in sc["invests_in"]],
        "tech_routes": [_edge_lite(e, "dst") for e in sc["tech_routes"]],
        "single_source_risks": [{"src": e.get("src_name"), "dst": e.get("dst_name")} for e in sc["single_source_risks"]],
    }
    smeta = SEGMENTS.get(seg_id, {})
    return {"company": company,
            "segment": {"id": seg_id, "name": smeta.get("name", seg_id), "nameCn": smeta.get("nameCn", "")},
            "industry": (c.get("meta") or {}).get("industry"),
            "sector": (c.get("meta") or {}).get("sector"),
            "cycle": cycle.cycle_of_company(c),
            "kpis": _kpi_block(c, data["funds"].get(cid, {})),
            "prices": prices, "fundamentals": [dict(f) for f in funds], "signals": sigs,
            "supplyChain": supply_chain, "landscape": graphrag.landscape(cid)}


def _edge_lite(e: dict, side: str, self_id: str | None = None) -> dict:
    if side == "other":
        name = e["dst_name"] if e["src_id"] == self_id else e["src_name"]
        nid = e["dst_id"] if e["src_id"] == self_id else e["src_id"]
    else:
        name, nid = e[f"{side}_name"], e[f"{side}_id"]
    return {"id": nid, "name": name, "rel": e["rel_type"], "confidence": round(e.get("confidence") or 0.7, 2)}


def segment_detail(sid: str) -> dict | None:
    meta = SEGMENTS.get(sid)
    if not meta:
        return None
    theme = meta["theme"]
    data = _load()
    val = _valuation_pctiles(data["companies"], data["funds"])
    seg = next((s for s in _segments_internal(data, val, theme) if s["id"] == sid), None)
    if not seg:
        seg = {"id": sid, "name": meta["name"], "nameCn": meta["nameCn"], "tier": meta.get("tier", 0),
               "cycle": cycle.as_dict(meta.get("cycle")), "axis": _theme_kind(theme),
               "thesisCn": meta.get("thesisCn", ""),
               "alpha": 0, "momentum": 0, "changeW": 0, "changeM": 0, "valuationPctile": 0,
               "crowding": 0, "supplyTightness": 0, "earningsRevision": 0, "companies": 0,
               "regime": "peaking", "spark": [], "markets": [], "note": "no data yet"}
    rows = [_company_row(c, data, val, theme) for c in _theme_companies(data, theme)]
    members = [_public_company(r) for r in rows if r["segmentId"] == sid]
    members.sort(key=lambda r: (-r["conviction"], -r["marketCap"]))
    comp_index = _company_segment_index(theme)
    tick_index = _company_ticker_index()
    member_ids = [m["id"] for m in members] or [""]
    sig_rows = db.query(
        "SELECT v.id, v.company_id, v.event_type, v.polarity, v.event_date, v.observed_at, "
        "v.summary, v.magnitude, v.confidence, v.license_tag, d.source AS doc_source "
        "FROM kg_events v LEFT JOIN documents d ON d.id=v.source_doc_id "
        "WHERE v.invalidated_at IS NULL AND v.company_id = ANY(%s) "
        "ORDER BY COALESCE(v.event_date, v.observed_at::date) DESC LIMIT 40", (member_ids,))
    sigs = [_signal_shape(r, comp_index, tick_index) for r in sig_rows]
    return {"segment": seg, "companies": members, "signals": sigs}
