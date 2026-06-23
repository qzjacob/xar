"""Sector / industry classification backbone — the prerequisite for a
whole-economy ontology.

Today a company carries only `themes[]` (thematic baskets) and a per-theme chain
`seg`. To let operating-metric packs key off INDUSTRY *independently of the AI
themes* (so the ontology spans the whole market, not just five AI chains), we add
a thin, code-as-truth GICS-style taxonomy: 11 `Sector`s and ~26 `Industry`s.

Licensing posture (consistent with `standards.py`): GICS sector *structure* is a
generic, non-copyrightable idea; we do NOT copy GICS's proprietary codes or
mappings. Each industry is anchored to a **public-domain NAICS** code and a
schema.org type so the classification is exportable and self-documenting.

Resolution order for a company's industry: explicit `industry` field → persisted
`meta.industry` → derived from its primary chain segment (`SEG_INDUSTRY`) →
derived from its first theme (`THEME_INDUSTRY`). The existing 294 AI-theme names
are therefore classified for free; net-new-sector names just carry an explicit
`industry`.
"""
from __future__ import annotations

from enum import Enum


class Sector(str, Enum):
    INFO_TECH = "information_technology"
    COMM_SVCS = "communication_services"
    CONSUMER_DISC = "consumer_discretionary"
    CONSUMER_STAPLES = "consumer_staples_sector"
    HEALTH_CARE = "health_care"
    FINANCIALS = "financials"
    INDUSTRIALS = "industrials"
    ENERGY = "energy"
    MATERIALS = "materials_sector"
    UTILITIES = "utilities_sector"
    REAL_ESTATE = "real_estate"


class Industry(str, Enum):
    # information_technology
    SOFTWARE = "software"
    SEMICONDUCTORS = "semiconductors"
    SEMI_EQUIPMENT = "semi_equipment"
    COMM_EQUIPMENT = "comm_equipment"        # optical modules, networking gear
    IT_HARDWARE = "it_hardware"              # PCB/CCL, devices, components
    # communication_services
    INTERNET_MEDIA = "internet_media"        # ad/engagement internet, social, gaming
    TELECOM = "telecom"
    # consumer_discretionary
    ECOMMERCE = "ecommerce"
    RETAIL = "retail"
    AUTOS = "autos"
    CONSUMER_DURABLES = "consumer_durables"
    # consumer_staples
    STAPLES = "consumer_staples"
    # health_care
    PHARMA = "pharma"
    BIOTECH = "biotech"
    MEDTECH = "medtech"
    HC_SERVICES = "healthcare_services"
    # financials
    BANKS = "banks"
    INSURANCE = "insurance"
    ASSET_MGMT = "asset_management"
    # industrials
    AEROSPACE_DEFENSE = "aerospace_defense"
    CAPITAL_GOODS = "capital_goods"          # machinery, robotics, electricals
    TRANSPORT = "transport"
    # energy
    ENERGY_EP = "energy_ep"                  # oil & gas E&P / integrated
    # materials
    MATERIALS = "materials"
    # utilities
    UTILITIES = "utilities"
    # real estate
    REITS = "reits"


SECTORS = [s.value for s in Sector]
INDUSTRIES = [i.value for i in Industry]

# industry -> parent sector
INDUSTRY_SECTOR: dict[str, str] = {
    Industry.SOFTWARE.value: Sector.INFO_TECH.value,
    Industry.SEMICONDUCTORS.value: Sector.INFO_TECH.value,
    Industry.SEMI_EQUIPMENT.value: Sector.INFO_TECH.value,
    Industry.COMM_EQUIPMENT.value: Sector.INFO_TECH.value,
    Industry.IT_HARDWARE.value: Sector.INFO_TECH.value,
    Industry.INTERNET_MEDIA.value: Sector.COMM_SVCS.value,
    Industry.TELECOM.value: Sector.COMM_SVCS.value,
    Industry.ECOMMERCE.value: Sector.CONSUMER_DISC.value,
    Industry.RETAIL.value: Sector.CONSUMER_DISC.value,
    Industry.AUTOS.value: Sector.CONSUMER_DISC.value,
    Industry.CONSUMER_DURABLES.value: Sector.CONSUMER_DISC.value,
    Industry.STAPLES.value: Sector.CONSUMER_STAPLES.value,
    Industry.PHARMA.value: Sector.HEALTH_CARE.value,
    Industry.BIOTECH.value: Sector.HEALTH_CARE.value,
    Industry.MEDTECH.value: Sector.HEALTH_CARE.value,
    Industry.HC_SERVICES.value: Sector.HEALTH_CARE.value,
    Industry.BANKS.value: Sector.FINANCIALS.value,
    Industry.INSURANCE.value: Sector.FINANCIALS.value,
    Industry.ASSET_MGMT.value: Sector.FINANCIALS.value,
    Industry.AEROSPACE_DEFENSE.value: Sector.INDUSTRIALS.value,
    Industry.CAPITAL_GOODS.value: Sector.INDUSTRIALS.value,
    Industry.TRANSPORT.value: Sector.INDUSTRIALS.value,
    Industry.ENERGY_EP.value: Sector.ENERGY.value,
    Industry.MATERIALS.value: Sector.MATERIALS.value,
    Industry.UTILITIES.value: Sector.UTILITIES.value,
    Industry.REITS.value: Sector.REAL_ESTATE.value,
}

# industry -> public-domain NAICS anchor (US Census, public domain)
INDUSTRY_NAICS: dict[str, str] = {
    Industry.SOFTWARE.value: "5132",          # Software Publishers
    Industry.SEMICONDUCTORS.value: "334413",  # Semiconductor & Related Device Mfg
    Industry.SEMI_EQUIPMENT.value: "333242",  # Semiconductor Machinery Mfg
    Industry.COMM_EQUIPMENT.value: "3342",    # Communications Equipment Mfg
    Industry.IT_HARDWARE.value: "3341",       # Computer & Peripheral Equipment Mfg
    Industry.INTERNET_MEDIA.value: "5191",    # Web Search Portals / Internet Publishing
    Industry.TELECOM.value: "517",            # Telecommunications
    Industry.ECOMMERCE.value: "454110",       # Electronic Shopping & Mail-Order Houses
    Industry.RETAIL.value: "44",              # Retail Trade
    Industry.AUTOS.value: "3361",             # Motor Vehicle Mfg
    Industry.CONSUMER_DURABLES.value: "335",  # Electrical Equipment & Appliances
    Industry.STAPLES.value: "311",            # Food Mfg
    Industry.PHARMA.value: "325412",          # Pharmaceutical Preparation Mfg
    Industry.BIOTECH.value: "325414",         # Biological Product Mfg
    Industry.MEDTECH.value: "339112",         # Surgical & Medical Instrument Mfg
    Industry.HC_SERVICES.value: "62",         # Health Care & Social Assistance
    Industry.BANKS.value: "5221",             # Depository Credit Intermediation
    Industry.INSURANCE.value: "5241",         # Insurance Carriers
    Industry.ASSET_MGMT.value: "5239",        # Other Financial Investment Activities
    Industry.AEROSPACE_DEFENSE.value: "3364",  # Aerospace Product & Parts Mfg
    Industry.CAPITAL_GOODS.value: "333",      # Machinery Mfg
    Industry.TRANSPORT.value: "48",           # Transportation
    Industry.ENERGY_EP.value: "211",          # Oil & Gas Extraction
    Industry.MATERIALS.value: "33",           # Manufacturing (materials)
    Industry.UTILITIES.value: "2211",         # Electric Power Gen/Trans/Dist
    Industry.REITS.value: "525990",           # Other Financial Vehicles (REITs)
}

# Existing chain segment id -> industry (classifies all 294 AI-theme names today).
SEG_INDUSTRY: dict[str, str] = {
    # ai_optical
    "upstream_component": Industry.SEMICONDUCTORS.value,
    "module_maker": Industry.COMM_EQUIPMENT.value,
    "contract_mfg": Industry.COMM_EQUIPMENT.value,
    "downstream_customer": Industry.COMM_EQUIPMENT.value,
    # ai_chip
    "chip_equipment": Industry.SEMI_EQUIPMENT.value,
    "chip_materials": Industry.MATERIALS.value,
    "chip_eda": Industry.SOFTWARE.value,
    "chip_foundry": Industry.SEMICONDUCTORS.value,
    "chip_memory": Industry.SEMICONDUCTORS.value,
    "chip_gpu": Industry.SEMICONDUCTORS.value,
    "chip_cpu": Industry.SEMICONDUCTORS.value,
    "chip_packaging": Industry.SEMICONDUCTORS.value,
    "chip_pcb": Industry.IT_HARDWARE.value,
    # ai_software (all software)
    "swe_devinfra": Industry.SOFTWARE.value, "swe_observability": Industry.SOFTWARE.value,
    "swe_data": Industry.SOFTWARE.value, "swe_security": Industry.SOFTWARE.value,
    "swe_productivity": Industry.SOFTWARE.value, "swe_crm": Industry.SOFTWARE.value,
    "swe_marketing": Industry.SOFTWARE.value, "swe_erp_hr": Industry.SOFTWARE.value,
    "swe_vertical": Industry.SOFTWARE.value,
    # space_exploration (aerospace & defense)
    "spx_launch": Industry.AEROSPACE_DEFENSE.value, "spx_propulsion": Industry.AEROSPACE_DEFENSE.value,
    "spx_satellites": Industry.AEROSPACE_DEFENSE.value, "spx_datacenter": Industry.AEROSPACE_DEFENSE.value,
    "spx_ground": Industry.AEROSPACE_DEFENSE.value, "spx_components": Industry.AEROSPACE_DEFENSE.value,
    "spx_apps": Industry.AEROSPACE_DEFENSE.value, "spx_defense": Industry.AEROSPACE_DEFENSE.value,
    # humanoid_robotics (capital goods, with semi sub-segments)
    "hum_actuation": Industry.CAPITAL_GOODS.value, "hum_motors": Industry.CAPITAL_GOODS.value,
    "hum_sensors": Industry.SEMICONDUCTORS.value, "hum_compute": Industry.SEMICONDUCTORS.value,
    "hum_power": Industry.CAPITAL_GOODS.value, "hum_hands": Industry.CAPITAL_GOODS.value,
    "hum_materials": Industry.MATERIALS.value, "hum_oem": Industry.CAPITAL_GOODS.value,
}

# theme -> default industry (last-resort fallback when no segment resolves)
THEME_INDUSTRY: dict[str, str] = {
    "ai_optical": Industry.COMM_EQUIPMENT.value,
    "ai_chip": Industry.SEMICONDUCTORS.value,
    "ai_software": Industry.SOFTWARE.value,
    "space_exploration": Industry.AEROSPACE_DEFENSE.value,
    "humanoid_robotics": Industry.CAPITAL_GOODS.value,
}


def sector_of_industry(industry: str | None) -> str | None:
    return INDUSTRY_SECTOR.get(industry) if industry else None


def naics_iri(industry: str | None) -> str:
    code = INDUSTRY_NAICS.get(industry or "")
    return f"https://www.census.gov/naics/?input={code}" if code else ""


def _primary_seg(company: dict) -> str | None:
    """The company's chain segment, tolerant of both the registry shape (`seg`
    dict) and the DB-row shape (`meta.segments` dict)."""
    seg = company.get("seg") or (company.get("meta") or {}).get("segments") or {}
    if isinstance(seg, dict) and seg:
        for t in (company.get("themes") or list(seg.keys())):
            if t in seg:
                return seg[t]
        return next(iter(seg.values()))
    return company.get("chain_role")


def industry_of_company(company: dict | None) -> str | None:
    if not company:
        return None
    if company.get("industry"):
        return company["industry"]
    meta = company.get("meta") or {}
    if meta.get("industry"):
        return meta["industry"]
    seg = _primary_seg(company)
    if seg and seg in SEG_INDUSTRY:
        return SEG_INDUSTRY[seg]
    for t in (company.get("themes") or []):
        if t in THEME_INDUSTRY:
            return THEME_INDUSTRY[t]
    return None


def sector_of_company(company: dict | None) -> str | None:
    return sector_of_industry(industry_of_company(company))


def classify(company: dict | None) -> dict:
    """{'sector': ..., 'industry': ...} for persistence into companies.meta."""
    ind = industry_of_company(company)
    return {"industry": ind, "sector": sector_of_industry(ind)}
