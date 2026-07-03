"""US SEC XBRL fundamentals via edgartools company-facts. GREEN: US-government
public domain. For each US-listed name (ticker resolvable on EDGAR) pull the
last N quarters of core statement lines onto the canonical `fundamentals`
vocabulary with the *real* period_end. Q4 — which US filers only report inside
the 10-K full-year total — is derived as FY minus the three reported quarters.
Non-US names (no US ticker) are skipped fast; per-company failures are
non-fatal. Import-light: edgartools is loaded lazily inside functions."""
from __future__ import annotations

from datetime import date

from ..config import get_settings
from ..logging import get_logger
from ..ontology.standards import FinMetric
from .registry import COMPANIES, company_by_id

log = get_logger("xar.ingest.xbrl")

SOURCE = "edgar_xbrl"

# canonical metric -> XBRL concept candidates, priority-ordered. The first
# concept reporting a given period wins, later ones only fill missing periods,
# so mixed-tag histories (companies switch tags across years) still line up.
# IFRS fallbacks cover 20-F ADR filers (e.g. TSM) on a best-effort basis.
CONCEPTS: dict[str, tuple[str, ...]] = {
    FinMetric.REVENUE.value: (
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:Revenues", "us-gaap:SalesRevenueNet",
        "ifrs-full:Revenue"),
    FinMetric.NET_INCOME.value: ("us-gaap:NetIncomeLoss", "ifrs-full:ProfitLoss"),
    FinMetric.OPERATING_INCOME.value: (
        "us-gaap:OperatingIncomeLoss", "ifrs-full:ProfitLossFromOperatingActivities"),
    FinMetric.GROSS_PROFIT.value: ("us-gaap:GrossProfit", "ifrs-full:GrossProfit"),
    FinMetric.CAPEX.value: (
        "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
        "us-gaap:PaymentsToAcquireProductiveAssets",
        "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"),
    FinMetric.OPERATING_CASH_FLOW.value: (
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
        "us-gaap:NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        "ifrs-full:CashFlowsFromUsedInOperatingActivities"),
    FinMetric.RD_EXPENSE.value: ("us-gaap:ResearchAndDevelopmentExpense",),
    FinMetric.INVENTORY.value: ("us-gaap:InventoryNet", "ifrs-full:Inventories"),
}
# balance-sheet stocks: instant facts (a value at a date), not flows over a period
INSTANT_METRICS = {FinMetric.INVENTORY.value}


def _d(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _us_ticker(company: dict) -> str | None:
    """US-listed ticker (exchange-suffixed tickers like 300308.SZ are non-US)."""
    return next((t for t in company.get("tickers", []) if "." not in t), None)


def _label(f, pe: date) -> str:
    """Fiscal period label matching the FMP style ('Q3-2025'); FY-end == fiscal Q4."""
    fp = str(getattr(f, "fiscal_period", "") or "")
    fy = getattr(f, "fiscal_year", None) or pe.year
    if fp == "FY":
        return f"Q4-{fy}"
    if fp.startswith("Q"):
        return f"{fp}-{fy}"
    return f"Q{(pe.month - 1) // 3 + 1}-{pe.year}"


def _filed(f) -> date:
    return _d(getattr(f, "filing_date", None)) or date.min


def _merge(fact_lists, keep, key) -> dict:
    """Priority-merge facts into {key: (value_fact, label_fact)}.

    Within one concept the *value* comes from the latest filing (restatements
    supersede) while the *label* context (fiscal_period/fiscal_year) comes from
    the earliest filing — later filings re-report old periods as comparatives
    under their own (wrong for that period) fiscal year. Across concepts the
    earlier candidate wins; later ones only fill missing keys."""
    out: dict = {}
    for facts in fact_lists:
        best: dict = {}
        for f in facts:
            if getattr(f, "numeric_value", None) is None or not keep(f):
                continue
            k = key(f)
            if k is None:
                continue
            vf, lf = best.get(k, (f, f))
            best[k] = (f if _filed(f) >= _filed(vf) else vf,
                       f if _filed(f) < _filed(lf) else lf)
        for k, pair in best.items():
            out.setdefault(k, pair)
    return out


def _row(vf, lf, pe: date, value=None, meta=None) -> dict:
    return {"value": float(value if value is not None else vf.numeric_value),
            "period": _label(lf, pe), "period_end": pe,
            "unit": str(getattr(vf, "unit", "") or "USD"),
            "meta": {"concept": str(getattr(vf, "concept", "")), **(meta or {})}}


def _duration_rows(fact_lists) -> dict[date, dict]:
    """Discrete quarterly rows from duration facts.

    Directly-reported ~3-month facts are kept as-is. Cash-flow statements in
    10-Qs are YTD-only, so successive differences within one fiscal-year start
    derive the missing discrete quarters (Q2=H1-Q1, Q3=9M-H1, Q4=FY-9M); when
    no YTD chain exists, Q4 falls back to FY minus three reported quarters."""
    def _key(f):
        ps, pe = _d(getattr(f, "period_start", None)), _d(getattr(f, "period_end", None))
        return (ps, pe) if ps and pe and pe > ps else None

    spans = _merge(fact_lists, lambda f: getattr(f, "period_type", "") == "duration", _key)
    rows: dict[date, dict] = {}
    starts: dict[date, date] = {}  # period_end -> period_start of the discrete quarter
    for (ps, pe), (vf, lf) in spans.items():
        if 70 <= (pe - ps).days <= 100:
            rows[pe] = _row(vf, lf, pe)
            starts[pe] = ps
    # YTD chain: successive same-start spans one quarter apart -> discrete quarter
    by_start: dict[date, list] = {}
    for (ps, pe), pair in spans.items():
        by_start.setdefault(ps, []).append((pe, pair))
    for ps, items in by_start.items():
        items.sort(key=lambda kv: kv[0])
        for (pe_a, (vf_a, _)), (pe_b, (vf_b, lf_b)) in zip(items, items[1:]):
            if pe_b in rows or not 70 <= (pe_b - pe_a).days <= 100:
                continue
            rows[pe_b] = _row(vf_b, lf_b, pe_b, value=vf_b.numeric_value - vf_a.numeric_value,
                              meta={"derived": "ytd_diff"})
            starts[pe_b] = pe_a
    # fallback: fiscal year reported as FY total + three discrete quarters only
    for (ps, pe), (vf, lf) in spans.items():
        if not 340 <= (pe - ps).days <= 390 or pe in rows:
            continue
        inner = [rows[e] for e, s in starts.items() if ps <= s and e < pe]
        if len(inner) != 3:
            continue
        val = vf.numeric_value - sum(r["value"] for r in inner)
        fy = getattr(lf, "fiscal_year", None) or pe.year
        rows[pe] = _row(vf, lf, pe, value=val, meta={"derived": "fy_minus_3q"})
        rows[pe]["period"] = f"Q4-{fy}"
    return rows


def pick_quarters(fact_lists, *, instant: bool, quarters: int = 8) -> list[dict]:
    """Reduce per-concept fact lists to the last-N discrete quarterly rows.
    Instant metrics (balance-sheet stocks) keep the balance at each report date;
    flow metrics go through direct + YTD-difference + FY-fallback derivation."""
    if instant:
        merged = _merge(fact_lists, lambda f: getattr(f, "period_type", "") == "instant",
                        lambda f: _d(getattr(f, "period_end", None)))
        rows = {pe: _row(vf, lf, pe) for pe, (vf, lf) in merged.items()}
    else:
        rows = _duration_rows(fact_lists)
    ordered = sorted(rows.items(), key=lambda kv: kv[0], reverse=True)[:quarters]
    return [r for _, r in ordered]


def pull_company(company_id: str, quarters: int = 8) -> int:
    """Pull core XBRL facts for one US company -> fundamentals rows. Returns rows
    written; 0 (fast, no network) for non-US names, 0 (logged) on any failure."""
    company = company_by_id(company_id)
    if not company:
        return 0
    ticker = _us_ticker(company)
    if not ticker:
        return 0  # non-US filer: no EDGAR company facts

    import edgar

    edgar.set_identity(get_settings().edgar_identity)
    try:
        facts = edgar.Company(ticker).get_facts()
    except Exception as e:  # noqa: BLE001 — per-company failures are non-fatal
        log.warning("xbrl facts lookup failed for %s: %s", ticker, e)
        return 0
    if facts is None:
        return 0

    from ..storage import structured

    n = 0
    for metric, concepts in CONCEPTS.items():
        fact_lists = []
        for c in concepts:
            try:
                fact_lists.append(facts.query().by_concept(c).execute())
            except Exception:  # noqa: BLE001 — unknown concept for this filer
                continue
        for r in pick_quarters(fact_lists, instant=metric in INSTANT_METRICS, quarters=quarters):
            structured.upsert_fundamental(
                company_id, metric, r["value"], period=r["period"],
                period_end=r["period_end"], freq="quarter", unit=r["unit"] or "USD",
                source=SOURCE, meta=r["meta"])
            n += 1
    log.info("xbrl: %s (%s) -> %d fundamentals rows", company_id, ticker, n)
    return n


def pull_universe(company_ids: list[str] | None = None, quarters: int = 8,
                  limit: int | None = None) -> dict:
    """Backfill XBRL fundamentals for every US-listed name in the watched
    universe (or an explicit id list). Per-company failures are non-fatal."""
    if company_ids is None:
        company_ids = [c["id"] for c in COMPANIES if _us_ticker(c)]
    if limit:
        company_ids = company_ids[:limit]
    done, rows = 0, 0
    for cid in company_ids:
        try:
            rows += pull_company(cid, quarters=quarters)
            done += 1
        except Exception as e:  # noqa: BLE001
            log.warning("xbrl pull failed for %s: %s", cid, e)
    log.info("xbrl universe: %d/%d companies, %d rows", done, len(company_ids), rows)
    return {"companies": done, "rows": rows}
