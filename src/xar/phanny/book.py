"""Phanny 整本 book 编排:build 全部 → 组合正态门 → 不达标 REDEBATE 离群名(补数据,**不重标**)
→ convergence_integrity 反作弊守卫 → 确定性 sizing(组合 gross_cap)→ 入库。

正态是"证据真实分化"的副产品:REDEBATE 只对低信息名补数据/重跑辩论(其 conviction 只能来自新辩论
输出),**绝不为凑钟形统一压低 conviction**;仍不达标 → 诚实标 calibration_incomplete,不造假。
"""
from __future__ import annotations

import time

from ..config import get_settings
from ..logging import get_logger
from ..storage import buildlog

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


def _book_run_start(run_id: str | None, origin: str) -> int | None:
    """开一条 book 运行记录(never-raise)。返回行 id 供收尾更新。"""
    from ..storage import db
    try:
        rows = db.query("INSERT INTO phanny_book_runs(run_id, origin, status) "
                        "VALUES(%s,%s,'running') RETURNING id", (run_id, origin))
        return rows[0]["id"] if rows else None
    except Exception as e:  # noqa: BLE001
        log.warning("book run start failed: %s", str(e)[:120])
        return None


def _book_run_finish(bid: int | None, out: dict) -> None:
    """收尾:整份返回值入库(分布/组合/跳过原因全留),不再只剩一行被截断的日志。"""
    if not bid:
        return
    import json as _json

    from ..storage import db
    try:
        payload = {k: v for k, v in out.items() if k != "plans"}   # plans 含 pydantic 对象,略去
        db.execute("UPDATE phanny_book_runs SET status=%s, passes=%s, n=%s, result=%s::jsonb, "
                   "finished_at=now() WHERE id=%s",
                   (out.get("status"), out.get("passes"), out.get("n"),
                    _json.dumps(payload, ensure_ascii=False, default=str), bid))
    except Exception as e:  # noqa: BLE001
        log.warning("book run finish failed: %s", str(e)[:120])


def run_book(company_ids: list[str] | None = None, *, force: bool = False,
             run_id: str | None = None, store: bool = True, origin: str = "?",
             max_seconds: int | None = None) -> dict:
    """max_seconds:整本 book 的**墙钟预算**(None=不限,给 CLI/单测保留旧语义)。

    为什么需要:单名完整辩论 = N critic × max_rounds 轮,而订阅模型实测均值 49~124 秒/次
    (glm-5.2 101s、k3 124s、minimax 49s),一名 30 分钟起步;book 又串行跑 12 名。这本身没问题,
    但 glm_worker.run_once 是**单线程**,phanny 排在最后一个阶段(拉取排第一),所以 phanny
    超时多久、下一轮拉取就冻结多久 —— 2026-07-29 实测冻结 3.5 小时、全库零新文档。
    到点后不再开新名(**已开的那名跑完**,避免半截辩论),其余按既有 cap 的同一套语义记
    buildlog 顺延,下轮继续(裁决幂等加锁,不会重做)。"""
    from ..ingestion.registry import company_by_id
    from ..ontology.phanny_events import phanny_universe
    from . import distribution as dist, engine, sizing as sizing_mod

    s = get_settings()
    book_run = _book_run_start(run_id, origin)
    if company_ids is None:
        company_ids = [c["id"] for c in phanny_universe()]

    t0 = time.monotonic()

    def _over_budget() -> bool:
        return max_seconds is not None and (time.monotonic() - t0) >= max_seconds

    # 1. build 全部(串行;每名 propose + 多 critic 辩论)
    plans: dict = {}
    skipped: list[dict] = []
    timed_out: list[str] = []
    for i, cid in enumerate(company_ids):
        if _over_budget():
            timed_out = list(company_ids[i:])
            log.warning("phanny book: 墙钟预算 %ds 用尽,%d 名顺延下轮(已完成 %d)",
                        max_seconds, len(timed_out), len(plans))
            for tcid in timed_out:
                buildlog.record("phanny", tcid, stage="book", status="skipped",
                                reason=f"book 墙钟预算 {max_seconds}s 用尽 — 顺延下一轮",
                                run_id=run_id)
            break
        try:
            r = engine.build_one(cid, force=force, run_id=run_id)
        except Exception as e:  # noqa: BLE001 — 单名任何异常隔离(propose/debate/store 兜底),不炸整批
            log.warning("phanny book %s crashed: %s", cid, str(e)[:120])
            buildlog.record("phanny", cid, stage="book", status="error",
                            reason=f"{type(e).__name__}: {str(e)[:400]}", run_id=run_id)
            skipped.append({"company_id": cid, "status": "error", "reason": str(e)[:160]})
            continue
        if r.get("status") == "converged":
            plans[cid] = r
        else:
            skipped.append({"company_id": cid, "status": r.get("status"), "reason": r.get("reason")})
    if not plans:
        out = {"status": "no_data", "n": 0, "plans": {}, "skipped": skipped,
               **({"timed_out": len(timed_out)} if timed_out else {}),
               "distribution": _ensemble([])}
        _book_run_finish(book_run, out)
        return out

    # 2. 组合正态门 + REDEBATE(补数据,非重标)
    passes = 0
    en = _ensemble([p["final_conviction"] for p in plans.values()])
    while ((not en["ok"]) and en.get("reason") != "insufficient_sample"
           and passes < s.phanny_max_book_passes and not _over_budget()):
        passes += 1
        off = _off_curve(plans)
        log.info("phanny book pass %d: ensemble off (%s) → redebate %s", passes, en["reason"], off)
        for cid in off:
            if _over_budget():        # REDEBATE 同样吃预算 —— 否则墙钟闸只挡住阶段 1,形同虚设
                log.warning("phanny book: 预算用尽,REDEBATE pass %d 提前收尾", passes)
                break
            r = engine.build_one(cid, force=True, run_id=run_id)   # 补数据重跑;conviction 只来自新辩论
            if r.get("status") == "converged":
                # REDEBATE 谱系:被顶替的那一稿此前直接被覆盖、无从追溯 ——
                # 于是「这名字重跑过、原来是什么样」在事后彻底消失。
                old_bid = (plans.get(cid) or {}).get("build_id")
                if old_bid and r.get("build_id"):
                    from . import snapshots
                    snapshots.mark_superseded(old_bid, r["build_id"])
                    r["redebate_of"] = old_bid
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
    out = {"status": status, "passes": passes, "integrity_violations": integrity, "n": len(plans),
           "distribution": {**en, "histogram": dist.histogram(convs)}, "portfolio": pf,
           "skipped": skipped, "stored": stored,
           "plans": {cid: _summary(cid, p) for cid, p in plans.items()}}
    if timed_out:            # 留声:否则「今天只裁决了这几家」会被读成窗内只有这几家
        out["timed_out"] = len(timed_out)
    _book_run_finish(book_run, out)
    return out


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
    from . import distribution as dist, engine

    rows = structured.upcoming_calendar(list(PHANNY_UNIVERSE), days=engine._window_days(), limit=100)
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
