"""Pydantic schemas the LLM extractor must fill — schema-constrained extraction
into the bitemporal KG. These double as the structured-output JSON schema."""
from __future__ import annotations

from pydantic import BaseModel, Field

from .catalysts import CATALYST_TYPES
from .edges import EDGE_TYPES
from .nodes import NODE_TYPES


class ExtractedNode(BaseModel):
    name: str = Field(description="entity name exactly as written in the text")
    node_type: str = Field(description=f"one of {NODE_TYPES}")
    tickers: list[str] = Field(default_factory=list)
    attrs: dict = Field(default_factory=dict, description="e.g. {'subtype':'laser_chip','single_source':true}")


class ExtractedEdge(BaseModel):
    src: str = Field(description="source entity name (must match an ExtractedNode.name)")
    dst: str = Field(description="target entity name")
    rel_type: str = Field(description=f"one of {EDGE_TYPES}")
    valid_from: str | None = Field(default=None, description="ISO date the relation became true, if stated")
    valid_to: str | None = Field(default=None, description="ISO date it stopped, if stated")
    confidence: float = Field(default=0.7, ge=0, le=1)
    evidence: str = Field(description="short verbatim quote supporting the edge")


class ExtractedEvent(BaseModel):
    company: str = Field(description="primary company the event concerns")
    event_type: str = Field(description=f"one of {CATALYST_TYPES}")
    event_date: str | None = Field(default=None, description="ISO date of the event if stated")
    magnitude: str | None = Field(default=None, description="size/amount if stated, e.g. '$663M', '+80% capacity'")
    polarity: str = Field(default="neutral", description="positive | negative | neutral for the named company")
    tech_route_tag: str | None = Field(default=None, description="e.g. '1.6T', 'CPO', 'EML'")
    summary: str = Field(description="one-sentence factual summary")
    confidence: float = Field(default=0.7, ge=0, le=1)
    evidence: str = Field(description="short verbatim quote supporting the event")


class ExtractedMetric(BaseModel):
    company: str = Field(description="company the metric concerns")
    metric: str = Field(description="canonical operating-metric key or a known alias, "
                                    "e.g. 'arr','nrr','rpo','book_to_bill','gmv','nim'")
    value: float = Field(description="numeric value (decimals as fractions: NRR 118% -> 1.18)")
    unit: str | None = Field(default=None, description="USD | ratio | pct | x | count | days ...")
    period: str | None = Field(default=None, description="period if stated, e.g. 'Q3-2025','FY2025'")
    evidence: str = Field(description="short verbatim quote stating the metric and its value")


class ExtractionResult(BaseModel):
    nodes: list[ExtractedNode] = Field(default_factory=list)
    edges: list[ExtractedEdge] = Field(default_factory=list)
    events: list[ExtractedEvent] = Field(default_factory=list)
    metrics: list[ExtractedMetric] = Field(default_factory=list)
