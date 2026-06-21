"""Industry-chain ontology — code IS the source of truth (per design §5)."""
from .catalysts import CATALYST_TYPES, CatalystType
from .edges import EDGE_TYPES, EdgeType
from .nodes import NODE_TYPES, NodeType
from .schema import (
    ExtractedEdge,
    ExtractedEvent,
    ExtractedNode,
    ExtractionResult,
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
    "EdgeType",
    "EDGE_TYPES",
    "CatalystType",
    "CATALYST_TYPES",
    "ExtractedNode",
    "ExtractedEdge",
    "ExtractedEvent",
    "ExtractionResult",
    "FinMetric",
    "FIN_METRICS",
    "RATIO_METRICS",
    "SIGNAL_TO_CATALYST",
    "canonical_metric",
    "node_iri",
    "edge_iri",
]
