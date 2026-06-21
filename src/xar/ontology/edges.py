"""Edge (relation) types. All are time-versioned (bitemporal) in storage."""
from __future__ import annotations

from enum import Enum


class EdgeType(str, Enum):
    SUPPLIES = "supplies"                  # src supplies dst
    SECOND_SOURCES = "second_sources"      # src is a 2nd source for dst's input
    SINGLE_SOURCE_RISK = "single_source_risk"
    USES_TECHROUTE = "uses_techroute"      # company -> TechRoute
    INVESTS_IN = "invests_in"              # equity stake (e.g. NVIDIA -> COHR)
    COMPETES_WITH = "competes_with"
    SUBSTITUTES = "substitutes"            # TechRoute -> TechRoute (CPO/LPO vs DSP, SiPh vs EML)
    QUALIFIED_BY = "qualified_by"          # supplier qualified_by customer (catalyst trigger)


EDGE_TYPES = [t.value for t in EdgeType]
