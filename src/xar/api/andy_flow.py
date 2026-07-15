"""Andy 资金流策略面(/api/andy/flow*)—— XAR 原生 shadow 路由,不碰 vendored slx。

四面板单端点:大类资产流向(ETF 篮量价矩阵)/ 风格与广度(因子对 z)/ 情绪与仓位
(P/C + 空头回补榜 + 语义 flow 事件流)/ 策略综合(risk-on 表盘 + 每资产类倾斜)。
数据全部来自 research/flow 的读接口(alt_signals + kg_events,零外呼);?as_of=
经 altstore.series 的 observed_at 谓词实现 PIT 语义(与 Andy 模块口径一致)。

策略综合是**规则式加权**(z 均值 → 档位),只做追踪与倾斜提示,不做交易执行
(交易层是 #59 的 deferred 范围)。
"""
from __future__ import annotations

import statistics
from datetime import date

from ..logging import get_logger
from ..research import flow
from ..storage import db

log = get_logger("xar.andy_flow")

# 资产类展示序 + 中文名(面板行序稳定)
_CLASS_ORDER = ("equity_us", "equity_intl", "credit", "duration", "gold",
                "commodity", "usd", "crypto", "cash")
_CLASS_CN = {"equity_us": "美股", "equity_intl": "海外股票", "credit": "信用债",
             "duration": "利率久期", "gold": "黄金", "commodity": "大宗商品",
             "usd": "美元", "crypto": "加密资产", "cash": "现金"}
_OW, _UW = 0.25, -0.25   # 倾斜档位阈值(composite ∈ [-1,1])


def _stance(score: float | None) -> str:
    if score is None:
        return "no_data"
    return "overweight" if score >= _OW else "underweight" if score <= _UW else "neutral"


def _short_interest_top(as_of: date | None, limit: int = 10) -> list[dict]:
    """空头回补天数榜(最新期,按 DTC 降序)—— 挤压弹药表。"""
    from datetime import timedelta

    pit = (as_of + timedelta(days=1)) if as_of else None   # observed_at 是 timestamptz,次日 0 点排他上界
    rows = db.query(
        "SELECT DISTINCT ON (s.company_id) s.company_id, c.name, s.value dtc, s.period_end, "
        "s.meta->>'ticker' ticker FROM alt_signals s JOIN companies c ON c.id=s.company_id "
        "WHERE s.signal_key='flow.days_to_cover'"
        + (" AND s.observed_at < %s" if pit else "") +
        " ORDER BY s.company_id, s.period_end DESC",
        ((pit,) if pit else ()))
    rows.sort(key=lambda r: -float(r["dtc"]))
    return [{"company_id": r["company_id"], "name": r["name"], "ticker": r["ticker"],
             "days_to_cover": round(float(r["dtc"]), 1), "period_end": str(r["period_end"])}
            for r in rows[:limit]]


def _flow_events(as_of: date | None, limit: int = 12) -> list[dict]:
    """语义 flow 事件流:量价越阈信号 + 投行点评抽取(市场级含 company/theme 双空行)。"""
    rows = db.query(
        "SELECT e.event_type, e.event_date, e.polarity, e.summary, e.attrs, e.theme, "
        "c.name company FROM kg_events e LEFT JOIN companies c ON c.id=e.company_id "
        "WHERE e.event_type IN ('flow_signal','flow_insight') AND e.invalidated_at IS NULL"
        + (" AND e.event_date <= %s" if as_of else "") +
        " ORDER BY e.event_date DESC NULLS LAST, e.id DESC LIMIT %s",
        ((as_of, limit) if as_of else (limit,)))
    return [{"type": r["event_type"], "date": str(r["event_date"]) if r["event_date"] else None,
             "polarity": r["polarity"], "summary": r["summary"], "company": r["company"],
             "theme": r["theme"], "attrs": r["attrs"] or {}} for r in rows]


def _theme_scores(as_of: date | None) -> list[dict]:
    """主题净分一览(宏观 → 行业的交接把手;深链到 Genny)。"""
    from ..ingestion.registry import THEMES

    out = []
    for tid, meta in THEMES.items():
        latest = flow._latest("flow.theme_net_score", theme=tid, as_of=as_of)
        out.append({"theme": tid, "name_cn": meta["nameCn"],
                    "score": (latest or {}).get("v"), "as_of": (latest or {}).get("d"),
                    "genny_link": "/genny"})
    out.sort(key=lambda r: -(r["score"] if r["score"] is not None else -9))
    return out


def flow_overview(as_of: str | None = None) -> dict:
    asof = date.fromisoformat(as_of) if as_of else None
    snap = flow.flow_snapshot("market", as_of=asof)

    # 策略综合:每资产类 = 成员 ETF composite 均值 → 档位;drivers 给出可审计构成
    by_class: dict[str, list[dict]] = {}
    for a in snap["assets"]:
        by_class.setdefault(a["asset_class"], []).append(a)
    tilts = []
    for cls in _CLASS_ORDER:
        members = by_class.get(cls, [])
        scores = [a["composite"] for a in members if a["composite"] is not None]
        score = round(statistics.fmean(scores), 2) if scores else None
        tilts.append({
            "asset_class": cls, "label_cn": _CLASS_CN.get(cls, cls),
            "score": score, "stance": _stance(score),
            "drivers": [{"ticker": a["ticker"], "composite": a["composite"],
                         "obv_z": a["obv_z"], "mom_63d": a["mom_63d"]} for a in members],
        })
    return {
        "as_of": as_of or str(date.today()),
        "assets": snap["assets"],
        "styles": snap["styles"],
        "sentiment": {
            "pc": snap["pc"],
            "short_interest_top": _short_interest_top(asof),
            "flow_events": _flow_events(asof),
        },
        "strategy": {
            "risk_on": snap["risk_on"],
            "tilts": tilts,
        },
        "themes": _theme_scores(asof),
    }


def flow_series(signal_key: str, theme: str | None = None, company_id: str | None = None,
                as_of: str | None = None, limit: int = 120) -> dict:
    """钻取端点:任一 flow.* 信号的原始序列(theme 可为 etf:*/pair:*/主题 id)。"""
    from ..ontology.flow import FLOW_BY_KEY

    spec = FLOW_BY_KEY.get(signal_key)
    if spec is None:
        return {"error": f"unknown flow signal {signal_key!r}"}
    asof = date.fromisoformat(as_of) if as_of else None
    return {"signal_key": signal_key, "name_cn": spec.name_cn, "unit": spec.unit,
            "series": flow._series(signal_key, theme=theme, company_id=company_id,
                                   as_of=asof, limit=limit)}
