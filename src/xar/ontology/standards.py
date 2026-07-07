"""Ontology standards anchor + canonical financial vocabulary.

DECISION (design §5 — "select an open-source ontology or build from scratch"):
We BUILD a thin, code-as-truth domain ontology for the optical-module supply
chain (NodeType/EdgeType/CatalystType) and ANCHOR it to two established open
standards for interoperability rather than adopting either wholesale:

  * FIBO  (Financial Industry Business Ontology, EDM Council, MIT/CC-BY) —
    canonical IRIs for organizations, equity instruments, and corporate roles.
  * schema.org (Organization / Corporation / Product) — lightweight, ubiquitous
    web-of-data anchors for export/JSON-LD.

Why not adopt FIBO wholesale: FIBO models *financial instruments and contracts*
exhaustively but has no concept of an "optical-module second-source" or a
"co-packaged-optics tech route". Our vertical needs a domain layer FIBO doesn't
provide; FIBO needs a triple-store FIBO-class reasoner we don't want in the
turnkey path. So: domain ontology in code (fast, typed, testable) + stable IRI
mappings here so any node/edge can be exported as FIBO/schema.org-aligned RDF.

The second half of this module is the canonical FINANCIAL METRIC vocabulary —
the real ontology work for *structured* data. Finnhub, FMP, Polygon, Yahoo and
Wind all name the same fact differently ("grossMargin" vs "grossProfitRatio" vs
"gross_margins"). Every provider normalizes onto FinMetric keys so the
`fundamentals`/`estimates` tables speak one language regardless of source.
"""
from __future__ import annotations

from enum import Enum

from . import metric_packs
from .catalysts import CatalystType
from .edges import EdgeType
from .nodes import NodeType

FIBO = "https://spec.edmcouncil.org/fibo"
SCHEMA = "https://schema.org"

# --- Node/edge/catalyst -> canonical external IRIs (for RDF/JSON-LD export) ---
NODE_IRI: dict[str, dict[str, str]] = {
    NodeType.MODULE_MAKER.value: {
        "fibo": f"{FIBO}/BE/LegalEntities/CorporateBodies/Corporation",
        "schema": f"{SCHEMA}/Corporation"},
    NodeType.UPSTREAM_COMPONENT.value: {
        "fibo": f"{FIBO}/BE/LegalEntities/CorporateBodies/Corporation",
        "schema": f"{SCHEMA}/Corporation"},
    NodeType.DOWNSTREAM_CUSTOMER.value: {
        "fibo": f"{FIBO}/BE/LegalEntities/CorporateBodies/Corporation",
        "schema": f"{SCHEMA}/Corporation"},
    NodeType.TECH_ROUTE.value: {
        "fibo": "",  # no FIBO analogue; domain-specific
        "schema": f"{SCHEMA}/Product"},
    # --- generalized core ---
    NodeType.COMPANY.value: {
        "fibo": f"{FIBO}/BE/LegalEntities/CorporateBodies/Corporation",
        "schema": f"{SCHEMA}/Corporation"},
    NodeType.PRODUCT.value: {"fibo": "", "schema": f"{SCHEMA}/Product"},
    NodeType.TECHNOLOGY.value: {"fibo": "", "schema": f"{SCHEMA}/Product"},
    NodeType.END_MARKET.value: {"fibo": "", "schema": f"{SCHEMA}/Audience"},
    NodeType.GEOGRAPHY.value: {"fibo": "", "schema": f"{SCHEMA}/Place"},
    NodeType.PERSON.value: {"fibo": "", "schema": f"{SCHEMA}/Person"},
    NodeType.FACILITY.value: {"fibo": "", "schema": f"{SCHEMA}/Place"},
    NodeType.INSTITUTION.value: {
        "fibo": f"{FIBO}/BE/LegalEntities/CorporateBodies/Corporation",
        "schema": f"{SCHEMA}/Organization"},
    NodeType.REGULATOR.value: {
        "fibo": f"{FIBO}/BE/GovernmentEntities/GovernmentEntities/GovernmentAgency",
        "schema": f"{SCHEMA}/GovernmentOrganization"},
    NodeType.INDEX.value: {
        "fibo": f"{FIBO}/IND/IndicesIndicators/Indices/MarketIndex", "schema": ""},
    NodeType.COMMODITY.value: {"fibo": "", "schema": f"{SCHEMA}/Product"},
    NodeType.STANDARD.value: {"fibo": "", "schema": f"{SCHEMA}/CreativeWork"},
    NodeType.CONTRACT.value: {
        "fibo": f"{FIBO}/FND/Agreements/Contracts/Contract", "schema": ""},
}

EDGE_IRI: dict[str, str] = {
    EdgeType.SUPPLIES.value: f"{SCHEMA}/supplier",
    EdgeType.SECOND_SOURCES.value: f"{SCHEMA}/supplier",
    EdgeType.SINGLE_SOURCE_RISK.value: f"{SCHEMA}/supplier",
    EdgeType.USES_TECHROUTE.value: f"{SCHEMA}/material",
    EdgeType.INVESTS_IN.value: f"{FIBO}/FBC/FunctionalEntities/Investment/Investor",
    EdgeType.COMPETES_WITH.value: f"{SCHEMA}/competitor",
    EdgeType.SUBSTITUTES.value: f"{SCHEMA}/isVariantOf",
    EdgeType.QUALIFIED_BY.value: f"{SCHEMA}/customer",
    # --- generalized chain + landscape ---
    EdgeType.CUSTOMER_OF.value: f"{SCHEMA}/customer",
    EdgeType.SELLS_INTO_ENDMARKET.value: f"{SCHEMA}/audience",
    EdgeType.COMPETES_IN.value: f"{SCHEMA}/competitor",
    EdgeType.DEPENDS_ON_INPUT.value: f"{SCHEMA}/material",
    EdgeType.PARTNERS_WITH.value: f"{SCHEMA}/affiliation",
    # --- corporate structure + event backbone ---
    EdgeType.SUBSIDIARY_OF.value: f"{SCHEMA}/subOrganization",
    EdgeType.ACQUIRES.value: f"{FIBO}/BE/Corporations/Corporations/Acquisition",
    EdgeType.HOLDS_STAKE.value: f"{FIBO}/FBC/FunctionalEntities/Investment/Investor",
    EdgeType.LICENSES.value: f"{SCHEMA}/license",
    EdgeType.OPERATES_FACILITY.value: f"{SCHEMA}/location",
    EdgeType.JV_WITH.value: f"{SCHEMA}/affiliation",
    EdgeType.REGULATED_BY.value: f"{FIBO}/FND/Law/LegalCapacity/isRegulatedBy",
    EdgeType.INDEXED_IN.value: f"{SCHEMA}/isPartOf",
    EdgeType.CAUSALLY_LINKED.value: "",  # domain-specific causal link; no clean schema.org/FIBO analogue
}


def node_iri(node_type: str, scheme: str = "schema") -> str:
    return NODE_IRI.get(node_type, {}).get(scheme, "")


def edge_iri(rel_type: str) -> str:
    return EDGE_IRI.get(rel_type, "")


# ===========================================================================
# Canonical financial-metric vocabulary
# ===========================================================================
class FinMetric(str, Enum):
    # Income statement
    REVENUE = "revenue"
    COST_OF_REVENUE = "cost_of_revenue"
    GROSS_PROFIT = "gross_profit"
    GROSS_MARGIN = "gross_margin"
    OPERATING_INCOME = "operating_income"
    OPERATING_MARGIN = "operating_margin"
    EBITDA = "ebitda"
    NET_INCOME = "net_income"
    NET_MARGIN = "net_margin"
    EPS_DILUTED = "eps_diluted"
    RD_EXPENSE = "rd_expense"
    SGA_EXPENSE = "sga_expense"
    # Balance sheet
    TOTAL_ASSETS = "total_assets"
    TOTAL_LIABILITIES = "total_liabilities"
    TOTAL_EQUITY = "total_equity"
    CASH = "cash_and_equivalents"
    INVENTORY = "inventory"
    TOTAL_DEBT = "total_debt"
    # Cash flow
    OPERATING_CASH_FLOW = "operating_cash_flow"
    CAPEX = "capex"
    FREE_CASH_FLOW = "free_cash_flow"
    # Valuation / returns
    PE = "pe_ratio"
    PS = "ps_ratio"
    PB = "pb_ratio"
    DIVIDEND_YIELD = "dividend_yield"
    ROE = "roe"
    ROIC = "roic"
    CURRENT_RATIO = "current_ratio"
    # Growth + size
    REVENUE_GROWTH = "revenue_growth"
    EARNINGS_GROWTH = "earnings_growth"
    MARKET_CAP = "market_cap"


# FIN_METRICS now spans the whole-economy KPI vocabulary (CORE GAAP + every sector
# pack key from metric_packs), so canonical_metric()'s passthrough accepts KPI keys
# like `arr`/`rpo`/`nim`/`gmv`. FinMetric stays the GAAP-core enum for callers that
# need it (providers, ops console).
FIN_METRICS = list(dict.fromkeys([m.value for m in FinMetric] + metric_packs.ALL_METRIC_KEYS))

# A metric is a "margin/ratio/multiple" (unitless) vs a currency amount — drives units.
# Superset: the legacy explicit GAAP set ∪ every ratio/pct/x pack metric.
RATIO_METRICS = {
    FinMetric.GROSS_MARGIN.value, FinMetric.OPERATING_MARGIN.value,
    FinMetric.NET_MARGIN.value, FinMetric.PE.value, FinMetric.PS.value,
    FinMetric.ROE.value, FinMetric.ROIC.value, FinMetric.CURRENT_RATIO.value,
    FinMetric.EPS_DILUTED.value, FinMetric.REVENUE_GROWTH.value,
    FinMetric.EARNINGS_GROWTH.value,
} | metric_packs.RATIO_LIKE_KEYS

# --- Provider field -> canonical FinMetric ---------------------------------
# FMP statement field names (income/balance/cashflow + ratios) -> canonical.
FMP_MAP: dict[str, str] = {
    "revenue": FinMetric.REVENUE.value,
    "costOfRevenue": FinMetric.COST_OF_REVENUE.value,
    "grossProfit": FinMetric.GROSS_PROFIT.value,
    "grossProfitRatio": FinMetric.GROSS_MARGIN.value,
    "operatingIncome": FinMetric.OPERATING_INCOME.value,
    "operatingIncomeRatio": FinMetric.OPERATING_MARGIN.value,
    "ebitda": FinMetric.EBITDA.value,
    "netIncome": FinMetric.NET_INCOME.value,
    "netIncomeRatio": FinMetric.NET_MARGIN.value,
    "epsdiluted": FinMetric.EPS_DILUTED.value,
    "researchAndDevelopmentExpenses": FinMetric.RD_EXPENSE.value,
    "sellingGeneralAndAdministrativeExpenses": FinMetric.SGA_EXPENSE.value,
    "totalAssets": FinMetric.TOTAL_ASSETS.value,
    "totalLiabilities": FinMetric.TOTAL_LIABILITIES.value,
    "totalStockholdersEquity": FinMetric.TOTAL_EQUITY.value,
    "cashAndCashEquivalents": FinMetric.CASH.value,
    "inventory": FinMetric.INVENTORY.value,
    "totalDebt": FinMetric.TOTAL_DEBT.value,
    "operatingCashFlow": FinMetric.OPERATING_CASH_FLOW.value,
    "capitalExpenditure": FinMetric.CAPEX.value,
    "freeCashFlow": FinMetric.FREE_CASH_FLOW.value,
}

# Finnhub `/stock/metric?metric=all` keys (suffix-stripped) -> canonical.
FINNHUB_METRIC_MAP: dict[str, str] = {
    "grossMargin": FinMetric.GROSS_MARGIN.value,
    "operatingMargin": FinMetric.OPERATING_MARGIN.value,
    "netProfitMargin": FinMetric.NET_MARGIN.value,
    "pe": FinMetric.PE.value,
    "ps": FinMetric.PS.value,
    "roe": FinMetric.ROE.value,
    "roi": FinMetric.ROIC.value,
    "currentRatio": FinMetric.CURRENT_RATIO.value,
}

# Yahoo (yfinance) `.info` keys -> canonical (point-in-time snapshot).
YAHOO_INFO_MAP: dict[str, str] = {
    "totalRevenue": FinMetric.REVENUE.value,
    "grossMargins": FinMetric.GROSS_MARGIN.value,
    "operatingMargins": FinMetric.OPERATING_MARGIN.value,
    "profitMargins": FinMetric.NET_MARGIN.value,
    "ebitda": FinMetric.EBITDA.value,
    "netIncomeToCommon": FinMetric.NET_INCOME.value,
    "trailingEps": FinMetric.EPS_DILUTED.value,
    "trailingPE": FinMetric.PE.value,
    "priceToSalesTrailing12Months": FinMetric.PS.value,
    "returnOnEquity": FinMetric.ROE.value,
    "totalCash": FinMetric.CASH.value,
    "totalDebt": FinMetric.TOTAL_DEBT.value,
    "freeCashflow": FinMetric.FREE_CASH_FLOW.value,
    "operatingCashflow": FinMetric.OPERATING_CASH_FLOW.value,
    "revenueGrowth": FinMetric.REVENUE_GROWTH.value,
    "earningsGrowth": FinMetric.EARNINGS_GROWTH.value,
    "marketCap": FinMetric.MARKET_CAP.value,
}


def canonical_metric(provider: str, field: str) -> str | None:
    """Map a raw provider field name to a canonical FinMetric value (or None)."""
    if provider == "fmp":
        return FMP_MAP.get(field)
    if provider == "finnhub":
        return FINNHUB_METRIC_MAP.get(field)
    if provider == "yahoo":
        return YAHOO_INFO_MAP.get(field)
    return field if field in FIN_METRICS else None


# Catalyst classes a structured signal can map onto (kept inside the 10-type
# taxonomy; the precise signal sub-class is recorded in event attrs/meta).
SIGNAL_TO_CATALYST = {
    "estimate_revision_up": CatalystType.EARNINGS.value,
    "estimate_revision_down": CatalystType.EARNINGS.value,
    "capex_estimate_jump": CatalystType.CAPEX_GUIDANCE.value,
    "insider_cluster_buy": CatalystType.EQUITY_INVESTMENT.value,
    "prediction_market_capex": CatalystType.CAPEX_GUIDANCE.value,
    "prediction_market_launch": CatalystType.ACCELERATOR_LAUNCH.value,
}
