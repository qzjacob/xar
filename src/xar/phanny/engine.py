"""Phanny 单名引擎:六维接地 dossier → 提议者(最高 reasoning effort)→ 多 critic 辩论(debate.py)
→ 校验 → 入库(INSERT 即锁,--force 才 version+1)。复用 earnings 底座,不重写数据装配。

推理一律 `reasoning_effort="high"`;host 上由 `_primary_pin()` 提级订阅执行器(codex/claude-max,$0),
docker/无执行器落**订阅池**(phanny_primary_models:minimax/kimi/glm,usd=0;已移除计量 deepseek)。
size 由 phanny.sizing 代码侧算(此处只写回),LLM 不报 size。
"""
from __future__ import annotations

import contextlib
import json
from datetime import date

from ..config import get_settings
from ..logging import get_logger
from ..storage import buildlog, db

log = get_logger("xar.phanny.engine")


# ── 数据装配:复用 ET dossier + 补技术/资金/期权结构 section(grounded id tech:/flow:/opt:)──
def _window_days() -> int:
    """Phanny 观察窗天数(含 lead-days 尾部)。单名 _next_earnings、批量 judge_due、
    book.portfolio 一律用此口径,保证"能构建的事件一定可见"(修 review M.2.1 口径不一致)。"""
    s = get_settings()
    return max(s.phanny_watch_days + s.phanny_verdict_lead_days + 2, 15)


def _next_earnings(cid: str):
    """cid 下一次 earnings 事件(Phanny 观察窗内)。窗口 = _window_days(),与 judge_due/portfolio 一致。"""
    from ..storage import structured
    rows = structured.upcoming_calendar([cid], days=_window_days(), limit=20)
    return next((r for r in rows if r.get("event_type") == "earnings"), None)


def dossier_phanny(cid: str, event: dict) -> dict | None:
    """六维接地 dossier。复用 earnings.dossier_earnings(基本/情绪/期权隐含/评级/价格/宏观/论点),
    再补技术面 + 资金面 section。返回 base + implied_move(供 sizing)。单节 fail-soft。"""
    from ..research import earnings

    base = earnings.dossier_earnings(cid, event)
    if base is None:
        return None
    known: set = base["known_ids"]
    panel: dict = base["panel"]
    parts = [base["text"]]

    def _sect(fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            log.warning("phanny dossier %s section: %s", cid, str(e)[:120])

    def _technical():
        pr = db.query("SELECT d, close FROM prices WHERE company_id=%s ORDER BY d DESC LIMIT 60", (cid,))
        if len(pr) >= 20:
            closes = [float(r["close"]) for r in pr]
            last, sma20 = closes[0], sum(closes[:20]) / 20
            ret20 = (last / closes[min(19, len(closes) - 1)] - 1) * 100
            known.add(f"tech:{cid}")
            panel["technical"] = {"last": last, "sma20": round(sma20, 2),
                                  "vs_sma20_pct": round((last / sma20 - 1) * 100, 2), "ret20_pct": round(ret20, 2)}
            parts.append(f"## 技术面\n[tech:{cid}] 收盘 {last} · SMA20 {sma20:.2f} "
                         f"(偏离 {(last / sma20 - 1) * 100:+.1f}%) · 近 20 日 {ret20:+.1f}%")
    _sect(_technical)

    def _flow():
        from ..research.thesis_signals import signal_snapshot
        snap = signal_snapshot(cid) or []
        flow = [s for s in snap if any(k in (s.get("signal_key") or "")
                for k in ("short", "obv", "hold", "flow", "put_call", "pc", "insider", "13f"))]
        if flow:
            known.add(f"flow:{cid}")
            panel["capital_flow"] = flow[:6]
            parts.append(f"## 资金面\n[flow:{cid}] "
                         + " · ".join(f"{s['name_cn']} z={s.get('z')} 贡献={s.get('contribution')}" for s in flow[:6]))
    _sect(_flow)

    implied_val = None
    im = panel.get("implied_move") or {}
    if im.get("series"):
        implied_val = float(im["series"][0]["value"])
        known.add(f"opt:{cid}")
        parts.append(f"## 期权结构(输入信号,不输出期权策略)\n[opt:{cid}] ATM 隐含波动 "
                     f"{implied_val * 100:.1f}%(仅作证据引用,方向观点仍为股票多空)")

    return {"text": "\n\n".join(parts), "known_ids": known, "panel": panel, "as_of": base["as_of"],
            "event_date": base["event_date"], "n_facts": len(known), "implied_move": implied_val}


# ── 提议者 ────────────────────────────────────────────────────────────────────────
def _system_phanny() -> str:
    """提议者 system prompt。正文在 models/prompts 注册表(带 version + 源码 sha),
    这里只做取用 —— 没有版本身份的提示词等于无法复盘:「这条裁决是哪一版提示词产出的」查不到。"""
    from ..models import prompts
    from ..ontology.phanny_events import PHANNY_DIMENSIONS
    return prompts.get("phanny.proposer.system").render(tuple(PHANNY_DIMENSIONS))


def _host_executor() -> str | None:
    """host 上可用的订阅深度研究执行器(codex/claude-max);无 → None(裸 token)。"""
    from ..models import agentsdk, codex_cli
    s = get_settings()
    if s.codex_enabled and codex_cli.available():
        return "codex"
    if s.anthropic_max_enabled and agentsdk.available():
        return "claude-max"
    return None


def _primary_pin():
    """host 择优深度研究订阅执行器(codex/claude-max,均订阅计费);无(docker)→ **订阅池**钉扎链
    (phanny_primary_models:minimax-m3-sub→kimi-k3-sub→glm-5.2-sub,均 usd=0)。
    2026-07-25 裁定:移除 deepseek-v4-pro —— 它按 token 计费,propose/rebut 曾日烧 ~$7.6,
    与「订阅额度充分利用、零计量支出」目标冲突。链外无计量回退:全订阅耗尽=本名裁决失败(优雅降级)。"""
    from ..models import llm
    ex = _host_executor()
    if ex == "codex":
        return llm.CODEX_PIN
    if ex == "claude-max":
        return llm.CLAUDE_MAX_PIN
    ids = tuple(x.strip() for x in (get_settings().phanny_primary_models or "").split(",") if x.strip())
    return ids or ("glm-5.2-sub", "glm-4.6-sub")


def propose(cid: str, event: dict, dossier: dict, *, run_id: str | None = None, extra: str = "",
            build_id: str | None = None):
    """dossier → PhannyProposal(validate + retry-once)。返回 (proposal|None, problems, model)。"""
    from ..models import llm, prompts
    from . import snapshots
    from ..models.router import TaskClass
    from ..ontology.phanny_events import PhannyProposal, validate_proposal

    prompt = prompts.get("phanny.proposer.user").render(dossier["as_of"], dossier["text"], extra)
    pin = _primary_pin()
    model = (pin[0] if pin else "token")
    problems: list[str] = []
    p = None
    for attempt in (1, 2):
        suffix = prompts.get("phanny.proposer.retry").render(problems)
        ctx = llm.pinned(pin) if pin else contextlib.nullcontext()
        cap: dict = {}
        try:
            with ctx:
                p = llm.complete_json(prompt + suffix, PhannyProposal, system=_system_phanny(),
                                      task=TaskClass.PHANNY_VERDICT, node="phanny_propose",
                                      run_id=run_id, max_tokens=8000, reasoning_effort="high",
                                      context={"company_id": cid, "role": "proposer",
                                               "attempt": attempt,
                                               "event_date": str(event.get("scheduled_for"))},
                                      capture=cap)
        except Exception as e:  # noqa: BLE001 — LLM/解析失败隔离为单名拒绝,不炸整批 book(镜像 earnings)
            buildlog.record("phanny", cid, stage="propose", status="llm_failed",
                            reason=f"llm: {str(e)[:400]}", run_id=run_id, attempt=attempt,
                            model=model, event_date=event.get("scheduled_for"))
            if build_id:
                snapshots.snap_call(build_id, cid, stage="propose", run_id=run_id,
                                    event_date=event.get("scheduled_for"), attempt=attempt,
                                    model=model, capture=cap, template="phanny.proposer.user",
                                    template_ver=1, meta={"status": "llm_failed"})
            return None, [f"llm: {str(e)[:160]}"], model
        problems = validate_proposal(p, known_ids=dossier["known_ids"])
        # 每一稿(含被拒稿)都留快照 —— 最该复盘的恰恰是没能入库的那些
        if build_id:
            snapshots.snap_call(build_id, cid, stage="propose", run_id=run_id,
                                event_date=event.get("scheduled_for"), attempt=attempt,
                                model=model, capture=cap, template="phanny.proposer.user",
                                template_ver=1,
                                params={"as_of": str(dossier.get("as_of")), "has_retry_suffix": bool(suffix)},
                                meta={"status": "ok" if not problems else "rejected",
                                      "problems": problems[:10]})
        if not problems:
            break
        log.warning("phanny propose %s attempt %d: %d violations", cid, attempt, len(problems))
        # 全量违规入台账(不截断)——截断过的清单无法用来定位系统性纪律模式
        buildlog.record("phanny", cid, stage="validate", status="rejected",
                        reason=f"attempt {attempt}: {len(problems)} violations", problems=problems,
                        run_id=run_id, attempt=attempt, model=model,
                        event_date=event.get("scheduled_for"))
    return p, problems, model


# ── 单名装配(propose + debate;未入库)────────────────────────────────────────────────
def build_one(cid: str, *, event: dict | None = None, force: bool = False, run_id: str | None = None) -> dict:
    def _no(status: str, reason: str, *, stage: str, ed=None, problems=None) -> dict:
        """非 converged 的每条出口都留痕 —— 否则「这家为何没产出」只能靠翻日志猜。"""
        buildlog.record("phanny", cid, stage=stage, status=status, reason=reason,
                        problems=problems, run_id=run_id, event_date=ed)
        out = {"status": status, "company_id": cid, "reason": reason}
        if ed is not None:
            out["event_date"] = str(ed)
        return out

    if event is None:
        event = _next_earnings(cid)
    if not event:
        return _no("no_data", "no upcoming earnings", stage="dossier")
    event_date = event.get("scheduled_for")
    prev = latest_verdict(cid, event_date)
    if prev and not force:
        return {**_no("skipped", "verdict locked (use force)", stage="store", ed=event_date),
                "version": prev["version"]}
    # host-only 闸(可选):无订阅执行器时 docker worker 延后,host 专跑(整本 book 较重,防 OOM)。
    if get_settings().phanny_verdict_host_only and _host_executor() is None:
        return _no("deferred_host", "no subscription executor (host-only)",
                   stage="dossier", ed=event_date)
    d = dossier_phanny(cid, event)
    if d is None:
        return _no("no_data", "unknown company", stage="dossier", ed=event_date)
    if d["n_facts"] < 4:
        return _no("no_data", f"only {d['n_facts']} grounded facts", stage="dossier", ed=event_date)
    # 从这里起本次构建有了身份:dossier 定格 + 每次调用挂在同一个 build_id 下,
    # 于是「这条裁决当时看到了什么」可字节级还原(见 phanny/snapshots)。
    from . import snapshots
    build_id = snapshots.new_build_id()
    snapshots.snap_dossier(build_id, cid, d, run_id=run_id, event_date=event_date)
    p0, problems, model = propose(cid, event, d, run_id=run_id, build_id=build_id)
    if p0 is None or problems:
        # propose 内已按 attempt 逐条记过明细;这里只记最终态(status 区分 llm_failed / rejected)
        llm_failed = bool(problems) and str(problems[0]).startswith("llm:")
        return {**_no("llm_failed" if llm_failed else "rejected",
                      "; ".join(problems[:5]) or "no proposal",
                      stage="propose", ed=event_date, problems=problems),
                "build_id": build_id}
    from . import debate as debate_mod
    conv = debate_mod.run_debate(cid, event, d, p0, run_id=run_id, primary_model=model,
                                 build_id=build_id)
    return {"status": "converged", "company_id": cid, "event": event, "dossier": d,
            "build_id": build_id, **conv}


# ── 入库(原子 version;并发输家撞 UNIQUE → raced,不炸批)────────────────────────────────
def _store(cid: str, plan: dict, ensemble_status: str, *, run_id: str | None = None,
           force: bool = False, variant: str = "prod", replay_of: int | None = None) -> dict:
    prop = plan["proposal"]
    event = plan["event"]
    d = plan["dossier"]
    event_date = event.get("scheduled_for")
    if not force:
        prev = latest_verdict(cid, event_date)
        if prev:
            return {"status": "skipped", "company_id": cid, "version": prev["version"],
                    "reason": "locked"}
    # round-1 原稿与 REDEBATE 谱系也进 content:反作弊守卫(禁止靠降 conviction 凑收敛)事后
    # 必须能被复核,而它依赖的正是 round1_* 这几个数 —— 此前只在内存里传给 book,从不落库。
    content = {**prop.model_dump(), "size_rationale": plan.get("size_rationale"),
               "debate_trace": plan.get("debate_trace"), "converged": plan.get("converged"),
               "rounds": plan.get("rounds"), "models": plan.get("models"),
               "build_id": plan.get("build_id"), "redebate_of": plan.get("redebate_of"),
               "round1_conviction": plan.get("round1_conviction"),
               "round1_anchors": plan.get("round1_anchors"),
               "final_anchors": plan.get("final_anchors")}
    anchors = len({e for dm in prop.dimensions for e in dm.evidence})
    quality = {"evidence_anchors": anchors, "dimensions": len(prop.dimensions), "n_facts": d["n_facts"]}
    try:
        row = db.query(
            "INSERT INTO phanny_verdicts(company_id, event_date, calendar_id, version, direction, "
            "conviction, size_pct, expected_move, debate_models, rounds, ensemble_status, content, "
            "quality, model, run_id, as_of, build_id, variant, replay_of) "
            "SELECT %s,%s,%s, COALESCE((SELECT max(version) FROM phanny_verdicts "
            "  WHERE company_id=%s AND event_date=%s),0)+1, %s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s "
            "RETURNING id, version",
            (cid, event_date, event.get("id"), cid, event_date, prop.direction, float(prop.conviction),
             plan.get("size_pct"), d.get("implied_move"), plan.get("models"), plan.get("rounds"),
             ensemble_status, json.dumps(content, ensure_ascii=False, default=str),
             json.dumps(quality, ensure_ascii=False), (plan.get("models") or ["token"])[0], run_id,
             d["as_of"], plan.get("build_id"), variant, replay_of))
    except Exception as e:  # noqa: BLE001
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            return {"status": "raced", "company_id": cid, "event_date": str(event_date)}
        raise
    version = row[0]["version"] if row else None
    if row and plan.get("build_id"):      # 建立 verdict ↔ 输入快照的双向索引(回放入口)
        from . import snapshots
        snapshots.stamp_verdict(plan["build_id"], row[0]["id"])
    log.info("phanny verdict %s v%s: %s conviction=%.1f size=%s ens=%s",
             cid, version, prop.direction, float(prop.conviction), plan.get("size_pct"), ensemble_status)
    return {"status": "built", "company_id": cid, "event_date": str(event_date), "version": version,
            "direction": prop.direction, "conviction": float(prop.conviction), "size_pct": plan.get("size_pct"),
            "ensemble_status": ensemble_status}


def build_verdict(cid: str, *, force: bool = False, run_id: str | None = None) -> dict:
    """单名裁决(无组合正态,n=1 → ensemble_status='single';size 用单名公式)。CLI/capability 入口。"""
    from . import sizing as sizing_mod
    from ..models import llm
    run_id = run_id or llm.new_batch_run_id("phanny")    # 保险杠:任何调用方漏传都不会退回 run_id=NULL
    r = build_one(cid, force=force, run_id=run_id)
    if r.get("status") != "converged":
        return r
    prop = r["proposal"]
    asym = 1.2 if prop.asymmetry_zh.strip() else 1.0
    size, rationale = sizing_mod.name_size(float(prop.conviction), asymmetry=asym,
                                           implied_move=r["dossier"].get("implied_move"))
    r["size_pct"], r["size_rationale"] = size, rationale
    return _store(cid, r, "single", run_id=run_id, force=force)


def judge_due(*, force: bool = False, run_id: str | None = None,
              origin: str = "?") -> dict:
    """观察窗内出财报的选中名 → 整本 book(组合正态 + sizing + 入库)。

    覆盖范围由 `phanny_universe_mode` 决定(默认 registry=全覆盖库)。全库模式下财报季会有
    大量公司同时进窗,而单名完整辩论约 40 次订阅调用 —— 故按**财报临近度**排序并用
    `phanny_book_max_per_cycle` 截断:先做最紧迫的,其余下一轮继续(裁决幂等加锁,不会重做)。
    """
    from . import book
    from ..models import llm
    from ..ontology.phanny_events import universe_ids
    from ..storage import structured
    run_id = run_id or llm.new_batch_run_id("phanny")    # 同上:整本 book 的花费归到一个 run
    ids = list(universe_ids())
    rows = structured.upcoming_calendar(ids, days=_window_days(), limit=max(100, len(ids)))
    earn = [r for r in rows if r.get("event_type") == "earnings"]
    earn.sort(key=lambda r: r["scheduled_for"])          # 财报临近度:最紧迫的先做
    cap = get_settings().phanny_book_max_per_cycle
    cids, deferred = [r["company_id"] for r in earn], []
    if cap and len(cids) > cap:
        deferred = cids[cap:]
        cids = cids[:cap]
        # 截断必须留声:静默截断会读成「今天只有这几家有财报」
        log.info("phanny book: %d names in window, capped to %d (deferred %d to next cycle)",
                 len(earn), cap, len(deferred))
        for dcid in deferred:
            buildlog.record("phanny", dcid, stage="book", status="skipped",
                            reason=f"per-cycle cap {cap} reached — 顺延下一轮", run_id=run_id)
    out = book.run_book(cids, force=force, run_id=run_id, origin=origin,
                        max_seconds=get_settings().phanny_book_max_seconds or None)
    if deferred:
        out["deferred_to_next_cycle"] = len(deferred)
    return out


# ── 读取 / 回验 / 校准 ────────────────────────────────────────────────────────────────
def latest_verdict(cid: str, event_date) -> dict | None:
    rows = db.query(
        "SELECT id, version, direction, conviction, size_pct, expected_move, ensemble_status, as_of, "
        "model, content, outcome FROM phanny_verdicts WHERE company_id=%s AND event_date=%s "
        "AND variant='prod' "          # 回放版本(variant='replay')绝不进最新裁决/组合读路径
        "ORDER BY version DESC LIMIT 1", (cid, event_date))
    return rows[0] if rows else None


def _stamp(vid: int, outcome: dict) -> None:
    db.execute("UPDATE phanny_verdicts SET outcome=%s::jsonb, outcome_at=now() WHERE id=%s",
               (json.dumps(outcome, ensure_ascii=False, default=str), vid))
    _stamp_horizon(vid, "reaction", outcome)


def _stamp_horizon(vid: int, horizon: str, outcome: dict) -> None:
    """多 horizon 回验史(append-only)。legacy 的 outcome JSONB 是单 horizon 且每次 UPDATE
    覆盖 —— 「当时怎么判、后来又怎么改判」一点痕迹都不剩。这里每个 horizon 独立一行,
    UNIQUE(verdict_id,horizon) 保证幂等重跑不重复。never-raise:回验主流程不受影响。"""
    try:
        db.execute(
            "INSERT INTO phanny_outcomes(verdict_id, horizon, status, reaction_pct, "
            "direction_hit, size_weighted_pnl_pct, details) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb) "
            "ON CONFLICT (verdict_id, horizon) DO UPDATE SET status=EXCLUDED.status, "
            "reaction_pct=EXCLUDED.reaction_pct, direction_hit=EXCLUDED.direction_hit, "
            "size_weighted_pnl_pct=EXCLUDED.size_weighted_pnl_pct, details=EXCLUDED.details",
            (vid, horizon, outcome.get("status", "scored"), outcome.get("reaction_pct"),
             outcome.get("direction_hit"), outcome.get("size_weighted_pnl_pct"),
             json.dumps(outcome, ensure_ascii=False, default=str)))
    except Exception as e:  # noqa: BLE001
        log.warning("phanny outcome horizon %s/%s: %s", vid, horizon, str(e)[:120])


def score_outcomes() -> dict:
    """盘后回验:已过财报日的裁决 → 反应 + 方向命中 + size 加权 pnl。复用 earnings 的 occurred/reaction。"""
    from ..research import earnings
    s = get_settings()
    today = date.today()
    pend = db.query(
        "SELECT id, company_id, event_date, direction, conviction, size_pct, expected_move "
        "FROM phanny_verdicts WHERE outcome IS NULL AND event_date < %s ORDER BY event_date LIMIT 200", (today,))
    out = {"scored": 0, "event_moved": 0, "price_missing": 0, "pending": 0}
    for v in pend:
        cid, ed = v["company_id"], v["event_date"]
        occ = earnings._occurred_on(cid, ed, tol_days=s.phanny_outcome_max_days)
        overdue = (today - ed).days > s.phanny_outcome_max_days
        if occ is None:
            if overdue:
                _stamp(v["id"], {"status": "event_moved"})
                out["event_moved"] += 1
            else:
                out["pending"] += 1
            continue
        rr = earnings.reaction_return(cid, occ["scheduled_for"], (occ["meta"] or {}).get("session"))
        if rr is None:
            if overdue:
                _stamp(v["id"], {"status": "price_missing"})
                out["price_missing"] += 1
            else:
                out["pending"] += 1
            continue
        reaction = rr["reaction_pct"]
        hit = (reaction > 0) if v["direction"] == "long" else (reaction < 0)
        signed = reaction if v["direction"] == "long" else -reaction
        pnl = round((float(v["size_pct"]) if v["size_pct"] else 0.0) / 100.0 * signed, 3)
        _stamp(v["id"], {"status": "scored", "session": rr["session"], "reaction_pct": reaction,
                         "direction_hit": hit, "size_weighted_pnl_pct": pnl})
        out["scored"] += 1
        # 兑现即反哺个股论点(合成 quarterly_print 事实 + 定向 VP 扫描)。fail-soft:
        # 反哺出问题绝不拖垮回验批。两条回验节拍(Phanny/ET)谁先跑都靠 dedup 只写一条。
        try:
            from ..research import quarterly_feedback
            quarterly_feedback.on_outcome(cid, ed)
        except Exception as e:  # noqa: BLE001
            log.warning("phanny feedback %s: %s", cid, str(e)[:120])
    return out


def calibration() -> dict:
    from . import distribution as dist
    rows = db.query("SELECT conviction, outcome FROM phanny_verdicts "
                    "WHERE outcome IS NOT NULL AND outcome->>'status'='scored'")
    return dist.calibration_buckets([{"conviction": r["conviction"], "outcome": r["outcome"]} for r in rows])
