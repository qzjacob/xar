"""Phanny 整本 book 编排:build 全部 → 组合正态门 → 不达标 REDEBATE 离群名(补数据,**不重标**)
→ convergence_integrity 反作弊守卫 → 确定性 sizing(组合 gross_cap)→ 入库。

正态是"证据真实分化"的副产品:REDEBATE 只对低信息名补数据/重跑辩论(其 conviction 只能来自新辩论
输出),**绝不为凑钟形统一压低 conviction**;仍不达标 → 诚实标 calibration_incomplete,不造假。
"""
from __future__ import annotations

from ..config import get_settings
from ..logging import get_logger

log = get_logger("xar.phanny.book")


def _ensemble(convs: list[float]) -> dict:
    from . import distribution as dist
    s = get_settings()
    return dist.ensemble_normality(convs, mean_lo=s.phanny_ensemble_mean_lo, mean_hi=s.phanny_ensemble_mean_hi,
                                   std_floor=s.phanny_ensemble_sigma_min, high_ratio_floor=s.phanny_ensemble_high_ratio)


def _off_curve(plans: dict, limit: int = 3) -> list[str]:
    """离群/低信息名:锚最少者优先(补数据最可能使其真实分化),取前 limit 个。"""
    items = sorted(plans.items(), key=lambda kv: kv[1]["final_anchors"])
    return [cid for cid, _ in items[:limit]]


def run_book(company_ids: list[str] | None = None, *, force: bool = False,
             run_id: str | None = None, store: bool = True) -> dict:
    from ..ingestion.registry import company_by_id
    from ..ontology.phanny_events import phanny_universe
    from . import distribution as dist, engine, sizing as sizing_mod

    s = get_settings()
    if company_ids is None:
        company_ids = [c["id"] for c in phanny_universe()]

    # 1. build 全部(串行;每名 propose + 多 critic 辩论)
    plans: dict = {}
    skipped: list[dict] = []
    for cid in company_ids:
        r = engine.build_one(cid, force=force, run_id=run_id)
        if r.get("status") == "converged":
            plans[cid] = r
        else:
            skipped.append({"company_id": cid, "status": r.get("status"), "reason": r.get("reason")})
    if not plans:
        return {"status": "no_data", "n": 0, "plans": {}, "skipped": skipped,
                "distribution": _ensemble([])}

    # 2. 组合正态门 + REDEBATE(补数据,非重标)
    passes = 0
    en = _ensemble([p["final_conviction"] for p in plans.values()])
    while (not en["ok"]) and en.get("reason") != "insufficient_sample" and passes < s.phanny_max_book_passes:
        passes += 1
        off = _off_curve(plans)
        log.info("phanny book pass %d: ensemble off (%s) → redebate %s", passes, en["reason"], off)
        for cid in off:
            r = engine.build_one(cid, force=True, run_id=run_id)   # 补数据重跑;conviction 只来自新辩论
            if r.get("status") == "converged":
                plans[cid] = r
        en = _ensemble([p["final_conviction"] for p in plans.values()])

    # 3. 反作弊守卫:批非正态时禁"降分凑收敛"
    traces = [{"company_id": cid, "round1_conviction": p["round1_conviction"], "final_conviction": p["final_conviction"],
               "round1_anchors": p["round1_anchors"], "final_anchors": p["final_anchors"]}
              for cid, p in plans.items()]
    integrity = dist.convergence_integrity(traces, en["ok"])
    status = ("normal" if en["ok"] else
              ("insufficient_sample" if en.get("reason") == "insufficient_sample" else "calibration_incomplete"))

    # 4. 确定性 sizing(单名公式 → 组合 gross_cap 缩放,不动 conviction)
    size_rows = []
    for cid, p in plans.items():
        prop = p["proposal"]
        asym = 1.2 if prop.asymmetry_zh.strip() else 1.0
        size, rationale = sizing_mod.name_size(float(prop.conviction), asymmetry=asym,
                                               implied_move=p["dossier"].get("implied_move"))
        p["size_pct"], p["size_rationale"] = size, rationale
        c = company_by_id(cid) or {}
        size_rows.append({"company_id": cid, "direction": prop.direction, "size_pct": size,
                          "theme": (c.get("themes") or ["?"])[0]})
    pf = sizing_mod.apply_portfolio(size_rows, gross_cap=s.phanny_gross_cap_pct)
    sz_by = {r["company_id"]: r["size_pct"] for r in pf["sizes"]}
    for cid, p in plans.items():
        p["size_pct"] = sz_by.get(cid, p["size_pct"])

    # 5. 入库
    stored: list[dict] = []
    if store:
        for cid, p in plans.items():
            stored.append(engine._store(cid, p, status, run_id=run_id, force=force))

    convs = [p["final_conviction"] for p in plans.values()]
    return {"status": status, "passes": passes, "integrity_violations": integrity, "n": len(plans),
            "distribution": {**en, "histogram": dist.histogram(convs)}, "portfolio": pf,
            "skipped": skipped, "stored": stored,
            "plans": {cid: _summary(cid, p) for cid, p in plans.items()}}


def _summary(cid: str, p: dict) -> dict:
    prop = p["proposal"]
    return {"company_id": cid, "event_date": p["dossier"]["event_date"], "direction": prop.direction,
            "conviction": float(prop.conviction), "size_pct": p.get("size_pct"),
            "converged": p.get("converged"), "rounds": p.get("rounds"), "models": p.get("models"),
            "e_return_pct": prop.e_return_pct, "asymmetry_zh": prop.asymmetry_zh,
            "dimensions": [{"key": d.key, "score": d.score} for d in prop.dimensions]}


def portfolio() -> dict:
    """当前 book 最新一版每名裁决 + 组合分布(前端/API/CLI 读)。"""
    from ..ontology.phanny_events import PHANNY_UNIVERSE
    from ..storage import db, structured
    from . import distribution as dist

    s = get_settings()
    rows = structured.upcoming_calendar(list(PHANNY_UNIVERSE), days=s.phanny_watch_days, limit=100)
    trades, convs = [], []
    for r in rows:
        if r.get("event_type") != "earnings":
            continue
        cid, ed = r["company_id"], r["scheduled_for"]
        v = db.query("SELECT direction, conviction, size_pct, ensemble_status, version, content "
                     "FROM phanny_verdicts WHERE company_id=%s AND event_date=%s ORDER BY version DESC LIMIT 1",
                     (cid, ed))
        if not v:
            trades.append({"company_id": cid, "event_date": str(ed), "verdict": None})
            continue
        row = v[0]
        convs.append(float(row["conviction"]))
        trades.append({"company_id": cid, "event_date": str(ed), "direction": row["direction"],
                       "conviction": float(row["conviction"]), "size_pct": row["size_pct"],
                       "ensemble_status": row["ensemble_status"], "version": row["version"]})
    return {"trades": trades, "n": len(convs), "distribution": _ensemble(convs),
            "histogram": dist.histogram(convs)}
