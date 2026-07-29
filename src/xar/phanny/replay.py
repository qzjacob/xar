"""裁决回放 —— 让「换个模型/换版提示词,同样的证据会怎么判」成为可回答的问题。

**为什么能回放**:输入不再重新计算,而是从 `phanny_build_snapshots` + `artifacts` 取回
当时定格的 dossier(全文 / known_ids / panel)。这一点是关键:`as_of` 只是构建日期,而
prices/estimates/ratings/alt_signals 一直在变 —— 「同样的代码同样的日期重跑一遍」得到的
dossier 与当初并不相同,那不叫回放,叫重做。

**bit_exact 的含义**:按快照记录的 (template, version) 重新渲染提示词,再与当时记录的
`prompt_sha` 比对。相等 = 输入逐字节一致,差异只可能来自模型;不等 = 模板在这期间漂移过,
结果**照样返回但显式标记 False** —— 悄悄吸收漂移比报错更危险。

生产隔离:回放写 `variant='replay'` + `replay_of`,读路径(latest_verdict / book.portfolio)
只认 `variant='prod'`,回放永远不会影响真实组合。但它**会**被同一套回验打分 ——
回放也是可证伪的预测,这正是 A/B 的意义。
"""
from __future__ import annotations

from ..logging import get_logger
from ..models import llm, prompts
from ..storage import db
from . import snapshots

log = get_logger("xar.phanny.replay")


def _rebuild_dossier(snap: dict) -> dict:
    """快照 → dossier dict(与 engine.dossier_phanny 的返回同形,但**不读任何活表**)。"""
    meta = snap.get("meta") or {}
    return {"text": snap.get("dossier_text") or "",
            "known_ids": set(snap.get("known_ids") or []),
            "panel": snap.get("panel") or {},
            "as_of": meta.get("as_of"),
            "n_facts": meta.get("n_facts"),
            "implied_move": meta.get("implied_move")}


def _verify_prompt(dossier: dict, snap_row: dict) -> tuple[bool, str | None]:
    """按记录的模板身份重渲染,与当时的 prompt_sha 比对 → (是否逐字节一致, 不一致原因)。"""
    tmpl, ver = snap_row.get("prompt_template"), snap_row.get("template_ver")
    recorded = snap_row.get("prompt_sha")
    if not (tmpl and recorded):
        return False, "快照缺模板身份或 prompt_sha(该 build 早于审计层)"
    t = prompts.REGISTRY.get(tmpl)
    if t is None:
        return False, f"模板 {tmpl} 已从注册表移除"
    if ver is not None and t.version != ver:
        return False, f"模板 {tmpl} 版本已从 v{ver} 升到 v{t.version}"
    params = snap_row.get("params") or {}
    try:
        body = t.render(params.get("as_of") or dossier.get("as_of"), dossier["text"], "")
    except Exception as e:  # noqa: BLE001 — 模板签名变了也是一种漂移
        return False, f"模板重渲染失败({type(e).__name__}),签名可能已改"
    system = None
    if tmpl == "phanny.proposer.user":
        from ..ontology.phanny_events import PHANNY_DIMENSIONS
        system = prompts.get("phanny.proposer.system").render(tuple(PHANNY_DIMENSIONS))
    instruction_sha = llm._sha(system, llm.json_instruction(body, _proposal_schema()))
    if instruction_sha == recorded:
        return True, None
    return False, "提示词指纹不一致(模板措辞或 schema 已漂移)"


def _proposal_schema():
    from ..ontology.phanny_events import PhannyProposal
    return PhannyProposal


def replay_verdict(verdict_id: int, *, model: str | None = None, store: bool = True) -> dict:
    """回放一条裁决:从快照重建输入 → 重跑 propose(+可选换模型)→ 存为 variant='replay'。

    返回 {status, bit_exact, drift_reason, direction, conviction, replay_of, version}。"""
    rows = db.query("SELECT id, company_id, event_date, build_id, direction, conviction, variant "
                    "FROM phanny_verdicts WHERE id=%s", (verdict_id,))
    if not rows:
        return {"status": "not_found", "verdict_id": verdict_id}
    v = rows[0]
    if not v.get("build_id"):
        return {"status": "no_snapshot", "verdict_id": verdict_id,
                "reason": "该裁决早于审计层(无 build_id),无法回放 —— 只有带快照的构建可回放"}
    build = snapshots.load_build(v["build_id"])
    if not build or not build.get("dossier"):
        return {"status": "no_snapshot", "verdict_id": verdict_id, "build_id": v["build_id"]}

    dossier = _rebuild_dossier(build["dossier"])
    propose_snap = next((c for c in build["calls"] if c["stage"] == "propose"), None)
    bit_exact, drift = (False, "无 propose 快照")
    if propose_snap:
        bit_exact, drift = _verify_prompt(dossier, propose_snap)

    from . import engine
    event = {"scheduled_for": v["event_date"], "id": None}
    run_id = llm.new_batch_run_id("phanny")
    build_id = snapshots.new_build_id()
    snapshots.snap_dossier(build_id, v["company_id"], dossier, run_id=run_id,
                           event_date=v["event_date"])
    pin = (model,) if model else None
    import contextlib
    with (llm.pinned(pin) if pin else contextlib.nullcontext()):
        p, problems, used_model = engine.propose(v["company_id"], event, dossier,
                                                 run_id=run_id, build_id=build_id)
    out = {"status": "replayed", "verdict_id": verdict_id, "build_id": build_id,
           "bit_exact": bit_exact, "drift_reason": drift, "model": model or used_model,
           "original": {"direction": v["direction"], "conviction": float(v["conviction"])}}
    if p is None or problems:
        out["status"] = "rejected"
        out["problems"] = problems[:8]
        return out
    out["direction"] = p.direction
    out["conviction"] = float(p.conviction)
    out["changed"] = (p.direction != v["direction"]
                      or abs(float(p.conviction) - float(v["conviction"])) >= 1.0)
    if store:
        plan = {"proposal": p, "event": event, "dossier": dossier, "models": [out["model"]],
                "rounds": 0, "converged": False, "build_id": build_id, "debate_trace": [],
                "size_pct": None}
        st = engine._store(v["company_id"], plan, "replay", run_id=run_id, force=True,
                           variant="replay", replay_of=verdict_id)
        out["stored"] = st
    return out
