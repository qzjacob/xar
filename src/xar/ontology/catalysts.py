"""The 10-type catalyst / order taxonomy (design §5)."""
from __future__ import annotations

from enum import Enum


class CatalystType(str, Enum):
    CAPEX_GUIDANCE = "capex_guidance"          # hyperscaler / NVIDIA capex (highest-order driver)
    ORDER = "order"                            # large POs, framework deals, capacity reservations
    QUALIFICATION = "qualification"            # design-win / passing customer qual
    PRODUCT_RAMP = "product_ramp"              # 800G->1.6T, 200G/lane, SiPh/CPO/LPO intros
    ACCELERATOR_LAUNCH = "accelerator_launch"  # GB300, Rubin, CX8 (exogenous demand)
    CAPACITY_EXPANSION = "capacity_expansion"  # supplier buildouts
    SUPPLY_CONSTRAINT = "supply_constraint"    # EML undersupply etc. (negative-supply)
    EARNINGS = "earnings"                      # run-rate, guidance, GM mix shift
    EQUITY_INVESTMENT = "equity_investment"    # strategic stakes (supply-securing signal)
    TECH_SUBSTITUTION = "tech_substitution"    # SiPh vs EML, CPO/LPO vs DSP attach


CATALYST_TYPES = [t.value for t in CatalystType]
