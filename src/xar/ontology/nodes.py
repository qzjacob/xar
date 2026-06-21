"""Node types for the industry-chain graph."""
from __future__ import annotations

from enum import Enum


class NodeType(str, Enum):
    MODULE_MAKER = "ModuleMaker"          # optical module makers (incl. contract mfrs)
    UPSTREAM_COMPONENT = "UpstreamComponent"  # laser chips, DSP, optical engines, passives
    DOWNSTREAM_CUSTOMER = "DownstreamCustomer"  # NVIDIA, hyperscalers, OEMs
    TECH_ROUTE = "TechRoute"              # 800G/1.6T, CPO, LPO, SiPh, hollow-core


NODE_TYPES = [t.value for t in NodeType]

# Sub-typing hints carried in attrs (kept open so other verticals reuse the schema)
COMPONENT_SUBTYPES = ["laser_chip", "dsp_pam4", "optical_engine_siph", "passive_optics", "tia_driver"]
