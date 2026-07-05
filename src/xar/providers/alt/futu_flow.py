"""富途资金流 alt-data provider —— 主力(超大单+大单)日度净流入。

写 ``alt.futu_main_capital_flow``(company-scope, daily)。主力资金持续净流入 = 机构
资金在盘面的足迹,是需求/估值的高频代理,覆盖港股/A股/美股。绑定派生自 ticker
(altdata._futu_code),故无需策展。经 OpenD 本地网关;OpenD 不可用则整源 graceful-skip。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from ...config import get_settings
from ...ontology.altdata import SIGNALS_BY_KEY, bindings
from ...storage.altstore import upsert_signal
from ..base import log
from ..futu import _num

_KEY = "alt.futu_main_capital_flow"


def available() -> bool:
    from ..futu import available as _avail

    return _avail()


def _main_inflow(row) -> float | None:
    """主力净流入:优先 main_in_flow;港股常为 N/A → 回退 超大单+大单。"""
    main = _num(row.get("main_in_flow"))
    if main is not None:
        return main
    sup, big = _num(row.get("super_in_flow")), _num(row.get("big_in_flow"))
    if sup is None and big is None:
        return None
    return (sup or 0.0) + (big or 0.0)


def _flow_date(item_time) -> str | None:
    try:
        return datetime.strptime(str(item_time)[:10], "%Y-%m-%d").date().isoformat()
    except (ValueError, TypeError):
        return None


def _ccy(code: str) -> str:
    if code.startswith("HK."):
        return "HKD"
    if code.startswith(("SH.", "SZ.")):
        return "CNY"
    return "USD"


def pull(limit: int | None = None) -> dict:
    """拉取绑定公司的主力资金日度净流入,写 company-scope 信号。``limit`` = 本轮最多
    处理的公司数(节流);None = 全量。返回统计。"""
    from ..futu import _quote_ctx

    ctx = _quote_ctx()
    if ctx is None:
        return {"skipped": "futu OpenD unavailable"}
    from futu import PeriodType, RET_OK

    spec = SIGNALS_BY_KEY[_KEY]
    bound = [(cid, b.futu_code) for cid, b in bindings().items() if b.futu_code]
    s = get_settings()
    start = (date.today() - timedelta(days=s.futu_flow_lookback_days)).isoformat()
    end = date.today().isoformat()
    stats: dict = {"bound_companies": len(bound), "companies": set(),
                   "rows": 0, "failed": 0}
    todo = bound[: limit] if limit else bound
    for cid, code in todo:
        try:
            r, df = ctx.get_capital_flow(code, period_type=PeriodType.DAY,
                                         start=start, end=end)
        except Exception as e:  # noqa: BLE001 — 单公司失败不沉整篮
            stats["failed"] += 1
            log.warning("futu_flow %s: %s", code, str(e)[:100])
            continue
        if r != RET_OK or df is None or not len(df):
            continue
        for _, row in df.iterrows():
            val = _main_inflow(row)
            pe = _flow_date(row.get("capital_flow_item_time"))
            if val is None or pe is None:
                continue
            upsert_signal(spec.key, period_end=pe, value=val, company_id=cid,
                          unit=_ccy(code), source=spec.source,
                          meta={"code": code,
                                "super_in_flow": _num(row.get("super_in_flow")),
                                "big_in_flow": _num(row.get("big_in_flow")),
                                "mid_in_flow": _num(row.get("mid_in_flow")),
                                "sml_in_flow": _num(row.get("sml_in_flow")),
                                "total_in_flow": _num(row.get("in_flow"))})
            stats["rows"] += 1
            stats["companies"].add(cid)
    stats["companies"] = len(stats["companies"])
    log.info("futu_flow: %s", stats)
    return stats
