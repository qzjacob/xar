"""Gangtise 投研 provider — deep CN sell-side research into XAR's canonical schema.

Structured (→ FinMetric via structured.upsert_fundamental/upsert_estimate):
  - financial reports (income/balance/cashflow, A-share consolidated latest)
  - valuation multiples + historical percentile (peTtm/psTtm/pbMrq)
  - 券商一致预期 (analyst consensus by fiscal year) → estimates  ← NET NEW vs Futu/finnhub
研 text (→ ingestion.base.Doc→save, source='gangtise', permission='grey' → triage/KG):
  - stock one-pager / investment-logic / peer-comparison Markdown

CN-focused: gates on a resolvable A-share/DR code (registry .SS/.SH/.SZ ticker or name).
OFF unless enable_gangtise + GTS keys → available() False → callers no-op (turnkey-safe).
Field maps grounded in the live API's real (short) field names.
"""
from __future__ import annotations

from ...ingestion.registry import company_by_id
from ...logging import get_logger
from ...ontology.standards import FinMetric as FM
from ...storage import structured
from . import client

log = get_logger("xar.gangtise")

available = client.available

# ── field maps (live API short names → canonical FinMetric) ────────────────────
# One canonical metric may have ranked source candidates (first non-null wins).
_INCOME_MAP: dict[str, tuple[str, ...]] = {
    FM.REVENUE.value: ("opRev", "totalOpRev"),        # core op revenue; total for financial firms
    FM.COST_OF_REVENUE.value: ("opCost",),
    FM.RD_EXPENSE.value: ("rdExp",),
    FM.OPERATING_INCOME.value: ("opProfit",),
    FM.NET_INCOME.value: ("netProfitAttrParent", "netProfit"),   # 归母净利润 preferred
    FM.EPS_DILUTED.value: ("dilutedEPS", "basicEPS"),
}
_BALANCE_MAP: dict[str, tuple[str, ...]] = {
    FM.TOTAL_ASSETS.value: ("totalAssets",),
    FM.TOTAL_LIABILITIES.value: ("totalLiab",),
    FM.TOTAL_EQUITY.value: ("totalEquity",),
    FM.CASH.value: ("monetaryAssets", "cash"),
    FM.INVENTORY.value: ("inventory",),
}
_CASHFLOW_MAP: dict[str, tuple[str, ...]] = {
    FM.OPERATING_CASH_FLOW.value: ("netOpCashFlows",),
    FM.CAPEX.value: ("cashPaidAcqConstructAssets",),
}
# sga_expense = 销售费用 + 管理费用 (summed); total_debt = sum of any borrowing lines present.
_SGA_FIELDS = ("salesExp", "totalAdminExp")
_DEBT_FIELDS = ("stBorrow", "ltBorrow", "shortTermBorrow", "longTermBorrow",
                "bondsPay", "bondPayable", "ltBorrowings", "stBorrowings")
_VALUATION = {"peTtm": (FM.PE.value, "pe_percentile"),
              "psTtm": (FM.PS.value, "ps_percentile"),
              "pbMrq": (FM.PB.value, "pb_percentile")}
# 一致预期 consensus field → (canonical metric, scale, unit)
_FORECAST_MAP = {
    "netIncome": (FM.NET_INCOME.value, 1_000_000.0, "CNY"),   # API 百万元 → CNY
    "eps": (FM.EPS_DILUTED.value, 1.0, "CNY/share"),
    "netIncomeYoy": (FM.EARNINGS_GROWTH.value, 0.01, "ratio"),  # percent → fraction
    "pe": (FM.PE.value, 1.0, "ratio"), "pb": (FM.PB.value, 1.0, "ratio"),
    "ps": (FM.PS.value, 1.0, "ratio"), "roe": (FM.ROE.value, 0.01, "ratio"),
}
_RESEARCH = {"one_pager": "/one-pager", "investment_logic": "/investment-logic",
             "peer_comparison": "/peer-comparison"}

_CODE_CACHE: dict[str, str | None] = {}
_CCY = {"人民币": "CNY", "港币": "HKD", "港元": "HKD", "美元": "USD", "美金": "USD"}


def _freq(category) -> str:
    """报告期 category (一季报/中报/三季报/年报) → freq label."""
    c = str(category or "")
    if "年报" in c or "annual" in c.lower():
        return "annual"
    if "中报" in c or "半年" in c or "interim" in c.lower():
        return "semi"
    return "quarter"


def _num(v):
    if v in (None, "", "N/A", "--"):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def gts_code(company_id: str) -> str | None:
    """Registry company → Gangtise gtsCode. CN A-shares only (.SS/.SH/.SZ ticker, else name).
    Cached; None for names Gangtise can't resolve (non-CN)."""
    if company_id in _CODE_CACHE:
        return _CODE_CACHE[company_id]
    c = company_by_id(company_id)
    code = None
    if c:
        cn = next((t for t in (c.get("tickers") or [])
                   if t.endswith((".SS", ".SH", ".SZ"))), None)
        if cn:                                    # 600519.SS → resolve to canonical gtsCode
            code = client.resolve_security(cn.split(".")[0])
        elif c.get("region") == "CN":             # fall back to the Chinese name
            code = client.resolve_security((c.get("name") or "").split(" ")[0])
    _CODE_CACHE[company_id] = code
    return code


def _period_end(endDate) -> str | None:
    s = str(endDate or "").strip()
    if len(s) == 8 and s.isdigit():               # 20260331 → 2026-03-31
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s or None


# ── structured: financial reports → fundamentals ───────────────────────────────
def _write_statement(company_id: str, url: str, fmap: dict, *, extras=None) -> int:
    data = client.post(url, {"securityCode": _CODE_CACHE.get(company_id), "period": ["latest"],
                             "reportType": ["consolidated"], "fieldList": [],
                             "startDate": None, "endDate": None, "fiscalYear": None})
    rows = client.rows(data)
    if not rows:
        return 0
    row = rows[0]
    pend = _period_end(row.get("endDate"))
    # Gangtise's balance-sheet rows carry companyType/currency positionally SWAPPED vs their
    # own fieldList, so row['currency'] can be '一般企业'. Trust only known currency values.
    ccy = _CCY.get(str(row.get("currency") or ""), "CNY")
    freq = _freq(row.get("category"))
    n = 0
    for metric, candidates in fmap.items():
        val = next((_num(row.get(f)) for f in candidates if _num(row.get(f)) is not None), None)
        if val is None:
            continue
        structured.upsert_fundamental(company_id, metric, val, period="latest", period_end=pend,
                                      freq=freq, unit=ccy, source="gangtise",
                                      meta={"code": _CODE_CACHE.get(company_id), "fiscalYear": row.get("fiscalYear")})
        n += 1
    if extras:
        n += extras(company_id, row, pend, ccy, freq)
    return n


def _income_extras(company_id, row, pend, ccy, freq) -> int:
    n = 0
    sga = sum(v for f in _SGA_FIELDS if (v := _num(row.get(f))) is not None)
    if any(_num(row.get(f)) is not None for f in _SGA_FIELDS):
        structured.upsert_fundamental(company_id, FM.SGA_EXPENSE.value, sga, period="latest",
                                      period_end=pend, freq=freq, unit=ccy, source="gangtise")
        n += 1
    rev, cost = _num(row.get("opRev")), _num(row.get("opCost"))
    if rev and cost is not None:                  # gross profit + margin (API omits them)
        gp = rev - cost
        structured.upsert_fundamental(company_id, FM.GROSS_PROFIT.value, gp, period="latest",
                                      period_end=pend, freq=freq, unit=ccy, source="gangtise")
        structured.upsert_fundamental(company_id, FM.GROSS_MARGIN.value, gp / rev, period="latest",
                                      period_end=pend, freq=freq, unit="ratio", source="gangtise")
        n += 2
    return n


def _balance_extras(company_id, row, pend, ccy, freq) -> int:
    debt = sum(v for f in _DEBT_FIELDS if (v := _num(row.get(f))) is not None)
    if any(_num(row.get(f)) is not None for f in _DEBT_FIELDS):
        structured.upsert_fundamental(company_id, FM.TOTAL_DEBT.value, debt, period="latest",
                                      period_end=pend, freq=freq, unit=ccy, source="gangtise")
        return 1
    return 0


def _cashflow_extras(company_id, row, pend, ccy, freq) -> int:
    ocf, capex = _num(row.get("netOpCashFlows")), _num(row.get("cashPaidAcqConstructAssets"))
    if ocf is not None and capex is not None:     # free cash flow
        structured.upsert_fundamental(company_id, FM.FREE_CASH_FLOW.value, ocf - capex,
                                      period="latest", period_end=pend, freq=freq,
                                      unit=ccy, source="gangtise")
        return 1
    return 0


def pull_financials(company_id: str) -> int:
    if not _CODE_CACHE.get(company_id):
        return 0
    return (_write_statement(company_id, client.INCOME_URL, _INCOME_MAP, extras=_income_extras)
            + _write_statement(company_id, client.BALANCE_URL, _BALANCE_MAP, extras=_balance_extras)
            + _write_statement(company_id, client.CASHFLOW_URL, _CASHFLOW_MAP, extras=_cashflow_extras))


# ── structured: valuation multiples + historical percentile → fundamentals ─────
def pull_valuation(company_id: str) -> int:
    from datetime import date, timedelta

    code = _CODE_CACHE.get(company_id)
    if not code:
        return 0
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=7)).isoformat()
    n = 0
    for ind, (metric, pct_metric) in _VALUATION.items():
        rows = client.rows(client.post(client.VALUATION_URL,
                           {"securityCode": code, "indicator": ind, "startDate": start, "endDate": end}))
        if not rows:
            continue
        row = rows[-1]                            # latest trade date
        td = str(row.get("tradeDate") or end)
        val, pct = _num(row.get("value")), _num(row.get("percentileRank"))
        if val is not None:
            structured.upsert_fundamental(company_id, metric, val, period=td, period_end=td,
                                          freq="daily", unit="ratio", source="gangtise", meta={"code": code})
            n += 1
        if pct is not None:                       # historical percentile — NET NEW (0-100)
            structured.upsert_fundamental(company_id, pct_metric, pct, period=td, period_end=td,
                                          freq="daily", unit="percent", source="gangtise", meta={"code": code})
            n += 1
    return n


# ── structured: analyst consensus (一致预期) → estimates ────────────────────────
def pull_forecasts(company_id: str) -> int:
    from datetime import date, timedelta

    from ...config import get_settings

    code = _CODE_CACHE.get(company_id)
    if not code:
        return 0
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=30)).isoformat()
    data = client.post(client.EARNING_FORECAST_URL,
                       {"securityCode": code, "startDate": start, "endDate": end,
                        "consensusList": list(dict.fromkeys(k for k in _FORECAST_MAP))})
    updates = (data or {}).get("updateList") or []
    if not updates:
        return 0
    latest = updates[-1]                          # newest consensus snapshot
    as_of = str(latest.get("date") or end)
    yrs = get_settings().gangtise_forecast_years
    n = 0
    for fy in (latest.get("fieldList") or [])[:yrs]:
        period = str(fy.get("forecastYear") or "")     # e.g. 2026E
        for src, (metric, scale, unit) in _FORECAST_MAP.items():
            v = _num(fy.get(src))
            if v is None:
                continue
            structured.upsert_estimate(company_id, metric, v * scale, as_of, period=period,
                                       unit=unit, source="gangtise", meta={"code": code})
            n += 1
    return n


# ── 研 text → documents (parse→embed→triage→KG feeds the thesis engine) ────────
def pull_research(company_id: str) -> int:
    code = _CODE_CACHE.get(company_id)
    if not code:
        return 0
    from ...ingestion.base import Doc, save

    c = company_by_id(company_id)
    name = (c.get("name") if c else "") or code
    n = 0
    for doc_type, suffix in _RESEARCH.items():
        data = client.post(client.AGENT_URL + suffix, {"securityCode": code})
        text = (data or {}).get("content") if isinstance(data, dict) else None
        if not text or not str(text).strip():
            continue
        save(Doc(company_id=company_id, source="gangtise", doc_type=doc_type,
                 title=f"{name} · {doc_type}", text=str(text),
                 url=f"gangtise://agent/{doc_type}/{code}",
                 published_at=(data or {}).get("date") or None,
                 permission="grey", license_tag="gangtise-research-extracted-facts-self-use",
                 meta={"code": code, "agent": doc_type}))
        n += 1
    return n


def pull(company_id: str) -> dict:
    if not available() or not gts_code(company_id):
        return {}
    out = {"financials": pull_financials(company_id), "valuation": pull_valuation(company_id),
           "forecasts": pull_forecasts(company_id), "research": pull_research(company_id)}
    log.info("gangtise %s: %s", company_id, out)
    return out
