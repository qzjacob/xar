"""投资论点(CompanyThesis)本体 —— 单一公司 360° 决策对象的代码即真相层。

Palantir 式的核心动作:论点不是自由文本,而是**类型化对象**——每个支柱(pillar)的
每条主张都以类型化外键锚回平台事实(kg_event / kg_edge / chunk / expert_insight /
fundamental / estimate),前端点开即见逐字证据;每个支柱声明自己的 watch_metrics /
watch_event_types,新事实到达时论点健康度(thesis health)可机器复核,而非等人重读。

这些 Pydantic 模型同时充当 LLM 结构化输出 schema(经 models.llm.complete_json),
与 ontology/schema.py 的 ExtractionResult 同一模式。存储见 storage/schema.sql 的
company_thesis / thesis_evidence;生成管线见 research/thesis.py;评分口径见
ontology/coverage360.py。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .catalysts import CATALYST_TYPES

# ── 受控词表(代码即真相;生成与校验共用)──────────────────────────────────────
PILLAR_KINDS: tuple[str, ...] = (
    "demand",         # 需求/量价:终端需求引擎与订单动能
    "moat",           # 竞争位置:份额/定价权/切换成本/技术壁垒
    "supply_chain",   # 链上位置:卡位/单一来源/客户集中度
    "technology",     # 技术路线:代际迁移中的赢面(tr_* 路线)
    "financials",     # 财务质量:盈利结构/现金流/资产负债
    "valuation",      # 估值:定价了什么/没定价什么
    "policy",         # 政策与监管:补贴/管制/合规
    "cyclical",       # 周期位置:所处周期相位与弹性
)

RISK_TYPES: tuple[str, ...] = (
    "demand", "competition", "technology_shift", "supply_chain",
    "customer_concentration", "regulatory", "financial", "valuation",
    "execution", "geopolitical",
)

STANCES: tuple[str, ...] = ("bull", "neutral", "bear")

EVIDENCE_KINDS: tuple[str, ...] = (
    "event",        # kg_events.id
    "edge",         # kg_edges.id
    "chunk",        # chunks.id(文档段落,含上传 Data Room 文档)
    "insight",      # expert_insights.id
    "fundamental",  # fundamentals:ref_id = "<company_id>:<metric>"
    "estimate",     # estimates:  ref_id = "<company_id>:<metric>"
    "registry",     # 本体注册表事实(themes/segments/edges bootstrap),ref_id 自由文本
)


class ThesisEvidence(BaseModel):
    """一条主张的证据锚。ref_id 必须来自提示词给出的事实清单,禁止编造。"""
    kind: str = Field(description=f"one of {EVIDENCE_KINDS}")
    ref_id: str = Field(description="the fact id EXACTLY as listed in the dossier")
    quote: str = Field(default="", description="short verbatim quote from that fact (≤160 chars)")

    def model_post_init(self, _ctx) -> None:  # noqa: D105
        # 容错归一化:模型常把 dossier 里的 "[event:261]" 整体抄进 ref_id;语义不变,剥掉前缀。
        prefix = f"{self.kind}:"
        if self.ref_id.startswith(prefix):
            object.__setattr__(self, "ref_id", self.ref_id[len(prefix):])


class ThesisPillar(BaseModel):
    key: str = Field(description="stable slug for this pillar, e.g. 'ai_demand_engine'")
    kind: str = Field(description=f"one of {PILLAR_KINDS}")
    title_zh: str = Field(description="支柱标题(≤12字)")
    claim_zh: str = Field(description="该支柱的可证伪主张(1–2 句,断言式,含数字更佳)")
    weight: float = Field(ge=0, le=1, description="对整体论点的权重,所有支柱合计≈1")
    score: float = Field(ge=-1, le=1, description="当前证据对该主张的支持度:-1 强反证 … +1 强支持")
    evidence: list[ThesisEvidence] = Field(
        default_factory=list, description="≥1 条;每条主张必须锚到 dossier 中的事实")
    watch_metrics: list[str] = Field(
        default_factory=list,
        description="canonical KPI keys to monitor for this pillar (from the dossier's KPI list)")
    watch_event_types: list[str] = Field(
        default_factory=list, description=f"catalyst types that move this pillar, from {CATALYST_TYPES}")
    falsifier_zh: str = Field(
        default="", description="什么样的事实出现即证伪该支柱(判定式,可被机器比对)")


class ThesisDriver(BaseModel):
    name: str = Field(description="驱动因子,e.g. 'AI capex', '800G→1.6T 迁移'")
    direction: str = Field(default="tailwind", description="tailwind | headwind")
    weight: float = Field(ge=0, le=1, default=0.3)
    note_zh: str = Field(default="", description="一句因果链说明")


class ThesisRisk(BaseModel):
    type: str = Field(description=f"one of {RISK_TYPES}")
    desc_zh: str = Field(description="风险描述(1 句,具体到主体与传导路径)")
    severity: float = Field(ge=0, le=1, description="0 轻微 … 1 论点级(证伪主论点)")
    watch_zh: str = Field(default="", description="盯什么信号能提前看到它兑现")
    evidence: list[ThesisEvidence] = Field(default_factory=list)


class ValuationScenario(BaseModel):
    case: str = Field(description="bull | base | bear")
    method_zh: str = Field(description="估值方法/倍数口径,e.g. '2027E EPS × 25x'")
    assumption_zh: str = Field(description="关键假设(1 句,含数字)")
    implied_view_zh: str = Field(description="该情形下市场当前定价意味着什么")


class WatchItem(BaseModel):
    what_zh: str = Field(description="盯的事项(事件/数据/日期)")
    when: str = Field(default="", description="ISO date or period if known, e.g. '2026-08' or 'Q3'")
    pillar_key: str = Field(default="", description="影响哪个支柱(pillar.key)")
    direction_zh: str = Field(default="", description="怎么读:超预期/不及预期分别意味着什么")


class CompanyThesis(BaseModel):
    """LLM 生成的完整论点对象(company_thesis.content)。所有主张必须可溯源。"""
    one_liner_zh: str = Field(description="一句话论点(≤40字):这家公司为什么值得/不值得关注")
    narrative_zh: str = Field(description="论点叙事(≤3 句):位置 → 驱动 → 变化中的赌注")
    stance: str = Field(description=f"one of {STANCES}")
    conviction: float = Field(ge=1, le=5, description="信念度 1–5;必须与证据密度一致,证据薄弱不得>3")
    pillars: list[ThesisPillar] = Field(description="3–6 个类型化支柱,权重合计≈1")
    drivers: list[ThesisDriver] = Field(default_factory=list, description="3–5 个加权驱动因子")
    bull_case_zh: str = Field(description="多头情形(2–3 句,含关键数字)")
    bear_case_zh: str = Field(description="空头情形(2–3 句,含关键数字)")
    variant_perception_zh: str = Field(
        default="", description="变体认知:我们与共识的分歧点是什么(没有就留空,不要硬编)")
    risks: list[ThesisRisk] = Field(description="2–5 条类型化风险")
    valuation: list[ValuationScenario] = Field(default_factory=list, description="bull/base/bear 三情形")
    what_to_watch: list[WatchItem] = Field(default_factory=list, description="未来 1–2 季度盯什么")
    coverage_gaps_zh: list[str] = Field(
        default_factory=list,
        description="诚实声明:哪些维度证据不足(如'无 capex 时序''无 CN 分析师覆盖')——宁缺毋滥")


# ── 校验(生成管线在入库前调用;宁可拒绝不可污染)────────────────────────────────
def validate_thesis(t: CompanyThesis, *, known_evidence_ids: set[str] | None = None,
                    known_kpis: set[str] | None = None) -> list[str]:
    """返回违规清单(空 = 通过)。known_evidence_ids 形如 {'event:123', 'chunk:ab'}。"""
    problems: list[str] = []
    if t.stance not in STANCES:
        problems.append(f"stance {t.stance!r} not in {STANCES}")
    if not (3 <= len(t.pillars) <= 6):
        problems.append(f"pillars count {len(t.pillars)} not in 3..6")
    wsum = sum(p.weight for p in t.pillars)
    if t.pillars and not (0.8 <= wsum <= 1.2):
        problems.append(f"pillar weights sum {wsum:.2f} not ≈1")
    for p in t.pillars:
        if p.kind not in PILLAR_KINDS:
            problems.append(f"pillar {p.key}: kind {p.kind!r} invalid")
        if not p.evidence:
            problems.append(f"pillar {p.key}: no evidence")
        for ev in p.evidence:
            if ev.kind not in EVIDENCE_KINDS:
                problems.append(f"pillar {p.key}: evidence kind {ev.kind!r} invalid")
            elif known_evidence_ids is not None and ev.kind != "registry" \
                    and f"{ev.kind}:{ev.ref_id}" not in known_evidence_ids:
                problems.append(f"pillar {p.key}: unknown evidence {ev.kind}:{ev.ref_id}")
        for et in p.watch_event_types:
            if et not in CATALYST_TYPES:
                problems.append(f"pillar {p.key}: watch_event_type {et!r} invalid")
        if known_kpis is not None:
            for m in p.watch_metrics:
                if m not in known_kpis:
                    problems.append(f"pillar {p.key}: watch_metric {m!r} not canonical")
    for r in t.risks:
        if r.type not in RISK_TYPES:
            problems.append(f"risk type {r.type!r} invalid")
    # 证据密度 ↔ 信念度纪律:总证据 <5 条时 conviction 不得超过 3
    n_ev = sum(len(p.evidence) for p in t.pillars) + sum(len(r.evidence) for r in t.risks)
    if n_ev < 5 and t.conviction > 3:
        problems.append(f"conviction {t.conviction} too high for {n_ev} evidence anchors")
    return problems
