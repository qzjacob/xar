"""Edge (relation) types. All are time-versioned (bitemporal) in storage.

The legacy 8 supply-chain relations are KEPT verbatim (graphrag.py + SEED_EDGES
depend on them). New relations generalize the chain (customer_of, partners_with),
add the industry-landscape basis (sells_into_endmarket, competes_in,
depends_on_input) and the corporate/event backbone (subsidiary_of, acquires,
holds_stake, licenses, operates_facility, regulated_by, indexed_in).
"""
from __future__ import annotations

from enum import Enum


class EdgeType(str, Enum):
    # --- legacy 8 (KEPT) ---
    SUPPLIES = "supplies"                  # src supplies dst
    SECOND_SOURCES = "second_sources"      # src is a 2nd source for dst's input
    SINGLE_SOURCE_RISK = "single_source_risk"
    USES_TECHROUTE = "uses_techroute"      # company -> TechRoute/Technology
    INVESTS_IN = "invests_in"              # equity stake (e.g. NVIDIA -> COHR)
    COMPETES_WITH = "competes_with"
    SUBSTITUTES = "substitutes"            # Technology -> Technology (CPO/LPO vs DSP)
    QUALIFIED_BY = "qualified_by"          # supplier qualified_by customer (catalyst trigger)
    # --- P0: generalized chain + industry landscape ---
    CUSTOMER_OF = "customer_of"            # src is a customer of dst (inverse demand side)
    SELLS_INTO_ENDMARKET = "sells_into_endmarket"  # Company -> EndMarket (TAM/share basis)
    COMPETES_IN = "competes_in"            # Company -> EndMarket/Segment (share pool)
    DEPENDS_ON_INPUT = "depends_on_input"  # Company -> Commodity/Component (bottleneck)
    PARTNERS_WITH = "partners_with"        # GTM / co-sell / ecosystem partnership
    # --- P1: corporate structure + event backbone ---
    SUBSIDIARY_OF = "subsidiary_of"
    ACQUIRES = "acquires"
    HOLDS_STAKE = "holds_stake"            # generalizes invests_in with % in attrs
    LICENSES = "licenses"                  # IP / tech licensing (ARM, EDA, foundry)
    OPERATES_FACILITY = "operates_facility"  # Company -> Facility
    # --- P2 ---
    JV_WITH = "jv_with"
    REGULATED_BY = "regulated_by"
    INDEXED_IN = "indexed_in"              # Company -> Index


EDGE_TYPES = [t.value for t in EdgeType]
