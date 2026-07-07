"""ET-P0:季报事件本体 —— universe 约束、EarningsVerdict schema、validate 五规则、路由存在。
纯离线,无网络无 DB。"""
from __future__ import annotations

from xar.ontology import earnings_events as ee
from xar.ontology.earnings_events import DimensionRead, EarningsVerdict, validate_verdict


def test_universe_all_us_registry_names():
    from xar.ingestion.registry import company_by_id

    assert len(ee.EARNINGS_UNIVERSE) == len(set(ee.EARNINGS_UNIVERSE)), "duplicate ids in universe"
    for cid in ee.EARNINGS_UNIVERSE:
        c = company_by_id(cid)
        assert c is not None, f"{cid} not in registry"
        tks = c.get("tickers") or []
        assert tks and "." not in tks[0], f"{cid}: not a US-listed ticker ({tks})"


def test_earnings_universe_resolves_and_caps():
    u = ee.earnings_universe()
    assert 20 <= len(u) <= 50 and all(isinstance(c, dict) for c in u)
    assert len(ee.earnings_universe(cap=5)) == 5


def _good_dims(n=6):
    # n 个不同维度,每个带 1 个接地 id
    keys = ee.EARNINGS_DIMENSIONS[:n]
    return [DimensionRead(key=k, score=1.0, note_zh="x", evidence=[f"estimate:now:m{i}"])
            for i, k in enumerate(keys)]


def _known(n=6):
    return {f"estimate:now:m{i}" for i in range(n)}


def test_valid_high_conviction_verdict_passes():
    v = EarningsVerdict(direction="long", conviction=7.5, expected_surprise_zh="beat",
                        move_view_zh="implied 便宜", dimensions=_good_dims(6), plan_zh="T-3 进",
                        falsifiers_zh=["指引下修"], asymmetry_zh="下行有限上行大")
    assert validate_verdict(v, known_ids=_known(6)) == []


def test_high_conviction_needs_six_anchors():
    v = EarningsVerdict(direction="long", conviction=8.0, expected_surprise_zh="b",
                        move_view_zh="m", dimensions=_good_dims(4), plan_zh="p",
                        falsifiers_zh=["f"], asymmetry_zh="a")
    probs = validate_verdict(v, known_ids=_known(6))
    assert any("distinct evidence anchors" in p for p in probs)


def test_high_conviction_needs_asymmetry():
    v = EarningsVerdict(direction="long", conviction=7.0, expected_surprise_zh="b",
                        move_view_zh="m", dimensions=_good_dims(6), plan_zh="p",
                        falsifiers_zh=["f"], asymmetry_zh="")
    assert any("asymmetry_zh" in p for p in validate_verdict(v, known_ids=_known(6)))


def test_hallucinated_evidence_rejected():
    dims = _good_dims(4)
    dims[0] = DimensionRead(key="consensus_setup", score=1, note_zh="x",
                            evidence=["estimate:GHOST:x"])   # 幻觉 id
    v = EarningsVerdict(direction="long", conviction=5.0, expected_surprise_zh="b",
                        move_view_zh="m", dimensions=dims, plan_zh="p", falsifiers_zh=["f"])
    assert any("unknown evidence" in p for p in validate_verdict(v, known_ids=_known(6)))


def test_no_trade_must_be_zero_conviction():
    v = EarningsVerdict(direction="no_trade", conviction=4.0, expected_surprise_zh="b",
                        move_view_zh="m", dimensions=_good_dims(4), plan_zh="p",
                        falsifiers_zh=["f"], no_trade_reason_zh="无 edge")
    assert any("conviction=0" in p for p in validate_verdict(v))
    # 正确的 no_trade
    v2 = v.model_copy(update={"conviction": 0.0})
    assert validate_verdict(v2, known_ids=_known(4)) == []


def test_bad_direction_and_dupe_dimension():
    v = EarningsVerdict(direction="sideways", conviction=0.0, expected_surprise_zh="b",
                        move_view_zh="m",
                        dimensions=[DimensionRead(key="event_risk", score=0, note_zh="x"),
                                    DimensionRead(key="event_risk", score=0, note_zh="y"),
                                    DimensionRead(key="bogus_dim", score=0, note_zh="z"),
                                    DimensionRead(key="valuation_cushion", score=0, note_zh="w")],
                        plan_zh="p", falsifiers_zh=["f"], no_trade_reason_zh="n")
    probs = validate_verdict(v, known_ids=set())
    assert any("direction" in p for p in probs)
    assert any("duplicated" in p for p in probs)
    assert any("invalid" in p for p in probs)


def test_earnings_judge_routes_strong_token():
    from xar.models import registry, router
    from xar.models.router import TaskClass

    pol = router.POLICIES[TaskClass.EARNINGS_JUDGE]
    assert pol.capability == registry.Capability.STRONG
    assert pol.prefer_billing == registry.Billing.TOKEN.value
    chain = [m.id for m in router.resolve(TaskClass.EARNINGS_JUDGE)]
    assert chain and any("deepseek" in c for c in chain)   # token strong lead present
