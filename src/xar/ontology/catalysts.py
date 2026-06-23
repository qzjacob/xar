"""Catalyst / event taxonomy (design §5).

The legacy 10 AI-hardware-cycle catalysts are KEPT verbatim (kg_events.dedup_key,
SIGNAL_TO_CATALYST and dashboard label maps depend on them). They are extended
with a sector-agnostic core (M&A, guidance, partnership, management, capital
returns) and an event backbone (regulatory, litigation, index, short-report,
macro) so the same catalyst stream serves the whole economy.
"""
from __future__ import annotations

from enum import Enum


class CatalystType(str, Enum):
    # --- legacy 10 (KEPT) ---
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
    # --- P0: sector-agnostic core ---
    GUIDANCE_CHANGE = "guidance_change"        # raised/cut outlook (any sector)
    MNA = "mna"                                # merger / acquisition / divestiture
    PARTNERSHIP = "partnership"                # strategic partnership / co-sell / JV announce
    CONTRACT_WIN = "contract_win"              # contract / program award / large deal
    PRICING_CHANGE = "pricing_change"          # price action (list price, ASP, tariff)
    MANAGEMENT_CHANGE = "management_change"     # CEO/CFO/key exec transition
    BUYBACK = "buyback"                        # share repurchase authorization
    DIVIDEND = "dividend"                      # initiation / change in dividend
    # --- P1: event backbone ---
    REGULATORY_ACTION = "regulatory_action"    # export control, approval, fine, ruling
    LITIGATION = "litigation"                  # lawsuit / settlement / IP dispute
    INDEX_INCLUSION = "index_inclusion"        # added/removed from an index
    SHORT_REPORT = "short_report"              # activist short report
    MACRO_PRINT = "macro_print"                # rate/CPI/PMI print moving the basket
    STOCK_SPLIT = "stock_split"
    SECONDARY_OFFERING = "secondary_offering"  # equity raise / dilution


CATALYST_TYPES = [t.value for t in CatalystType]

# The original AI-hardware-cycle catalysts (used where the legacy 10 must be
# referenced explicitly, e.g. demand-clock heuristics).
LEGACY_CATALYST_TYPES = [
    "capex_guidance", "order", "qualification", "product_ramp", "accelerator_launch",
    "capacity_expansion", "supply_constraint", "earnings", "equity_investment", "tech_substitution",
]
