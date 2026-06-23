"""Node types for the industry-chain graph.

Generalized for a whole-economy ontology: the original four optical types are
KEPT verbatim (seed data, NODE_TYPE_BY_ROLE, graphrag and dashboards depend on
them) and are now understood as "a Company carrying a chain_role". New extraction
emits the sector-agnostic `Company` with `attrs.chain_role`, plus richer object
types (Product / Technology / EndMarket / Geography / Person / Facility / …).
"""
from __future__ import annotations

from enum import Enum


class NodeType(str, Enum):
    # --- legacy optical (KEPT — now "Company with a chain_role") ---
    MODULE_MAKER = "ModuleMaker"              # optical module makers (incl. contract mfrs)
    UPSTREAM_COMPONENT = "UpstreamComponent"  # laser chips, DSP, optical engines, passives
    DOWNSTREAM_CUSTOMER = "DownstreamCustomer"  # NVIDIA, hyperscalers, OEMs
    TECH_ROUTE = "TechRoute"                  # 800G/1.6T, CPO, LPO, SiPh, hollow-core
    # --- generalized core (P0) ---
    COMPANY = "Company"                       # any issuer/operator; chain_role lives in attrs
    PRODUCT = "Product"                       # product line / SKU family
    TECHNOLOGY = "Technology"                 # generalizes TechRoute across sectors
    END_MARKET = "EndMarket"                  # demand pool / application (TAM/share basis)
    GEOGRAPHY = "Geography"                    # region/country for revenue mix & policy
    # --- corporate / event backbone (P1+) ---
    PERSON = "Person"                         # exec / board (management_change, insiders)
    FACILITY = "Facility"                     # fab / plant / launch site / data center
    INSTITUTION = "Institution"               # investor / sovereign fund / hyperscaler-as-buyer
    REGULATOR = "Regulator"                   # BIS, FCC, SEC, MOFCOM — regulated_by / policy
    INDEX = "Index"                           # SPX/NDX/CSI — index_inclusion, rebalances
    COMMODITY = "Commodity"                   # HBM, wafers, Ti, He, lithium — depends_on_input
    STANDARD = "Standard"                     # OIDA/3GPP/PCIe — interop, not an issuer
    CONTRACT = "Contract"                     # framework deal / launch contract as an object


NODE_TYPES = [t.value for t in NodeType]

# Node types that ARE companies (chain_role in attrs) — graphrag/dashboards treat
# these uniformly as issuers.
COMPANY_NODE_TYPES = {
    NodeType.COMPANY.value, NodeType.MODULE_MAKER.value,
    NodeType.UPSTREAM_COMPONENT.value, NodeType.DOWNSTREAM_CUSTOMER.value,
}

# Sub-typing hints carried in attrs (kept open so other verticals reuse the schema)
COMPONENT_SUBTYPES = ["laser_chip", "dsp_pam4", "optical_engine_siph", "passive_optics", "tia_driver"]


def is_company_node(node_type: str) -> bool:
    return node_type in COMPANY_NODE_TYPES
