"""Phanny 本体:universe、PhannyProposal schema、validate 纪律(禁 neutral/禁期权策略/六维齐全/
高信念证据密度)。纯离线,无网络无 DB。"""
from __future__ import annotations

from xar.ontology import phanny_events as pe
from xar.ontology.phanny_events import CriticVote, DimensionRead, PhannyProposal, validate_proposal


def test_universe_all_us_registry_names():
    from xar.ingestion.registry import company_by_id

    assert len(pe.PHANNY_UNIVERSE) == len(set(pe.PHANNY_UNIVERSE)), "duplicate ids"
    for cid in pe.PHANNY_UNIVERSE:
        c = company_by_id(cid)
        assert c is not None, f"{cid} not in registry"
        tks = c.get("tickers") or []
        assert tks and "." not in tks[0], f"{cid}: not US-listed ({tks})"


def test_universe_resolves_and_caps():
    u = pe.phanny_universe()
    assert 15 <= len(u) <= 40 and all(isinstance(c, dict) for c in u)
    assert len(pe.phanny_universe(cap=5)) == 5


def _dims(n=6, score=1.0):
    return [DimensionRead(key=k, score=score, note_zh="x", evidence=[f"estimate:now:m{i}"])
            for i, k in enumerate(pe.PHANNY_DIMENSIONS[:n])]


def _known(n=6):
    return {f"estimate:now:m{i}" for i in range(n)}


def test_valid_high_conviction_long_passes():
    p = PhannyProposal(direction="long", conviction=8.0, dimensions=_dims(6),
                       asymmetry_zh="下行有限上行大", plan_zh="T-3 进场，财报后了结",
                       falsifiers_zh=["指引下修"], prob_bins=[0.3, 0.3, 0.2, 0.15, 0.05], e_return_pct=3.2)
    assert validate_proposal(p, known_ids=_known()) == []


def test_low_conviction_needs_direction_but_not_asymmetry():
    p = PhannyProposal(direction="short", conviction=3.0, dimensions=_dims(6))
    assert validate_proposal(p, known_ids=_known()) == []


def test_neutral_and_no_trade_rejected():
    for bad in ("neutral", "no_trade", "hold"):
        p = PhannyProposal(direction=bad, conviction=5.0, dimensions=_dims(6))
        probs = validate_proposal(p, known_ids=_known())
        assert any("not in" in x for x in probs), bad


def test_dimensions_must_be_complete_six():
    p = PhannyProposal(direction="long", conviction=4.0, dimensions=_dims(4))
    probs = validate_proposal(p, known_ids=_known())
    assert any("incomplete" in x for x in probs)


def test_hallucinated_evidence_rejected():
    dims = _dims(6)
    dims[0].evidence = ["estimate:FAKE:zzz"]
    p = PhannyProposal(direction="long", conviction=4.0, dimensions=dims)
    probs = validate_proposal(p, known_ids=_known())
    assert any("unknown evidence" in x for x in probs)


def test_high_conviction_needs_anchors_asymmetry_falsifier():
    # 6 dims but only 3 distinct anchors, no asymmetry, no falsifier
    dims = [DimensionRead(key=k, score=1.0, note_zh="x", evidence=["estimate:now:m0"])
            for k in pe.PHANNY_DIMENSIONS]
    p = PhannyProposal(direction="long", conviction=9.0, dimensions=dims)
    probs = validate_proposal(p, known_ids={"estimate:now:m0"})
    assert any("anchors" in x for x in probs)
    assert any("asymmetry" in x for x in probs)
    assert any("falsifier" in x for x in probs)


def test_option_strategy_in_plan_rejected():
    for kw in ("卖出 iron condor 收权利金", "构建 long straddle", "做多跨式"):
        p = PhannyProposal(direction="long", conviction=4.0, dimensions=_dims(6), plan_zh=kw)
        probs = validate_proposal(p, known_ids=_known())
        assert any("期权" in x for x in probs), kw


def test_prob_bins_must_sum_to_one():
    p = PhannyProposal(direction="long", conviction=4.0, dimensions=_dims(6),
                       prob_bins=[0.5, 0.5, 0.5, 0.5, 0.5])
    probs = validate_proposal(p, known_ids=_known())
    assert any("sum to" in x for x in probs)


def test_critic_vote_schema():
    v = CriticVote(direction_vote="disagree", conviction_delta=-1.5, size_delta=-2.0, attack_zh="a")
    assert v.direction_vote == "disagree" and v.conviction_delta == -1.5


def test_router_has_phanny_tasks():
    from xar.models.router import POLICIES, TaskClass
    assert TaskClass.PHANNY_VERDICT in POLICIES and TaskClass.PHANNY_CHALLENGE in POLICIES
