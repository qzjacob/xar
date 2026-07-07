"""非标投研文档本体(代码即真相)。

给两家 CN 数据商的**非标准化语义文档**——券商研报 / 会议·业绩会纪要 / 专家纪要 /
经营讨论 MD&A / 既有 agent 一页通类——建立类型语义,决定每类文档:走哪条抽取道
(expert 语义洞见 / 仅 KG 事件 / 不抽)、build_kg 队列优先级、是否走零 LLM 评级第二遍、
典型催化类型与影响的论点支柱 kind。这些是让纪要/研报事实流进 semantic_facts →
research/thesis.py dossier「语义事实」→ research/evidence_link.py 相对主张分类的类型骨架。

镜像 ontology/altdata.py 的"frozen dataclass 元组注册表 + import 时自检不变式"模式;
kg_priority_case() 用可信常量编译 SQL CASE(同 storage/structured.source_priority_sql 手法,
单一真相防两处漂移)。经 ontology/__init__ 导出。
"""
from __future__ import annotations

from dataclasses import dataclass

from .catalysts import CATALYST_TYPES
from .thesis import PILLAR_KINDS


@dataclass(frozen=True)
class ResearchDocSpec:
    doc_type: str                   # documents.doc_type 取值(全局唯一)
    label_zh: str
    vendor: str                     # 'gangtise' | 'aifinmarket'
    endpoint: str                   # 文档化真相(open-insight/broker-report/getList …)
    kb_resource_type: int | None    # Gangtise FILE_TYPE_MAP 码(10/40/60);非 KB 检索类=None
    extraction: str = "expert"      # 'expert'(语义洞见道)| 'kg_only' | 'none'
    rating_extractor: bool = False  # True → 零 LLM 评级/目标价第二遍(broker_report)
    catalyst_types: tuple[str, ...] = ()   # 典型催化类型,⊆ CATALYST_TYPES(自检)
    pillar_kinds: tuple[str, ...] = ()     # ⊆ thesis.PILLAR_KINDS(自检)
    kg_priority: int = 1            # build_kg ORDER CASE(0 最高;研报/纪要=1,与 cninfo/news 平级)
    cadence_hours: int = 24         # 新鲜度 SLO(审计对账用)
    body: str = "brief"             # 'brief'(保守:brief+essence)| 'full_core'(预留,本期不用)
    permission: str = "grey"
    license_tag: str = "gangtise-research-extracted-facts-self-use"
    rationale_zh: str = ""


_R = ResearchDocSpec

RESEARCH_DOCS: tuple[ResearchDocSpec, ...] = (
    _R("broker_report", "券商研报", "gangtise", "open-insight/broker-report/getList", 10,
       rating_extractor=True,
       catalyst_types=("earnings", "guidance_change", "contract_win", "tech_substitution"),
       pillar_kinds=("demand", "moat", "financials", "valuation"),
       rationale_zh="卖方深度/点评/行业策略;元数据含评级/目标价 → 零 LLM 评级第二遍。"),
    _R("meeting_minutes", "会议·业绩会纪要", "gangtise", "open-insight/summary/v2/getList", 60,
       catalyst_types=("earnings", "guidance_change", "product_ramp", "order"),
       pillar_kinds=("demand", "financials", "technology"),
       rationale_zh="业绩会/策略会/路演纪要;essence 精华段落即高浓度语义。"),
    _R("expert_minutes", "专家纪要", "gangtise", "open-insight/summary/v2/getList", 60,
       catalyst_types=("tech_substitution", "supply_constraint", "product_ramp"),
       pillar_kinds=("technology", "supply_chain", "demand"),
       rationale_zh="participantRoleList 含 expert / guest 的专家交流纪要——链上一手草根验证。"),
    _R("mgmt_discussion", "经营讨论 MD&A", "gangtise", "open-ai/management_discuss", None,
       catalyst_types=("earnings", "guidance_change"),
       pillar_kinds=("demand", "financials"),
       rationale_zh="from-earningsCall/from-announcement 结构化经营讨论,按 reportDate 取历史季度。"),
    # 既有 agent 类型纳入注册表(vendor 完备;pull_research 已落库,extraction='expert')
    _R("one_pager", "一页通", "gangtise", "open-ai/agent/one-pager", None,
       catalyst_types=("earnings",), pillar_kinds=("demand", "moat", "valuation"),
       rationale_zh="个股一页通投资概要。"),
    _R("investment_logic", "投资逻辑", "gangtise", "open-ai/agent/investment-logic", None,
       catalyst_types=("earnings",), pillar_kinds=("demand", "moat", "technology"),
       rationale_zh="个股投资逻辑主线。"),
    _R("peer_comparison", "同业对比", "gangtise", "open-ai/agent/peer-comparison", None,
       pillar_kinds=("moat", "valuation"),
       rationale_zh="同业横向对比。"),
)

DOCS_BY_TYPE: dict[str, ResearchDocSpec] = {s.doc_type: s for s in RESEARCH_DOCS}
EXPERT_DOC_TYPES: frozenset[str] = frozenset(s.doc_type for s in RESEARCH_DOCS if s.extraction == "expert")
RATED_DOC_TYPES: frozenset[str] = frozenset(s.doc_type for s in RESEARCH_DOCS if s.rating_extractor)
RESEARCH_SOURCES: frozenset[str] = frozenset(s.vendor for s in RESEARCH_DOCS)   # {'gangtise'}


def kg_priority_case(col: str = "doc_type") -> str:
    """build_kg ORDER BY 里注入的可信常量 CASE 片段(同 structured.source_priority_sql 手法)。"""
    whens = " ".join(f"WHEN '{s.doc_type}' THEN {s.kg_priority}" for s in RESEARCH_DOCS)
    return f"CASE {col} {whens} ELSE 3 END"


# ── 代码即真相自检(import 时执行;test_research_docs.py 再断言一遍)──────────────
assert len({s.doc_type for s in RESEARCH_DOCS}) == len(RESEARCH_DOCS), "duplicate research doc_type"
for _s in RESEARCH_DOCS:
    assert set(_s.catalyst_types) <= set(CATALYST_TYPES), f"{_s.doc_type}: bad catalyst_type"
    assert set(_s.pillar_kinds) <= set(PILLAR_KINDS), f"{_s.doc_type}: bad pillar_kind"
    assert _s.extraction in ("expert", "kg_only", "none"), f"{_s.doc_type}: bad extraction"
    assert _s.body in ("brief", "full_core"), f"{_s.doc_type}: bad body"
