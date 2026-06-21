"""Wind (万得) connector via WindPy. Wind requires a licensed local terminal +
the WindPy client, so this is OFF by default (XAR_ENABLE_WIND=true to arm) and is
the only provider that reaches deep CN-A fundamentals. Fully guarded: absent a
working Wind terminal it returns nothing rather than failing."""
from __future__ import annotations

from datetime import date

from ..config import get_settings
from ..ingestion.registry import company_by_id
from ..ontology.standards import FinMetric
from ..storage import structured
from .base import log

# Wind indicator field -> canonical metric (annual statement indicators).
_FIELDS = {
    "or": FinMetric.REVENUE.value,
    "grossprofitmargin": FinMetric.GROSS_MARGIN.value,
    "netprofit": FinMetric.NET_INCOME.value,
    "roe_avg": FinMetric.ROE.value,
    "eps_diluted": FinMetric.EPS_DILUTED.value,
}


def _w():
    if not get_settings().enable_wind:
        return None
    try:
        from WindPy import w

        if not w.isconnected():
            w.start()
        return w
    except Exception as e:  # noqa: BLE001
        log.warning("WindPy unavailable: %s", e)
        return None


def available() -> bool:
    return _w() is not None


def _cn_code(company_id: str) -> str | None:
    c = company_by_id(company_id)
    if not c:
        return None
    return next((t for t in c.get("tickers", []) if t.endswith((".SZ", ".SS", ".SH"))), None)


def pull_fundamentals(company_id: str) -> int:
    w = _w()
    code = _cn_code(company_id)
    if not w or not code:
        return 0
    today = date.today().isoformat()
    fields = ",".join(_FIELDS.keys())
    try:
        data = w.wsd(code, fields, today, today, "unit=1;rptType=1")
    except Exception as e:  # noqa: BLE001
        log.warning("wind wsd %s: %s", code, e)
        return 0
    if getattr(data, "ErrorCode", -1) != 0:
        return 0
    n = 0
    for i, fld in enumerate(_FIELDS):
        try:
            val = data.Data[i][0]
        except Exception:
            continue
        if val is None:
            continue
        canon = _FIELDS[fld]
        structured.upsert_fundamental(company_id, canon, val, period="TTM", freq="ttm",
                                      unit="ratio" if "margin" in fld or fld == "roe_avg" else "CNY",
                                      source="wind")
        n += 1
    return n


def pull(company_id: str) -> dict:
    if not available():
        return {}
    out = {"fundamentals": pull_fundamentals(company_id)}
    log.info("wind %s: %s", company_id, out)
    return out
