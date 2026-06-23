"""Operating-metric / sector-KPI packs — the whole-economy heart of the ontology.

The long `fundamentals`/`estimates` tables already make `metric` a free string
key, so a KPI needs NO new table: it is just a canonical metric string like
`revenue` is today. What was missing is (a) the canonical key existing in the
vocabulary, (b) its unit + "higher-is-better" direction, (c) the sector/industry
tag so dashboards pick the right KPIs, and (d) extraction synonyms. This module
supplies all four as a pluggable registry of `MetricSpec`s, organized by INDUSTRY
(via `classifiers`) so adding a new sector is a one-list edit — never a schema or
core change.

A metric shared across industries (book-to-bill, backlog, ASP, churn, ARPU…) is
defined ONCE with several `classifiers`; `PACK_FOR` inverts the list so each
classifier resolves to its full KPI set. `kpis_for_company()` unions a company's
industry + parent sector + theme-derived industry packs on top of CORE.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import sectors

STAR = "*"  # CORE / cross-industry classifier


@dataclass(frozen=True)
class MetricSpec:
    key: str                                  # canonical string written to fundamentals.metric
    label: str
    unit: str                                 # USD|ratio|pct|x|count|days|months|usd_per_kg|mw|boe
    higher_is_better: bool
    classifiers: tuple[str, ...]              # industry/sector values, or STAR for cross-industry
    fibo: str = ""                            # us-gaap / NAICS / SaaS-definition anchor (optional)
    aliases: tuple[str, ...] = field(default_factory=tuple)


def _s(key, label, unit, hib, classifiers, fibo="", aliases=()):
    return MetricSpec(key, label, unit, hib, tuple(classifiers), fibo, tuple(aliases))


# === CORE — generic GAAP financials (mirrors FinMetric; single source of truth) ===
CORE_PACK = [
    _s("revenue", "Revenue", "USD", True, (STAR,), "us-gaap:Revenues"),
    _s("cost_of_revenue", "Cost of Revenue", "USD", False, (STAR,)),
    _s("gross_profit", "Gross Profit", "USD", True, (STAR,)),
    _s("gross_margin", "Gross Margin", "ratio", True, (STAR,)),
    _s("operating_income", "Operating Income", "USD", True, (STAR,)),
    _s("operating_margin", "Operating Margin", "ratio", True, (STAR,)),
    _s("ebitda", "EBITDA", "USD", True, (STAR,)),
    _s("net_income", "Net Income", "USD", True, (STAR,)),
    _s("net_margin", "Net Margin", "ratio", True, (STAR,)),
    _s("eps_diluted", "Diluted EPS", "USD", True, (STAR,)),
    _s("rd_expense", "R&D Expense", "USD", True, (STAR,), "us-gaap:ResearchAndDevelopmentExpense"),
    _s("sga_expense", "SG&A Expense", "USD", False, (STAR,)),
    _s("total_assets", "Total Assets", "USD", True, (STAR,)),
    _s("total_liabilities", "Total Liabilities", "USD", False, (STAR,)),
    _s("total_equity", "Total Equity", "USD", True, (STAR,)),
    _s("cash_and_equivalents", "Cash & Equivalents", "USD", True, (STAR,)),
    _s("inventory", "Inventory", "USD", True, (STAR,)),
    _s("total_debt", "Total Debt", "USD", False, (STAR,)),
    _s("operating_cash_flow", "Operating Cash Flow", "USD", True, (STAR,)),
    _s("capex", "Capex", "USD", False, (STAR,), "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"),
    _s("free_cash_flow", "Free Cash Flow", "USD", True, (STAR,)),
    _s("pe_ratio", "P/E", "x", False, (STAR,)),
    _s("ps_ratio", "P/S", "x", False, (STAR,)),
    _s("roe", "ROE", "ratio", True, (STAR,)),
    _s("roic", "ROIC", "ratio", True, (STAR,)),
    _s("current_ratio", "Current Ratio", "x", True, (STAR,)),
    _s("revenue_growth", "Revenue Growth", "ratio", True, (STAR,)),
    _s("earnings_growth", "Earnings Growth", "ratio", True, (STAR,)),
    _s("market_cap", "Market Cap", "USD", True, (STAR,)),
]

# === LANDSCAPE — industry structure / 行业格局 (used by P3 view; keys defined now) ===
LANDSCAPE_PACK = [
    _s("tam", "Total Addressable Market", "USD", True, (STAR,)),
    _s("sam", "Serviceable Addressable Market", "USD", True, (STAR,)),
    _s("market_share", "Market Share", "ratio", True, (STAR,), aliases=("market share", "share of market")),
    _s("hhi", "HHI (concentration)", "count", False, (STAR,)),
    _s("rev_mix_pct", "Revenue Mix %", "ratio", True, (STAR,)),
    _s("market_growth", "Market Growth", "ratio", True, (STAR,)),
]

# === SOFTWARE / SaaS (user-named: ARR / NRR / RPO …) ===
SOFTWARE_PACK = [
    _s("arr", "Annual Recurring Revenue", "USD", True, ("software",),
       aliases=("ARR", "annual recurring revenue", "annualized recurring revenue")),
    _s("net_new_arr", "Net New ARR", "USD", True, ("software",), aliases=("net new ARR",)),
    _s("nrr", "Net Revenue Retention", "ratio", True, ("software",),
       aliases=("NRR", "net revenue retention", "net dollar retention", "NDR", "dollar-based net retention", "DBNRR")),
    _s("grr", "Gross Revenue Retention", "ratio", True, ("software",),
       aliases=("GRR", "gross revenue retention", "gross retention")),
    _s("logo_retention", "Logo Retention", "ratio", True, ("software",), aliases=("logo retention",)),
    _s("rpo", "Remaining Performance Obligations", "USD", True, ("software",),
       fibo="us-gaap:RevenueRemainingPerformanceObligation",
       aliases=("RPO", "remaining performance obligations", "total RPO")),
    _s("crpo", "Current RPO", "USD", True, ("software",), aliases=("cRPO", "current RPO", "current remaining performance obligations")),
    _s("billings", "Calculated Billings", "USD", True, ("software",), aliases=("billings", "calculated billings")),
    _s("rule_of_40", "Rule of 40", "pct", True, ("software",), aliases=("rule of 40",)),
    _s("magic_number", "Sales Magic Number", "x", True, ("software",), aliases=("magic number",)),
    _s("cac_payback", "CAC Payback (months)", "months", False, ("software",), aliases=("CAC payback",)),
    _s("sbc_pct", "SBC % of Revenue", "ratio", False, ("software",), aliases=("SBC % of revenue", "stock-based comp %")),
    _s("fcf_margin", "FCF Margin", "ratio", True, ("software", "internet_media")),
    _s("subscription_gross_margin", "Subscription Gross Margin", "ratio", True, ("software",)),
    _s("customers_count", "Customers", "count", True, ("software",), aliases=("total customers",)),
    _s("large_customers", "Customers >$100k ARR", "count", True, ("software",), aliases=(">$100k", ">$1M ARR")),
]

# === SEMICONDUCTORS / equipment / comm-equipment (formalizes ai_chip / ai_optical) ===
SEMI_PACK = [
    _s("book_to_bill", "Book-to-Bill", "x", True,
       ("semiconductors", "semi_equipment", "comm_equipment", "aerospace_defense", "capital_goods"),
       aliases=("book-to-bill", "book to bill")),
    _s("backlog", "Backlog", "USD", True,
       ("semiconductors", "semi_equipment", "comm_equipment", "aerospace_defense", "capital_goods",
        "consumer_durables", "autos")),
    _s("asp", "Average Selling Price", "USD", True,
       ("semiconductors", "comm_equipment", "it_hardware", "autos", "materials"),
       aliases=("ASP", "average selling price")),
    _s("wafer_starts", "Wafer Starts", "count", True, ("semiconductors", "semi_equipment")),
    _s("utilization", "Fab Utilization", "ratio", True, ("semiconductors",), aliases=("utilization rate",)),
    _s("bit_growth", "Bit Growth", "ratio", True, ("semiconductors",), aliases=("bit shipment growth",)),
    _s("doi_days", "Days of Inventory", "days", False, ("semiconductors", "it_hardware"), aliases=("DOI", "days of inventory")),
    _s("hbm_capacity_gb", "HBM Capacity (GB)", "count", True, ("semiconductors",), aliases=("HBM capacity",)),
    _s("cowos_capacity", "CoWoS Capacity", "count", True, ("semiconductors",), aliases=("CoWoS capacity", "advanced packaging capacity")),
    _s("design_wins", "Design Wins", "count", True, ("semiconductors", "comm_equipment"), aliases=("design win",)),
    _s("systems_shipped", "Systems Shipped", "count", True, ("semi_equipment",)),
    _s("service_attach", "Service Attach", "ratio", True, ("semi_equipment", "capital_goods")),
    # comm-equipment / optical specifics
    _s("module_asp", "Module ASP", "USD", True, ("comm_equipment",), aliases=("module ASP", "per-port ASP")),
    _s("attach_rate", "Attach Rate", "ratio", True, ("comm_equipment", "software")),
    _s("qual_wins", "Qualification Wins", "count", True, ("comm_equipment",), aliases=("customer qualification",)),
    _s("lane_speed_gbps", "Lane Speed (Gbps)", "count", True, ("comm_equipment",), aliases=("per-lane speed", "200G/lane")),
    _s("ports_shipped", "Ports Shipped", "count", True, ("comm_equipment",)),
    _s("units_shipped", "Units Shipped", "count", True,
       ("it_hardware", "consumer_durables", "comm_equipment", "autos")),
    _s("inventory_turns", "Inventory Turns", "x", True, ("it_hardware", "retail", "materials", "consumer_durables")),
    _s("capacity_utilization", "Capacity Utilization", "ratio", True,
       ("it_hardware", "materials", "capital_goods", "energy_ep")),
]

# === COMMUNICATION SERVICES — internet/media + telecom ===
INTERNET_PACK = [
    _s("mau", "Monthly Active Users", "count", True, ("internet_media",), aliases=("MAU", "monthly active users")),
    _s("dau", "Daily Active Users", "count", True, ("internet_media",), aliases=("DAU", "daily active users")),
    _s("arpu", "ARPU", "USD", True, ("internet_media", "telecom"), aliases=("ARPU", "average revenue per user")),
    _s("ad_load", "Ad Load", "ratio", True, ("internet_media",), aliases=("ad load",)),
    _s("engagement_minutes", "Engagement (minutes)", "count", True, ("internet_media",), aliases=("time spent",)),
    _s("paying_users", "Paying Users", "count", True, ("internet_media", "ecommerce")),
    _s("churn", "Churn", "ratio", False, ("internet_media", "telecom", "software"), aliases=("churn rate",)),
    _s("take_rate", "Take Rate", "ratio", True, ("internet_media", "ecommerce"), aliases=("take rate",)),
    _s("subscribers", "Subscribers", "count", True, ("telecom", "internet_media")),
    _s("subscriber_net_adds", "Subscriber Net Adds", "count", True, ("internet_media",),
       aliases=("subscriber net adds", "paid net additions", "membership net adds")),
    _s("content_spend", "Content Spend", "USD", False, ("internet_media",),
       aliases=("content spend", "cash content spend", "content amortization")),
    _s("gross_bookings", "Gross Bookings", "USD", True, ("internet_media", "ecommerce"),
       aliases=("gross bookings", "total bookings")),
    _s("postpaid_net_adds", "Postpaid Net Adds", "count", True, ("telecom",), aliases=("net adds",)),
    _s("capex_intensity", "Capex Intensity", "ratio", False, ("telecom", "utilities")),
]

# === RESTAURANTS / foodservice (cycle theme: QSR / casual / fast-casual) ===
RESTAURANTS_PACK = [
    _s("unit_count", "Unit Count", "count", True, ("restaurants",),
       aliases=("restaurant count", "system units", "system-wide units")),
    _s("net_new_units", "Net New Units", "count", True, ("restaurants",),
       aliases=("net new restaurants", "unit growth", "net unit growth")),
    _s("average_unit_volume", "Average Unit Volume", "USD", True, ("restaurants",),
       aliases=("AUV", "average unit volume", "average weekly sales")),
    _s("traffic", "Traffic / Transactions", "ratio", True, ("restaurants",),
       aliases=("traffic", "guest counts", "comparable traffic", "transaction growth")),
    _s("check_size", "Average Check", "USD", True, ("restaurants",),
       aliases=("average check", "ticket", "check size")),
    _s("restaurant_margin", "Restaurant-Level Margin", "ratio", True, ("restaurants",),
       aliases=("restaurant-level margin", "restaurant level operating margin", "store-level margin")),
    _s("digital_mix", "Digital Sales Mix", "ratio", True, ("restaurants",),
       aliases=("digital mix", "digital sales mix", "off-premise mix")),
    _s("franchise_mix", "Franchise Mix", "ratio", True, ("restaurants",),
       aliases=("franchise mix", "franchised percentage", "refranchising")),
]

# === CONSUMER — ecommerce / retail / staples / autos ===
CONSUMER_PACK = [
    _s("gmv", "Gross Merchandise Value", "USD", True, ("ecommerce",), aliases=("GMV", "gross merchandise value")),
    _s("orders", "Orders", "count", True, ("ecommerce",)),
    _s("active_buyers", "Active Buyers", "count", True, ("ecommerce",), aliases=("active customers",)),
    _s("units_sold", "Units Sold", "count", True, ("ecommerce", "retail", "consumer_durables")),
    _s("fulfillment_cost", "Fulfillment Cost", "USD", False, ("ecommerce",)),
    _s("same_store_sales", "Same-Store Sales", "ratio", True, ("retail", "restaurants"),
       aliases=("SSS", "comparable sales", "comps", "comp sales", "same-restaurant sales")),
    _s("store_count", "Store Count", "count", True, ("retail",)),
    _s("sales_per_sqft", "Sales per Sq Ft", "USD", True, ("retail",)),
    _s("organic_growth", "Organic Growth", "ratio", True,
       ("consumer_staples", "capital_goods", "asset_management", "consumer_durables", "materials")),
    _s("volume_growth", "Volume Growth", "ratio", True, ("consumer_staples", "materials")),
    _s("price_mix", "Price/Mix", "ratio", True, ("consumer_staples",)),
    _s("units_delivered", "Units Delivered", "count", True, ("autos",), aliases=("deliveries", "vehicles delivered")),
    _s("production_volume", "Production Volume", "count", True, ("autos", "capital_goods")),
]

# === HEALTH CARE — pharma / biotech / medtech / services ===
HEALTHCARE_PACK = [
    _s("peak_sales_est", "Peak Sales Estimate", "USD", True, ("pharma", "biotech")),
    _s("script_volume", "Prescription Volume", "count", True, ("pharma",), aliases=("TRx", "scripts")),
    _s("gtn_discount", "Gross-to-Net Discount", "ratio", False, ("pharma",), aliases=("GTN", "gross to net")),
    _s("patent_cliff_year", "Patent Cliff Year", "count", False, ("pharma",), aliases=("LOE", "loss of exclusivity")),
    _s("rd_pipeline_count", "Pipeline Programs", "count", True, ("pharma", "biotech")),
    _s("royalty_rate", "Royalty Rate", "ratio", True, ("pharma", "biotech")),
    _s("pipeline_phase", "Lead Program Phase", "count", True, ("biotech",), aliases=("Phase 1", "Phase 2", "Phase 3")),
    _s("pdufa_date_count", "Upcoming PDUFA", "count", True, ("biotech", "pharma"), aliases=("PDUFA",)),
    _s("cash_runway_qtrs", "Cash Runway (qtrs)", "count", True, ("biotech",), aliases=("cash runway",)),
    _s("procedure_volume", "Procedure Volume", "count", True, ("medtech",)),
    _s("installed_base", "Installed Base", "count", True, ("medtech", "capital_goods")),
    _s("recurring_rev_pct", "Recurring Revenue %", "ratio", True, ("medtech",)),
    _s("medical_loss_ratio", "Medical Loss Ratio", "ratio", False, ("healthcare_services", "insurance"), aliases=("MLR",)),
    _s("membership", "Membership", "count", True, ("healthcare_services",)),
]

# === FINANCIALS — banks / insurance / asset management ===
FINANCIALS_PACK = [
    _s("nim", "Net Interest Margin", "ratio", True, ("banks",), aliases=("NIM", "net interest margin")),
    _s("nii", "Net Interest Income", "USD", True, ("banks",), aliases=("NII",)),
    _s("npl_ratio", "NPL Ratio", "ratio", False, ("banks",), aliases=("non-performing loans", "NPL")),
    _s("coverage_ratio", "Coverage Ratio", "ratio", True, ("banks",), aliases=("provision coverage",)),
    _s("rotce", "ROTCE", "ratio", True, ("banks",), aliases=("return on tangible common equity",)),
    _s("cet1", "CET1 Ratio", "ratio", True, ("banks",), aliases=("CET1", "common equity tier 1")),
    _s("loan_growth", "Loan Growth", "ratio", True, ("banks",)),
    _s("deposit_beta", "Deposit Beta", "ratio", False, ("banks",), aliases=("deposit beta",)),
    _s("efficiency_ratio", "Efficiency Ratio", "ratio", False, ("banks",), aliases=("efficiency ratio",)),
    _s("net_charge_off_rate", "Net Charge-Off Rate", "ratio", False, ("banks",), aliases=("NCO", "charge-offs")),
    _s("book_value_ps", "Book Value / Share", "USD", True, ("banks", "insurance")),
    _s("tangible_book_ps", "Tangible Book / Share", "USD", True, ("banks",), aliases=("TBVPS",)),
    _s("combined_ratio", "Combined Ratio", "ratio", False, ("insurance",), aliases=("combined ratio",)),
    _s("loss_ratio", "Loss Ratio", "ratio", False, ("insurance",)),
    _s("expense_ratio", "Expense Ratio", "ratio", False, ("insurance",)),
    _s("net_premiums_written", "Net Premiums Written", "USD", True, ("insurance",), aliases=("NPW",)),
    _s("reserve_development", "Reserve Development", "USD", True, ("insurance",)),
    _s("aum", "Assets Under Management", "USD", True, ("asset_management",), aliases=("AUM",)),
    _s("net_flows", "Net Flows", "USD", True, ("asset_management",), aliases=("net inflows",)),
    _s("fee_rate", "Fee Rate", "ratio", True, ("asset_management",)),
    _s("performance_fees", "Performance Fees", "USD", True, ("asset_management",)),
]

# === INDUSTRIALS — aerospace/defense + capital goods (formalizes space / robotics) ===
INDUSTRIALS_PACK = [
    _s("launch_cadence", "Launch Cadence", "count", True, ("aerospace_defense",), aliases=("launches per year", "launch rate")),
    _s("usd_per_kg", "Cost to Orbit ($/kg)", "usd_per_kg", False, ("aerospace_defense",), aliases=("$/kg to orbit", "cost per kg")),
    _s("reuse_count", "Booster Reuse Count", "count", True, ("aerospace_defense",), aliases=("reflights",)),
    _s("payload_mass_kg", "Payload Mass (kg)", "count", True, ("aerospace_defense",)),
    _s("constellation_sats", "Constellation Satellites", "count", True, ("aerospace_defense",), aliases=("satellites in orbit",)),
    _s("program_awards", "Program Awards", "USD", True, ("aerospace_defense",), aliases=("contract awards",)),
    _s("unit_volume", "Unit Volume", "count", True, ("capital_goods",), aliases=("units produced", "volume ramp")),
    _s("bom_cost", "BOM Cost", "USD", False, ("capital_goods",), aliases=("bill of materials", "BOM")),
    _s("actuator_content_usd", "Actuator Content ($)", "USD", True, ("capital_goods",), aliases=("content per unit",)),
    _s("pilot_deployments", "Pilot Deployments", "count", True, ("capital_goods",), aliases=("pilots", "deployments")),
    _s("gross_margin_unit", "Per-Unit Gross Margin", "ratio", True, ("capital_goods",)),
    _s("load_factor", "Load Factor", "ratio", True, ("transport",)),
    _s("rpm", "Revenue Passenger-Miles", "count", True, ("transport",), aliases=("RPM",)),
    _s("on_time_rate", "On-Time Rate", "ratio", True, ("transport",)),
]

# === ENERGY / UTILITIES / MATERIALS / REITS (user priority: energy/power/utilities) ===
ENERGY_PACK = [
    _s("production_boe", "Production (BOE/d)", "boe", True, ("energy_ep",), aliases=("production", "BOE/d", "barrels of oil equivalent")),
    _s("reserves", "Reserves", "boe", True, ("energy_ep", "materials"), aliases=("proved reserves",)),
    _s("realized_price", "Realized Price", "USD", True, ("energy_ep", "utilities")),
    _s("lifting_cost", "Lifting Cost", "USD", False, ("energy_ep",), aliases=("cash cost per barrel",)),
    _s("netback", "Netback", "USD", True, ("energy_ep",)),
    _s("fcf_breakeven_price", "FCF Breakeven Price", "USD", False, ("energy_ep",), aliases=("breakeven oil price",)),
    _s("reserve_replacement", "Reserve Replacement", "ratio", True, ("energy_ep",), aliases=("RRR",)),
    _s("decline_rate", "Decline Rate", "ratio", False, ("energy_ep",)),
    _s("capacity_mw", "Capacity (MW)", "mw", True, ("utilities",), aliases=("installed capacity", "MW")),
    _s("capacity_factor", "Capacity Factor", "ratio", True, ("utilities",), aliases=("capacity factor",)),
    _s("load_growth", "Load Growth", "ratio", True, ("utilities",), aliases=("demand growth",)),
    _s("rate_base", "Rate Base", "USD", True, ("utilities",), aliases=("rate base",)),
    _s("ppa_price", "PPA Price", "USD", True, ("utilities",), aliases=("power purchase agreement",)),
    _s("allowed_roe", "Allowed ROE", "ratio", True, ("utilities",)),
    _s("capex_plan", "Capex Plan", "USD", True, ("utilities",)),
    _s("cash_cost", "Cash Cost", "USD", False, ("materials",)),
]

REITS_PACK = [
    _s("ffo_ps", "FFO / Share", "USD", True, ("reits",), aliases=("FFO", "funds from operations")),
    _s("affo_ps", "AFFO / Share", "USD", True, ("reits",), aliases=("AFFO",)),
    _s("noi", "Net Operating Income", "USD", True, ("reits",), aliases=("NOI",)),
    _s("same_store_noi", "Same-Store NOI Growth", "ratio", True, ("reits",), aliases=("SS NOI",)),
    _s("occupancy", "Occupancy", "ratio", True, ("reits",)),
    _s("cap_rate", "Cap Rate", "ratio", False, ("reits",), aliases=("capitalization rate",)),
    _s("nav_ps", "NAV / Share", "USD", True, ("reits",), aliases=("net asset value",)),
    _s("leverage_ratio", "Net Debt / EBITDA", "x", False, ("reits", "utilities")),
]

ALL_SPECS: list[MetricSpec] = (
    CORE_PACK + LANDSCAPE_PACK + SOFTWARE_PACK + SEMI_PACK + INTERNET_PACK
    + RESTAURANTS_PACK + CONSUMER_PACK + HEALTHCARE_PACK + FINANCIALS_PACK
    + INDUSTRIALS_PACK + ENERGY_PACK + REITS_PACK
)

# --- derived indexes (computed once at import) ------------------------------
SPEC_BY_KEY: dict[str, MetricSpec] = {s.key: s for s in ALL_SPECS}
ALL_METRIC_KEYS: list[str] = [s.key for s in ALL_SPECS]
RATIO_LIKE_KEYS: set[str] = {s.key for s in ALL_SPECS if s.unit in ("ratio", "pct", "x")}

# classifier -> ordered metric keys (CORE first, then the classifier's own)
PACK_FOR: dict[str, list[str]] = {}
for _spec in ALL_SPECS:
    for _c in _spec.classifiers:
        PACK_FOR.setdefault(_c, []).append(_spec.key)

# extraction alias -> canonical key (for the LLM + free-text grounding)
ALIAS_TO_KEY: dict[str, str] = {}
for _spec in ALL_SPECS:
    for _a in _spec.aliases:
        ALIAS_TO_KEY[_a.lower()] = _spec.key


def spec(metric: str) -> MetricSpec | None:
    return SPEC_BY_KEY.get(metric)


def is_higher_better(metric: str) -> bool:
    s = SPEC_BY_KEY.get(metric)
    return True if s is None else s.higher_is_better


def is_ratio(metric: str) -> bool:
    return metric in RATIO_LIKE_KEYS


def canonical_kpi(name: str) -> str | None:
    """Resolve a metric key or a known alias to its canonical key (else None)."""
    k = (name or "").strip()
    if k in SPEC_BY_KEY:
        return k
    return ALIAS_TO_KEY.get(k.lower())


def kpis_for_industry(industry: str | None) -> list[MetricSpec]:
    """Industry KPIs only (no CORE)."""
    return [SPEC_BY_KEY[k] for k in PACK_FOR.get(industry or "", [])]


def _classifiers_for_company(company: dict | None) -> list[str]:
    out: list[str] = []
    ind = sectors.industry_of_company(company)
    if ind:
        out.append(ind)
        sec = sectors.sector_of_industry(ind)
        if sec and sec in PACK_FOR:
            out.append(sec)
    for t in ((company or {}).get("themes") or []):
        ti = sectors.THEME_INDUSTRY.get(t)
        if ti and ti not in out:
            out.append(ti)
    return out


def kpis_for_company(company: dict | None, *, include_core: bool = False) -> list[MetricSpec]:
    """The sector-appropriate KPI specs for a company: its industry ∪ parent
    sector ∪ theme-derived industry packs (CORE optional). De-duplicated, order
    stable."""
    seen: set[str] = set()
    out: list[MetricSpec] = []
    classifiers = ([STAR] if include_core else []) + _classifiers_for_company(company)
    for c in classifiers:
        for k in PACK_FOR.get(c, []):
            if k not in seen:
                seen.add(k)
                out.append(SPEC_BY_KEY[k])
    return out


def kpi_labels_for_company(company: dict | None) -> list[str]:
    """`key (Label)` hints for the extraction prompt (industry KPIs only)."""
    return [f"{s.key} ({s.label})" for s in kpis_for_company(company)]
