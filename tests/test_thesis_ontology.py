"""Thesis 争论/验证点/衍生指标/种子本体的离线一致性测试 —— 代码即真相守卫。

不碰 DB、不联网:校验(a)CompanyThesis 向后兼容(旧 JSON 无 debates → []);
(b)validate_thesis 对每类争论违规都报错、对合法争论放行;(c)indicators / debates
注册表的完整性不变式(base_metric 锚在 SPEC_BY_KEY、指标 key 不与 KPI 冲突、种子公司/
主题/指标/事件 key 全部合法、key 唯一)。
"""
from __future__ import annotations

from xar.ingestion.registry import COMPANIES, THEMES, company_by_id
from xar.ontology.catalysts import CATALYST_TYPES
from xar.ontology.debates import (
    DEBATE_SEEDS,
    THEME_DEBATES,
    seed_company_ids,
    seeds_for,
)
from xar.ontology.indicators import (
    ALL_INDICATOR_KEYS,
    INDICATORS,
    TRANSFORMS,
    indicator_keys_for_company,
)
from xar.ontology.metric_packs import ALL_METRIC_KEYS, SPEC_BY_KEY
from xar.ontology.thesis import CompanyThesis, validate_thesis

_COMPANY_IDS = {c["id"] for c in COMPANIES}
_LEGAL_METRICS = set(ALL_METRIC_KEYS) | set(ALL_INDICATOR_KEYS)


# ── 测试夹具:最小合法论点 ─────────────────────────────────────────────────────
def _pillar(key: str, weight: float, **o):
    d = dict(key=key, kind="demand", title_zh="标题", claim_zh="可证伪主张:增速 >10%",
             weight=weight, score=0.5,
             evidence=[dict(kind="registry", ref_id="reg:x", quote="q")],
             watch_metrics=[], watch_event_types=[])
    d.update(o)
    return d


def _mk(**o) -> CompanyThesis:
    d = dict(
        one_liner_zh="一句话论点", narrative_zh="位置→驱动→赌注", stance="bull", conviction=3,
        pillars=[_pillar("p1", 0.34), _pillar("p2", 0.33), _pillar("p3", 0.33)],
        bull_case_zh="多头 2 句含数字", bear_case_zh="空头 2 句含数字",
        risks=[dict(type="demand", desc_zh="需求风险", severity=0.3),
               dict(type="valuation", desc_zh="估值风险", severity=0.3)],
    )
    d.update(o)
    return CompanyThesis.model_validate(d)


def _debate(**o) -> dict:
    d = dict(
        key="ai_disrupt_vs_empower", question_zh="AI 颠覆还是赋能?",
        bull_zh="赋能:提价+扩席位", bear_zh="颠覆:Agent 绕开座位模型",
        weight=0.5, lean=0.0, pillar_keys=["p1"],
        verification_points=[dict(
            key="crpo_floor", question_zh="cRPO 增速?", metric="crpo_yoy",
            bull_reading_zh="≥20% 证多", bear_reading_zh="≤12.5% 证空",
            direction="higher_is_bull", bull_threshold=0.20, bear_threshold=0.125)],
    )
    d.update(o)
    return d


# ── 向后兼容 ───────────────────────────────────────────────────────────────────
def test_legacy_thesis_without_debates_roundtrips():
    t = _mk()
    assert t.debates == []
    assert validate_thesis(t, known_indicators={"crpo_yoy"}) == []


# ── 争论/VP 校验 ───────────────────────────────────────────────────────────────
def test_valid_debate_passes():
    t = _mk(debates=[_debate()])
    assert validate_thesis(t, known_kpis=set(), known_indicators={"crpo_yoy"}) == []


def test_bad_vp_metric_flagged():
    t = _mk(debates=[_debate(verification_points=[dict(
        key="v", question_zh="q", metric="not_a_real_key",
        bull_reading_zh="b", bear_reading_zh="be")])])
    probs = validate_thesis(t, known_indicators={"crpo_yoy"})
    assert any("not_a_real_key" in p for p in probs)


def test_bad_vp_event_type_flagged():
    t = _mk(debates=[_debate(verification_points=[dict(
        key="v", question_zh="q", event_types=["totally_bogus"],
        bull_reading_zh="b", bear_reading_zh="be")])])
    probs = validate_thesis(t, known_indicators={"crpo_yoy"})
    assert any("totally_bogus" in p for p in probs)


def test_bad_pillar_key_flagged():
    t = _mk(debates=[_debate(pillar_keys=["nonexistent_pillar"])])
    probs = validate_thesis(t, known_indicators={"crpo_yoy"})
    assert any("nonexistent_pillar" in p for p in probs)


def test_threshold_inversion_flagged():
    t = _mk(debates=[_debate(verification_points=[dict(
        key="v", question_zh="q", metric="crpo_yoy",
        bull_reading_zh="b", bear_reading_zh="be",
        direction="higher_is_bull", bull_threshold=0.10, bear_threshold=0.20)])])
    probs = validate_thesis(t, known_indicators={"crpo_yoy"})
    assert any("bull_threshold < bear_threshold" in p for p in probs)


def test_vp_needs_metric_or_events():
    t = _mk(debates=[_debate(verification_points=[dict(
        key="v", question_zh="q", bull_reading_zh="b", bear_reading_zh="be")])])
    probs = validate_thesis(t, known_indicators={"crpo_yoy"})
    assert any("needs metric or event_types" in p for p in probs)


def test_too_many_debates_flagged():
    ds = [_debate(key=f"d{i}", pillar_keys=[]) for i in range(4)]
    t = _mk(debates=ds)
    probs = validate_thesis(t, known_indicators={"crpo_yoy"})
    assert any("> 3" in p for p in probs)


def test_debate_evidence_validated():
    # 评审 #6:争论证据的 kind/ref_id 也要过纪律(否则幻觉 ref 混入 thesis_evidence)
    d = _debate(evidence=[dict(kind="event", ref_id="hallucinated_99", quote="q")])
    t = _mk(debates=[d])
    probs = validate_thesis(t, known_evidence_ids={"event:1"}, known_indicators={"crpo_yoy"})
    assert any("hallucinated_99" in p for p in probs)
    # 合法 registry 证据豁免存在性检查
    d2 = _debate(evidence=[dict(kind="registry", ref_id="macro:x", quote="q")])
    ok = validate_thesis(_mk(debates=[d2]), known_evidence_ids={"event:1"},
                         known_indicators={"crpo_yoy"})
    assert ok == []


def test_debate_key_pillar_key_collision_flagged():
    # 评审 #1:争论 key 与支柱 key 同名会让证据链接器把该争论的 LLM 道覆盖掉
    t = _mk(debates=[_debate(key="p1")])   # p1 是既有支柱 key
    probs = validate_thesis(t, known_indicators={"crpo_yoy"})
    assert any("collides with a pillar key" in p for p in probs)


def test_debate_cap_allows_required_plus_headroom():
    # 评审 #7:3 个必答种子 + 模型自主补 1 个 = 4 个,不应因 >3 被拒
    reqs = {"d0", "d1", "d2"}
    ds = [_debate(key=k, pillar_keys=[]) for k in ("d0", "d1", "d2", "extra")]
    t = _mk(debates=ds)
    probs = validate_thesis(t, known_indicators={"crpo_yoy"}, required_debate_keys=reqs)
    assert not any("count" in p for p in probs), probs


def test_missing_required_seed_flagged():
    t = _mk(debates=[_debate()])
    probs = validate_thesis(t, known_indicators={"crpo_yoy"},
                            required_debate_keys={"some_seed_key"})
    assert any("some_seed_key" in p for p in probs)
    # 覆盖了就不该报
    ok = validate_thesis(t, known_indicators={"crpo_yoy"},
                         required_debate_keys={"ai_disrupt_vs_empower"})
    assert ok == []


# ── indicators 注册表完整性 ────────────────────────────────────────────────────
def test_indicator_base_metrics_anchor_in_spec():
    for spec in INDICATORS:
        assert spec.transform in TRANSFORMS, f"{spec.key}: bad transform {spec.transform}"
        assert spec.base_metric in SPEC_BY_KEY, f"{spec.key}: base {spec.base_metric} ∉ SPEC_BY_KEY"
        if spec.transform == "ratio_to":
            assert spec.other_metric in SPEC_BY_KEY, f"{spec.key}: other {spec.other_metric} ∉ SPEC_BY_KEY"


def test_indicator_keys_unique_and_no_kpi_collision():
    assert len(ALL_INDICATOR_KEYS) == len(set(ALL_INDICATOR_KEYS)), "duplicate indicator keys"
    collide = set(ALL_INDICATOR_KEYS) & set(SPEC_BY_KEY)
    assert not collide, f"indicator keys collide with canonical KPIs: {sorted(collide)}"


def test_indicator_keys_for_company_software():
    now = company_by_id("now")
    keys = indicator_keys_for_company(now)
    # 软件公司应拿到 SaaS 递延收入衍生指标
    assert "crpo_yoy" in keys and "nrr_trend" in keys
    # 但不该拿到它没有 base KPI 的指标(如餐饮的净新增门店)
    assert "net_new_units_yoy" not in keys


# ── debates 种子注册表完整性(空 tuple 时各断言 vacuously true)──────────────────
def test_seed_company_ids_exist():
    for s in DEBATE_SEEDS:
        assert s.company_id in _COMPANY_IDS, f"seed {s.key}: unknown company {s.company_id}"


def test_seed_metric_and_event_keys_legal():
    for s in DEBATE_SEEDS:
        for m in s.suggested_metrics:
            assert m in _LEGAL_METRICS, f"seed {s.company_id}/{s.key}: metric {m!r} not legal"
        for et in s.suggested_event_types:
            assert et in CATALYST_TYPES, f"seed {s.company_id}/{s.key}: event {et!r} not legal"


def test_theme_debate_themes_and_keys_valid():
    for d in THEME_DEBATES:
        assert d.theme in THEMES, f"theme debate {d.key}: unknown theme {d.theme}"
    all_keys = [d.key for d in THEME_DEBATES]
    assert len(all_keys) == len(set(all_keys)), "duplicate theme-debate keys"


def test_seed_pair_uniqueness():
    pairs = [(s.company_id, s.key) for s in DEBATE_SEEDS]
    assert len(pairs) == len(set(pairs)), "duplicate (company_id, key) seed pairs"


def test_seeds_for_inheritance_and_longtail():
    flagships = seed_company_ids()
    # 长尾公司(无种子)→ 空
    non_seed = next((cid for cid in _COMPANY_IDS if cid not in flagships), None)
    assert non_seed is not None
    assert seeds_for(non_seed, ["ai_software"]) == []
    # 旗舰 → 至少包含其公司级种子的 key
    for cid in list(flagships)[:3]:
        c = company_by_id(cid)
        got = {s.key for s in seeds_for(cid, (c or {}).get("themes"))}
        own = {s.key for s in DEBATE_SEEDS if s.company_id == cid}
        assert own <= got, f"{cid}: seeds_for dropped own seeds {own - got}"
