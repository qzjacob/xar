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

def _critic_system() -> str:
    """反方 critic 的 system prompt(正文在 models/prompts 注册表,带 version + 源码 sha)。"""
    from ..models import prompts
    return prompts.get("phanny.critic.system").render()


# 兼容既有引用点(测试/其他模块按名字取用);求值一次即可,模板本身无参数。
_CRITIC_SYSTEM = _critic_system()


def _critic_pins() -> list[tuple[str, ...]]:
    """异厂商 critic 钉扎链:每个 challenger 头 + **订阅**兜底(GLM 订阅池;链外无计量回退)。
    2026-07-25 裁定:Phanny 只用订阅模型(minimax/kimi/glm),不再落 deepseek 计费兜底 ——
    某厂商额度耗尽即由订阅兜底承接,兜底也耗尽则该 critic 本轮失败(优雅降级,绝不花钱)。

    2026-07-29:按 subpool 的 per-provider 额度状态**跳过冷却中的厂商**。此前是静态列表,
    某家订阅触限后每轮每名照打不误 —— 钉扎链会轮转到兜底并成功,所以多数时候不抛异常,
    只是每次白烧一发已知无额度的调用(实测一代容器 42 次)。头冷却就直接从兜底起链;
    头与兜底都冷却则这个 critic 位空缺。按 provider 去重:多个 challenger 塌缩到同一家
    等于单模型自博,失去「异厂商」的意义,宁可少而不同。"""
    from ..models import subpool
    ids = [x.strip() for x in (get_settings().phanny_challenger_models or "").split(",") if x.strip()]
    if not ids:
        ids = ["glm-5.2-sub"]
    tail = "glm-5.2-sub"          # 订阅兜底(非计量);与自身相同时不重复前插
    tail_ok = not subpool.cooling(subpool.provider_of(tail))
    out: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for mid in ids:
        prov = subpool.provider_of(mid)
        if not subpool.cooling(prov):
            pin = (mid,) if mid == tail or not tail_ok else (mid, tail)
        elif tail_ok:
            prov, pin = subpool.provider_of(tail), (tail,)
        else:
            continue
        if prov in seen:
            continue
        seen.add(prov)
        out.append(pin)
    return out


def _anchors(p) -> int:
    from ..ontology.phanny_events import anchor_ids
    return len(anchor_ids(p))


def _critic_prompt(cid: str, dossier: dict, prop) -> str:
    from ..models import prompts
    dims = "\n".join(f"- {d.key}: score={d.score} {d.note_zh}" for d in prop.dimensions)
    return prompts.get("phanny.critic.user").render(
        cid, dossier["text"], prop.direction, prop.conviction, dims, prop.asymmetry_zh)


def _rebut_prompt(cid: str, dossier: dict, prop, votes: list[dict]) -> str:
    from ..models import prompts
    vt = "\n".join(f"- [{v['model']}] vote={v['direction_vote']} Δconv={v['conviction_delta']} "
                   f"Δsize={v['size_delta']}: {v['attack_zh']}" for v in votes)
    return prompts.get("phanny.rebut.user").render(
        cid, dossier["text"], prop.direction, prop.conviction, vt)


def run_debate(cid: str, event: dict, dossier: dict, proposal, *,
               run_id: str | None = None, primary_model: str = "token",
               build_id: str | None = None) -> dict:
    from ..models import llm
    from ..models.router import TaskClass
    from ..ontology.phanny_events import CriticVote, PhannyProposal, validate_proposal
    from . import distribution as dist, snapshots

    s = get_settings()
    ed = event.get("scheduled_for") if event else None
    critic_pins = _critic_pins()
    cur = proposal
    r1_conv, r1_anchor = float(cur.conviction), _anchors(cur)
    # 第 0 轮 = 原始提案 p0。此前**从不入痕**,于是「辩论到底改变了什么」事后无从对比,
    # book 的反作弊守卫(禁止靠降 conviction 凑收敛)也无法被复核。
    trace: list[dict] = [{"round": 0, "role": "proposer", "direction": cur.direction,
                          "conviction": float(cur.conviction), "anchors": r1_anchor,
                          "model": primary_model}]
    models_used = [primary_model]
    history = [{"direction": cur.direction, "conviction": float(cur.conviction), "anchors": _anchors(cur)}]
    converged = False
    rnd = 0
    if not critic_pins:
        # 全部订阅厂商都在冷却 = 这一名**无法被质疑**。此时若照跑 max_rounds 轮,每轮都是
        # 「拿空票让 proposer 自我修订」—— 零对抗信息却要烧 5 次 8000-token 的 rebut,且
        # `agree_ok = bool(votes)` 决定它必定不收敛。直接原样返回 p0 并诚实标 converged=False,
        # models 里只有 proposer,事后一眼能看出这稿没被辩过。
        log.warning("phanny debate %s: 无可用 critic(订阅厂商全部冷却)— 跳过辩论,原稿不入辩", cid)
        trace.append({"round": 0, "role": "debate_skipped", "reason": "all_critic_providers_cooling"})
        return {"proposal": cur, "round1_conviction": r1_conv, "final_conviction": float(cur.conviction),
                "round1_anchors": r1_anchor, "final_anchors": r1_anchor, "rounds": 0,
                "models": sorted(set(models_used)), "debate_trace": trace, "converged": False,
                "history": history, "build_id": build_id}
    for rnd in range(1, s.phanny_debate_max_rounds + 1):
        votes: list[dict] = []
        critic_failures = 0
        for pin in list(critic_pins):     # 迭代副本:额度失败会就地把该 pin 从后续轮摘掉
            cm = pin[0]
            cap: dict = {}
            try:
                with llm.pinned(pin):
                    v = llm.complete_json(_critic_prompt(cid, dossier, cur), CriticVote, system=_CRITIC_SYSTEM,
                                          task=TaskClass.PHANNY_CHALLENGE, node=f"phanny_critic:{cm}:{rnd}",
                                          run_id=run_id, max_tokens=3000, reasoning_effort="high",
                                          context={"company_id": cid, "role": "critic",
                                                   "round": rnd, "model": cm},
                                          capture=cap, on_fail="raise")
            except llm.StructuredOutputError as e:
                # 模型答了但不是可解析 JSON —— **不是弃权**。默认兜底会返回一张全默认的
                # CriticVote(direction_vote="abstain"),把解析失败伪装成真实弃权票。
                critic_failures += 1
                log.warning("phanny critic %s r%d parse_failed: %s", cm, rnd, str(e)[:100])
                trace.append({"round": rnd, "role": "critic", "model": cm, "status": "parse_failed"})
                if build_id:
                    snapshots.snap_call(build_id, cid, stage="critic", run_id=run_id, event_date=ed,
                                        round=rnd, model=cm, capture=cap,
                                        template="phanny.critic.user",
                                        meta={"status": "parse_failed"})
                continue
            except Exception as e:  # noqa: BLE001 — 额度耗尽/供应商故障:跳过,余下 critic 继续
                critic_failures += 1
                log.warning("phanny critic %s r%d: %s", cm, rnd, str(e)[:100])
                trace.append({"round": rnd, "role": "critic", "model": cm, "status": "provider_failed",
                              "error": str(e)[:160]})
                # 整条链(头+兜底)都失败才会走到这里。若是额度类,冷却该 provider 并把这条
                # pin 从**后续轮**摘掉 —— 否则 max_rounds 轮会对同一家已耗尽的订阅重复发起
                # 完全相同的调用。非额度类(供应商 5xx/网络抖动)不冷却,下轮照常重试。
                from ..models import subpool
                if subpool.note_failure(subpool.provider_of(cm), e):
                    critic_pins = [p for p in critic_pins if p[0] != cm]
                continue
            models_used.append(cm)
            votes.append({"model": cm, **v.model_dump()})
            trace.append({"round": rnd, "role": "critic", "model": cm, "status": "ok",
                          "vote": v.model_dump()})
            if build_id:
                snapshots.snap_call(build_id, cid, stage="critic", run_id=run_id, event_date=ed,
                                    round=rnd, model=cm, capture=cap,
                                    template="phanny.critic.user",
                                    meta={"status": "ok", "vote": v.direction_vote})

        prev = {"direction": cur.direction, "conviction": float(cur.conviction), "anchors": _anchors(cur)}
        # proposer 据反方修正(pinned 回原方模型)
        from . import engine
        cap_r: dict = {}
        try:
            with llm.pinned(engine._primary_pin()):
                nxt = llm.complete_json(_rebut_prompt(cid, dossier, cur, votes), PhannyProposal,
                                        system=engine._system_phanny(), task=TaskClass.PHANNY_VERDICT,
                                        node=f"phanny_rebut:{rnd}", run_id=run_id, max_tokens=8000,
                                        reasoning_effort="high",
                                        context={"company_id": cid, "role": "rebut", "round": rnd},
                                        capture=cap_r)
            rebut_problems = validate_proposal(nxt, known_ids=dossier["known_ids"])
            if not rebut_problems:
                cur = nxt
            else:
                # 被拒的修正稿此前**无声丢弃**,痕迹里却记着旧状态当作本轮 proposer 输出 ——
                # 事后看不出这一轮其实提了一稿、因何被否。
                log.warning("phanny rebut %s r%d rejected: %d violations",
                            cid, rnd, len(rebut_problems))
                trace.append({"round": rnd, "role": "proposer_rejected",
                              "problems": rebut_problems[:10],
                              "direction": nxt.direction, "conviction": float(nxt.conviction)})
        except Exception as e:  # noqa: BLE001
            log.warning("phanny rebut %s r%d: %s", cid, rnd, str(e)[:100])
            trace.append({"round": rnd, "role": "rebut_failed", "error": str(e)[:160]})
        if build_id:
            snapshots.snap_call(build_id, cid, stage="rebut", run_id=run_id, event_date=ed,
                                round=rnd, model=(engine._primary_pin() or ("token",))[0],
                                capture=cap_r, template="phanny.rebut.user")
        cur_state = {"direction": cur.direction, "conviction": float(cur.conviction), "anchors": _anchors(cur)}
        trace.append({"round": rnd, "role": "proposer", "direction": cur.direction,
                      "conviction": float(cur.conviction), "anchors": cur_state["anchors"],
                      "critic_failures": critic_failures})
        history.append(cur_state)

        active = [v for v in votes if v["direction_vote"] in ("agree", "disagree")]
        agree = sum(1 for v in votes if v["direction_vote"] == "agree")
        # `bool(votes)` 是关键:全体 critic 崩掉时 active 为空,旧式 `(not active)` 会判 True ——
        # 于是「零对抗压力」被当成「一致通过」,第 1 轮即收敛。真正的全体弃权(有票但都 abstain)
        # 仍照旧收敛,因为那是模型的真实表态。
        agree_ok = bool(votes) and ((not active) or (agree / len(active) >= 2 / 3))
        recent = [h["conviction"] for h in history[-3:]]
        conv_stable = (statistics.pstdev(recent) <= s.phanny_convergence_conv_delta) if len(recent) >= 2 else True
        dir_stable = len({h["direction"] for h in history[-2:]}) == 1
        haircut = dist.conviction_only_haircut(prev, cur_state)   # 假收敛?→ 不算收敛
        if agree_ok and conv_stable and dir_stable and not haircut:
            converged = True
            break

    return {"proposal": cur, "round1_conviction": r1_conv, "final_conviction": float(cur.conviction),
            "round1_anchors": r1_anchor, "final_anchors": _anchors(cur), "rounds": rnd,
            "models": sorted(set(models_used)), "debate_trace": trace, "converged": converged,
            "history": history, "build_id": build_id}
