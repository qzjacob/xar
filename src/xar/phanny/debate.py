"""Phanny 多 LLM 对抗辩论引擎(核心)。

每轮:N 个**异厂商** critic 对当前 proposal 提 signed-Δ 反方(direction_vote/conviction_delta/
size_delta/attack/rebuttal)→ proposer(原方模型)据反方修正 → 收敛判据:
  ≥2/3 critic 同向 ∧ 近轮 conviction std ≤ 阈 ∧ **非 conviction_only_haircut(假收敛)**。
到顶未收敛 → converged=False(不自动放行,由 book 的正态门/REDEBATE 处理)。
critic 钉扎不同订阅厂商(config.phanny_challenger_models),相邻实现"多 LLM"而非单模型自博。
"""
from __future__ import annotations

import statistics

from ..config import get_settings
from ..logging import get_logger

log = get_logger("xar.phanny.debate")

_CRITIC_SYSTEM = """你是季报事件多空交易的**反方 critic**。给你 dossier 与一份 PhannyProposal(某方向 + conviction)。
你的职责是构建**最强反方**:攻击最弱的维度、列举证伪证据、提出替代叙事。铁律:
- attack_zh 里每条论据尽量引用 dossier 的接地 id;禁止空喊;
- direction_vote ∈ {agree, disagree, abstain}:证据不足或 dossier 太薄(事实<4)→ abstain;真同意才 agree;
  **禁止反射式同意**——若 agree 也要在 attack_zh 指出至少一个残留风险;
- conviction_delta(-2..+2)、size_delta(-3..+3)给你认为应调整的方向与幅度(signed);
- rebuttal_zh:即便你反对,也把原方向的最强钢人版写出来(供裁决权衡)。"""


def _critic_pins() -> list[tuple[str, ...]]:
    """异厂商 critic 钉扎链:每个 challenger 头 + **订阅**兜底(GLM 订阅池;链外无计量回退)。
    2026-07-25 裁定:Phanny 只用订阅模型(minimax/kimi/glm),不再落 deepseek 计费兜底 ——
    某厂商额度耗尽即由订阅兜底承接,兜底也耗尽则该 critic 本轮失败(优雅降级,绝不花钱)。"""
    ids = [x.strip() for x in (get_settings().phanny_challenger_models or "").split(",") if x.strip()]
    if not ids:
        ids = ["glm-5.2-sub"]
    tail = "glm-5.2-sub"          # 订阅兜底(非计量);与自身相同时不重复前插
    return [((mid,) if mid == tail else (mid, tail)) for mid in ids]


def _anchors(p) -> int:
    from ..ontology.phanny_events import anchor_ids
    return len(anchor_ids(p))


def _critic_prompt(cid: str, dossier: dict, prop) -> str:
    dims = "\n".join(f"- {d.key}: score={d.score} {d.note_zh}" for d in prop.dimensions)
    return (f"公司 {cid} · dossier(接地事实):\n{dossier['text']}\n\n"
            f"待挑战的 PhannyProposal:方向={prop.direction} conviction={prop.conviction}\n"
            f"维度:\n{dims}\n赔率不对称:{prop.asymmetry_zh or '(未给)'}\n"
            f"给出你的 signed-Δ 反方 CriticVote。")


def _rebut_prompt(cid: str, dossier: dict, prop, votes: list[dict]) -> str:
    vt = "\n".join(f"- [{v['model']}] vote={v['direction_vote']} Δconv={v['conviction_delta']} "
                   f"Δsize={v['size_delta']}: {v['attack_zh']}" for v in votes)
    return (f"公司 {cid} · dossier:\n{dossier['text']}\n\n"
            f"你上一稿:方向={prop.direction} conviction={prop.conviction}。\n"
            f"多位异厂商 critic 的反方意见:\n{vt}\n\n"
            f"据此**修正并重出一个完整 PhannyProposal**(仍六维齐全、仍 long/short、evidence 接地)。"
            f"若被说服则改方向/降信念;若能反驳则维持并强化 asymmetry_zh;"
            f"**严禁仅靠降低 conviction 来平息分歧**——要么补强证据维持,要么因证据真的转向而改判。")


def run_debate(cid: str, event: dict, dossier: dict, proposal, *,
               run_id: str | None = None, primary_model: str = "token") -> dict:
    from ..models import llm
    from ..models.router import TaskClass
    from ..ontology.phanny_events import CriticVote, PhannyProposal, validate_proposal
    from . import distribution as dist

    s = get_settings()
    critic_pins = _critic_pins()
    cur = proposal
    r1_conv, r1_anchor = float(cur.conviction), _anchors(cur)
    trace: list[dict] = []
    models_used = [primary_model]
    history = [{"direction": cur.direction, "conviction": float(cur.conviction), "anchors": _anchors(cur)}]
    converged = False
    rnd = 0
    for rnd in range(1, s.phanny_debate_max_rounds + 1):
        votes: list[dict] = []
        for pin in critic_pins:
            cm = pin[0]
            try:
                with llm.pinned(pin):
                    v = llm.complete_json(_critic_prompt(cid, dossier, cur), CriticVote, system=_CRITIC_SYSTEM,
                                          task=TaskClass.PHANNY_CHALLENGE, node=f"phanny_critic:{cm}:{rnd}",
                                          run_id=run_id, max_tokens=3000, reasoning_effort="high")
            except Exception as e:  # noqa: BLE001 — 某厂商额度耗尽/失败:跳过,余下 critic 继续
                log.warning("phanny critic %s r%d: %s", cm, rnd, str(e)[:100])
                continue
            models_used.append(cm)
            votes.append({"model": cm, **v.model_dump()})
            trace.append({"round": rnd, "role": "critic", "model": cm, "vote": v.model_dump()})

        prev = {"direction": cur.direction, "conviction": float(cur.conviction), "anchors": _anchors(cur)}
        # proposer 据反方修正(pinned 回原方模型)
        from . import engine
        try:
            with llm.pinned(engine._primary_pin()):
                nxt = llm.complete_json(_rebut_prompt(cid, dossier, cur, votes), PhannyProposal,
                                        system=engine._system_phanny(), task=TaskClass.PHANNY_VERDICT,
                                        node=f"phanny_rebut:{rnd}", run_id=run_id, max_tokens=8000,
                                        reasoning_effort="high")
            if not validate_proposal(nxt, known_ids=dossier["known_ids"]):
                cur = nxt
        except Exception as e:  # noqa: BLE001
            log.warning("phanny rebut %s r%d: %s", cid, rnd, str(e)[:100])
        cur_state = {"direction": cur.direction, "conviction": float(cur.conviction), "anchors": _anchors(cur)}
        trace.append({"round": rnd, "role": "proposer", "direction": cur.direction,
                      "conviction": float(cur.conviction), "anchors": cur_state["anchors"]})
        history.append(cur_state)

        active = [v for v in votes if v["direction_vote"] in ("agree", "disagree")]
        agree = sum(1 for v in votes if v["direction_vote"] == "agree")
        agree_ok = (not active) or (agree / len(active) >= 2 / 3)
        recent = [h["conviction"] for h in history[-3:]]
        conv_stable = (statistics.pstdev(recent) <= s.phanny_convergence_conv_delta) if len(recent) >= 2 else True
        dir_stable = len({h["direction"] for h in history[-2:]}) == 1
        haircut = dist.conviction_only_haircut(prev, cur_state)   # 假收敛?→ 不算收敛
        if agree_ok and conv_stable and dir_stable and not haircut:
            converged = True
            break

    return {"proposal": cur, "round1_conviction": r1_conv, "final_conviction": float(cur.conviction),
            "round1_anchors": r1_anchor, "final_anchors": _anchors(cur), "rounds": rnd,
            "models": sorted(set(models_used)), "debate_trace": trace, "converged": converged, "history": history}
