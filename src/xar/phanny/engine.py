"""Phanny 单名引擎:六维接地 dossier → 提议者(最高 reasoning effort)→ 多 critic 辩论(debate.py)
→ 校验 → 入库(INSERT 即锁,--force 才 version+1)。复用 earnings 底座,不重写数据装配。

推理一律 `reasoning_effort="high"`;host 上由 `_primary_pin()` 提级订阅执行器(codex/claude-max,$0),
docker/无执行器落 deepseek 强 token。size 由 phanny.sizing 代码侧算(此处只写回),LLM 不报 size。
"""
from __future__ import annotations

import contextlib
import json
from datetime import date

from ..config import get_settings
from ..logging import get_logger
from ..storage import db

log = get_logger("xar.phanny.engine")


# ── 数据装配:复用 ET dossier + 补技术/资金/期权结构 section(grounded id tech:/flow:/opt:)──
def _next_earnings(cid: str):
    """cid 下一次 earnings 事件(Phanny 观察窗内;比 ET 窗更宽,含更多提前布局的名字)。"""
    from ..storage import structured
    s = get_settings()
    days = s.phanny_watch_days + s.phanny_verdict_lead_days + 2
    rows = structured.upcoming_calendar([cid], days=max(days, 15), limit=20)
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
    from ..ontology.phanny_events import PHANNY_DIMENSIONS
    dims = " / ".join(PHANNY_DIMENSIONS)
    return f"""你是对冲基金的季报事件多空交易员。给你某公司季报前的 360° dossier(含接地事实 id)。
输出一个 PhannyProposal JSON。铁律:
1. **方向只能 long 或 short**——禁止 neutral / no_trade / 弃权;证据弱就给低 conviction(可低至 1)但仍须表态;
2. **禁止把期权/结构化策略(straddle/iron condor/价差 等)当作交易观点**——只做方向性股票多空;
   期权数据(IV/skew/隐含波动)只可作为 options_structure 维度的**证据**引用;
3. evidence 里每个 id 必须逐字抄自 dossier(如 "estimate:now:eps_diluted"),严禁编造;
4. dimensions **必须覆盖全部 6 维**(不缺项、不自造别名):{dims};每维 score(-2..+2)与 note 一致,
   信息缺失的维度诚实写"数据不足"而非编造;
5. conviction(1-10)与证据密度耦合:≥7 需 ≥6 个不同接地锚、asymmetry_zh 写清赔率为何不对称、
   并给 ≥1 条盘前可观测的 falsifier;
6. move_view_zh 表态 implied move 相对你预期分布是贵/便宜/合理;prob_bins 给 5 分箱
   [P(>+5%),P(+2~+5%),P(-2~+2%),P(-5~-2%),P(<-5%)] 且和≈1,e_return_pct 与之一致、符号与 direction 一致;
7. **不要输出 size**(size 由系统按 conviction/赔率/波动确定性计算)。"""


def _primary_pin():
    """host 择优深度研究订阅执行器;无 → deepseek 强 token(仍显式 high effort)。"""
    from ..models import agentsdk, codex_cli, llm
    s = get_settings()
    if s.codex_enabled and codex_cli.available():
        return llm.CODEX_PIN
    if s.anthropic_max_enabled and agentsdk.available():
        return llm.CLAUDE_MAX_PIN
    return ("deepseek-v4-pro",)


def propose(cid: str, event: dict, dossier: dict, *, run_id: str | None = None, extra: str = ""):
    """dossier → PhannyProposal(validate + retry-once)。返回 (proposal|None, problems, model)。"""
    from ..models import llm
    from ..models.router import TaskClass
    from ..ontology.phanny_events import PhannyProposal, validate_proposal

    prompt = f"为下述公司生成季报多空 PhannyProposal(as_of={dossier['as_of']}):\n\n{dossier['text']}{extra}"
    pin = _primary_pin()
    problems: list[str] = []
    p = None
    for attempt in (1, 2):
        suffix = ("\n\n上一稿违规,必须修正:\n- " + "\n- ".join(problems)) if problems else ""
        ctx = llm.pinned(pin) if pin else contextlib.nullcontext()
        with ctx:
            p = llm.complete_json(prompt + suffix, PhannyProposal, system=_system_phanny(),
                                  task=TaskClass.PHANNY_VERDICT, node="phanny_propose",
                                  run_id=run_id, max_tokens=8000, reasoning_effort="high")
        problems = validate_proposal(p, known_ids=dossier["known_ids"])
        if not problems:
            break
        log.warning("phanny propose %s attempt %d: %d violations", cid, attempt, len(problems))
    return p, problems, (pin[0] if pin else "token")


# ── 单名装配(propose + debate;未入库)────────────────────────────────────────────────
def build_one(cid: str, *, event: dict | None = None, force: bool = False, run_id: str | None = None) -> dict:
    if event is None:
        event = _next_earnings(cid)
    if not event:
        return {"status": "no_data", "company_id": cid, "reason": "no upcoming earnings"}
    event_date = event.get("scheduled_for")
    prev = latest_verdict(cid, event_date)
    if prev and not force:
        return {"status": "skipped", "company_id": cid, "event_date": str(event_date),
                "version": prev["version"], "reason": "verdict locked (use force)"}
    d = dossier_phanny(cid, event)
    if d is None:
        return {"status": "no_data", "company_id": cid, "reason": "unknown company"}
    if d["n_facts"] < 4:
        return {"status": "no_data", "company_id": cid, "reason": f"only {d['n_facts']} grounded facts"}
    p0, problems, model = propose(cid, event, d, run_id=run_id)
    if p0 is None or problems:
        return {"status": "rejected", "company_id": cid, "reason": "; ".join(problems[:5]) or "no proposal"}
    from . import debate as debate_mod
    conv = debate_mod.run_debate(cid, event, d, p0, run_id=run_id, primary_model=model)
    return {"status": "converged", "company_id": cid, "event": event, "dossier": d, **conv}


# ── 入库(原子 version;并发输家撞 UNIQUE → raced,不炸批)────────────────────────────────
def _store(cid: str, plan: dict, ensemble_status: str, *, run_id: str | None = None, force: bool = False) -> dict:
    prop = plan["proposal"]
    event = plan["event"]
    d = plan["dossier"]
    event_date = event.get("scheduled_for")
    if not force:
        prev = latest_verdict(cid, event_date)
        if prev:
            return {"status": "skipped", "company_id": cid, "version": prev["version"],
                    "reason": "locked"}
    content = {**prop.model_dump(), "size_rationale": plan.get("size_rationale"),
               "debate_trace": plan.get("debate_trace"), "converged": plan.get("converged"),
               "rounds": plan.get("rounds"), "models": plan.get("models")}
    anchors = len({e for dm in prop.dimensions for e in dm.evidence})
    quality = {"evidence_anchors": anchors, "dimensions": len(prop.dimensions), "n_facts": d["n_facts"]}
    try:
        row = db.query(
            "INSERT INTO phanny_verdicts(company_id, event_date, calendar_id, version, direction, "
            "conviction, size_pct, expected_move, debate_models, rounds, ensemble_status, content, "
            "quality, model, run_id, as_of) "
            "SELECT %s,%s,%s, COALESCE((SELECT max(version) FROM phanny_verdicts "
            "  WHERE company_id=%s AND event_date=%s),0)+1, %s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s "
            "RETURNING version",
            (cid, event_date, event.get("id"), cid, event_date, prop.direction, float(prop.conviction),
             plan.get("size_pct"), d.get("implied_move"), plan.get("models"), plan.get("rounds"),
             ensemble_status, json.dumps(content, ensure_ascii=False, default=str),
             json.dumps(quality, ensure_ascii=False), (plan.get("models") or ["token"])[0], run_id, d["as_of"]))
    except Exception as e:  # noqa: BLE001
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            return {"status": "raced", "company_id": cid, "event_date": str(event_date)}
        raise
    version = row[0]["version"] if row else None
    log.info("phanny verdict %s v%s: %s conviction=%.1f size=%s ens=%s",
             cid, version, prop.direction, float(prop.conviction), plan.get("size_pct"), ensemble_status)
    return {"status": "built", "company_id": cid, "event_date": str(event_date), "version": version,
            "direction": prop.direction, "conviction": float(prop.conviction), "size_pct": plan.get("size_pct"),
            "ensemble_status": ensemble_status}


def build_verdict(cid: str, *, force: bool = False, run_id: str | None = None) -> dict:
    """单名裁决(无组合正态,n=1 → ensemble_status='single';size 用单名公式)。CLI/capability 入口。"""
    from . import sizing as sizing_mod
    r = build_one(cid, force=force, run_id=run_id)
    if r.get("status") != "converged":
        return r
    prop = r["proposal"]
    asym = 1.2 if prop.asymmetry_zh.strip() else 1.0
    size, rationale = sizing_mod.name_size(float(prop.conviction), asymmetry=asym,
                                           implied_move=r["dossier"].get("implied_move"))
    r["size_pct"], r["size_rationale"] = size, rationale
    return _store(cid, r, "single", run_id=run_id, force=force)


def judge_due(*, force: bool = False, run_id: str | None = None) -> dict:
    """观察窗内出财报的选中名 → 整本 book(组合正态 + sizing + 入库)。"""
    from . import book
    from ..ontology.phanny_events import PHANNY_UNIVERSE
    from ..storage import structured
    s = get_settings()
    rows = structured.upcoming_calendar(list(PHANNY_UNIVERSE), days=s.phanny_watch_days, limit=100)
    cids = [r["company_id"] for r in rows if r.get("event_type") == "earnings"]
    return book.run_book(cids, force=force, run_id=run_id)


# ── 读取 / 回验 / 校准 ────────────────────────────────────────────────────────────────
def latest_verdict(cid: str, event_date) -> dict | None:
    rows = db.query(
        "SELECT id, version, direction, conviction, size_pct, expected_move, ensemble_status, as_of, "
        "model, content, outcome FROM phanny_verdicts WHERE company_id=%s AND event_date=%s "
        "ORDER BY version DESC LIMIT 1", (cid, event_date))
    return rows[0] if rows else None


def _stamp(vid: int, outcome: dict) -> None:
    db.execute("UPDATE phanny_verdicts SET outcome=%s::jsonb, outcome_at=now() WHERE id=%s",
               (json.dumps(outcome, ensure_ascii=False, default=str), vid))


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
    return out


def calibration() -> dict:
    from . import distribution as dist
    rows = db.query("SELECT conviction, outcome FROM phanny_verdicts "
                    "WHERE outcome IS NOT NULL AND outcome->>'status'='scored'")
    return dist.calibration_buckets([{"conviction": r["conviction"], "outcome": r["outcome"]} for r in rows])
