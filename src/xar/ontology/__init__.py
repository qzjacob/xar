"""Industry-chain ontology — code IS the source of truth (per design §5)."""
from . import cycle, earnings_events, metric_packs, research_docs, sectors
from .catalysts import CATALYST_TYPES, LEGACY_CATALYST_TYPES, CatalystType
from .earnings_events import (
    EARNINGS_DIMENSIONS,
    EARNINGS_UNIVERSE,
    EarningsVerdict,
    earnings_universe,
    validate_verdict,
)
from .research_docs import DOCS_BY_TYPE, EXPERT_DOC_TYPES, RESEARCH_DOCS, ResearchDocSpec
from .cycle import (
    CYCLE_LABELS,
    CYCLE_RANK,
    CycleProfile,
    CyclePosition,
    Cyclicality,
    cycle_of_company,
)
from .edges import EDGE_TYPES, EdgeType
from .metric_packs import (
    ALL_METRIC_KEYS,
    MetricSpec,
    canonical_kpi,
    is_higher_better,
    kpi_labels_for_company,
    kpis_for_company,
    kpis_for_industry,
)
from .nodes import NODE_TYPES, COMPANY_NODE_TYPES, NodeType, is_company_node
from .schema import (
    ExtractedEdge,
    ExtractedEvent,
    ExtractedMetric,
    ExtractedNode,
    ExtractionResult,
)
from .sectors import (
    INDUSTRIES,
    SECTORS,
    Industry,
    Sector,
    classify,
    industry_of_company,
    sector_of_company,
)
from .standards import (
    FIN_METRICS,
    RATIO_METRICS,
    SIGNAL_TO_CATALYST,
    FinMetric,
    canonical_metric,
    edge_iri,
    node_iri,
)

__all__ = [
    "NodeType",
    "NODE_TYPES",
    "COMPANY_NODE_TYPES",
    "is_company_node",
    "EdgeType",
    "EDGE_TYPES",
    "CatalystType",
    "CATALYST_TYPES",
    "LEGACY_CATALYST_TYPES",
    "ExtractedNode",
    "ExtractedEdge",
    "ExtractedEvent",
    "ExtractedMetric",
    "ExtractionResult",
    # sector taxonomy
    "Sector",
    "Industry",
    "SECTORS",
    "INDUSTRIES",
    "classify",
    "industry_of_company",
    "sector_of_company",
    # metric packs
    "MetricSpec",
    "ALL_METRIC_KEYS",
    "kpis_for_company",
    "kpis_for_industry",
    "kpi_labels_for_company",
    "canonical_kpi",
    "is_higher_better",
    # financial-metric vocabulary / standards
    "FinMetric",
    "FIN_METRICS",
    "RATIO_METRICS",
    "SIGNAL_TO_CATALYST",
    "canonical_metric",
    "node_iri",
    "edge_iri",
    "metric_packs",
    "sectors",
    # economic-cycle dimension
    "cycle",
    "CyclePosition",
    "Cyclicality",
    "CycleProfile",
    "CYCLE_RANK",
    "CYCLE_LABELS",
    "cycle_of_company",
    # non-standard research-document ontology
    "research_docs",
    "earnings_events",
    "EARNINGS_DIMENSIONS",
    "EARNINGS_UNIVERSE",
    "EarningsVerdict",
    "earnings_universe",
    "validate_verdict",
    "ResearchDocSpec",
    "RESEARCH_DOCS",
    "DOCS_BY_TYPE",
    "EXPERT_DOC_TYPES",
]
