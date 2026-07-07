"""衍生追踪指标计算引擎(零 LLM)。

读 fundamentals 原始序列 → 计算二阶信息(同比/环比/增速二阶导/比率/趋势斜率)→
写回 fundamentals(source='derived')。写回同一张表是全计划最高杠杆的复用:
research/thesis.py:dossier 的财务节自动带上这些衍生值(免费获得 [fundamental:cid:crpo_yoy]
引用锚),前端 KPI 表免费显示,UNIQUE(company_id,metric,period,source) 天然幂等。

财年安全:所有配对按 period_end 的**日窗**匹配(同比 350–380 天、环比 80–100 天),
绝不 parse 'FY2025'/'Q3' 这类跨源格式不一的 period 字符串。计算时以 source<>'derived'
排除自身产物,杜绝"衍生的衍生"反馈环。
"""
from __future__ import annotations

from datetime import date

from ..logging import get_logger
from ..ontology.indicators import INDICATOR_BY_KEY, IndicatorSpec, indicator_keys_for_company
from ..storage import db, structured

log = get_logger("xar.indicators")

# 同一 period_end 多源撞车时的取值优先级(报表口径 > 第三方聚合 > 抽取)。
_SOURCE_PRIORITY: dict[str, int] = {
    "edgar": 6, "cninfo": 5, "gangtise": 5, "wind": 4, "aifinmarket": 4,
    "futu": 3, "fmp": 3, "finnhub": 3, "polygon": 2, "yahoo": 2, "extracted": 1,
}


def _series(cid: str, metric: str, prefer_freq: str = "quarter") -> list[dict]:
    """某公司某原始指标的干净时间序列(按 period_end 升序,每期一点,去重取优先源)。

    **频率同质化**:同比/环比/趋势的口径要求序列单一频率——混入年报(全年累计)与季报
    (离散季)会把季度值和全年值错配同比(见评审 #1),也会让 slope4 趋势失真。故只保留
    单一频率:prefer_freq 点数足够则用之,否则退回众数频率。ratio_to 用 annual 口径调用,
    规避 A 股经 gangtise 的累计(YTD)营收把点时点存量/流量比压成锯齿(评审 #2)。"""
    rows = db.query(
        "SELECT period, period_end, freq, value, source FROM fundamentals "
        "WHERE company_id=%s AND metric=%s AND source<>'derived' "
        "  AND period_end IS NOT NULL AND value IS NOT NULL "
        "ORDER BY period_end",
        (cid, metric))
    best: dict[date, tuple[tuple[int, int], dict]] = {}
    for r in rows:
        pe = r["period_end"]
        # 撞期取值:先偏好目标频率(保住季报点,别被同日的年报全年值挤掉),再按权威源优先级。
        score = (1 if r["freq"] == prefer_freq else 0, _SOURCE_PRIORITY.get(r["source"], 0))
        cur = best.get(pe)
        if cur is None or score > cur[0]:
            best[pe] = (score, r)
    series = [best[k][1] for k in sorted(best)]
    # 单一频率:优先 prefer_freq(≥2 点),否则退回出现最多的频率
    counts: dict[str, int] = {}
    for r in series:
        counts[r.get("freq") or ""] = counts.get(r.get("freq") or "", 0) + 1
    keep = prefer_freq if counts.get(prefer_freq, 0) >= 2 else (
        max(counts, key=counts.get) if counts else None)
    if keep:
        series = [r for r in series if (r.get("freq") or "") == keep]
    return series


def _match(cands: list[dict], target: date, lo: int, hi: int) -> dict | None:
    """在 cands 里找 period_end 落在 target 前 [lo,hi] 天窗内、且最接近的那个点。"""
    best: dict | None = None
    best_gap = 10**9
    for r in cands:
        gap = (target - r["period_end"]).days
        if lo <= gap <= hi and abs(gap - (lo + hi) / 2) < best_gap:
            best, best_gap = r, abs(gap - (lo + hi) / 2)
    return best


def _yoy_points(series: list[dict]) -> list[tuple[dict, float, dict]]:
    """(当期点, 同比值, 去年同期点) 列表;仅在基期 >0 时产出(避免符号翻转的伪同比)。"""
    out = []
    for i, r in enumerate(series):
        p = _match(series[:i], r["period_end"], 350, 380)
        if p and p["value"] and p["value"] > 0:
            out.append((r, r["value"] / p["value"] - 1.0, p))
    return out


def _qoq_points(series: list[dict]) -> list[tuple[dict, float, dict]]:
    out = []
    for i, r in enumerate(series):
        p = _match(series[:i], r["period_end"], 80, 100)
        if p and p["value"] and p["value"] > 0:
            out.append((r, r["value"] / p["value"] - 1.0, p))
    return out


def _ols_slope_norm(values: list[float]) -> float | None:
    """近 n 点最小二乘斜率 / 平均幅度(归一化趋势方向)。

    归一化用 mean(|v|) 而非 |mean(v)|:后者在过零序列(如盈亏平衡附近的 fcf_margin)上
    均值≈0 会把斜率放大成荒谬值(评审 #8);mean(|v|) 是稳健的量纲尺度。"""
    n = len(values)
    if n < 2:
        return None
    xbar = (n - 1) / 2.0
    ybar = sum(values) / n
    num = sum((i - xbar) * (v - ybar) for i, v in enumerate(values))
    den = sum((i - xbar) ** 2 for i in range(n))
    scale = sum(abs(v) for v in values) / n
    if den == 0 or scale < 1e-9:
        return None
    return (num / den) / scale


def _points_for(spec: IndicatorSpec, cid: str) -> list[tuple[dict, float, list[dict]]]:
    """按变换算出 (锚点, 值, 输入点集) 列表;数据不足则空。"""
    if spec.transform == "ratio_to":
        # 存量/流量比用**年报口径**:年营收是干净全年流量、年末存量是干净时点值,比值可跨年比较,
        # 规避 A 股累计(YTD)营收把季度比压成锯齿(评审 #2)。
        base = _series(cid, spec.base_metric, prefer_freq="annual")
        other = {o["period_end"]: o for o in _series(cid, spec.other_metric, prefer_freq="annual")}
        if len(base) < spec.min_points:
            return []
        out = []
        for r in base:
            o = other.get(r["period_end"])
            if o and o["value"] and o["value"] > 0:
                out.append((r, r["value"] / o["value"], [r, o]))
        return out
    base = _series(cid, spec.base_metric)
    if len(base) < spec.min_points:
        return []
    if spec.transform == "yoy":
        return [(r, v, [p]) for r, v, p in _yoy_points(base)]
    if spec.transform == "qoq":
        return [(r, v, [p]) for r, v, p in _qoq_points(base)]
    if spec.transform == "yoy_accel":
        yoy = _yoy_points(base)
        anchors = [(r, v) for r, v, _ in yoy]
        out = []
        for i, (r, v) in enumerate(anchors):
            prior = _match([a[0] for a in anchors[:i]], r["period_end"], 80, 100)
            if prior is None:
                continue
            pv = next((av for ar, av in anchors if ar["period_end"] == prior["period_end"]), None)
            if pv is not None:
                out.append((r, v - pv, [r, prior]))
        return out
    if spec.transform == "slope4":
        out = []
        for i in range(3, len(base)):
            window = base[i - 3:i + 1]
            s = _ols_slope_norm([w["value"] for w in window])
            if s is not None:
                out.append((base[i], s, window))
        return out
    return []


def compute_company(cid: str, company: dict | None = None) -> dict:
    """为一家公司计算其行业适用的全部衍生指标,写回 fundamentals(source='derived')。"""
    if company is None:
        from ..ingestion.registry import company_by_id
        company = company_by_id(cid)
    written = 0
    for key in indicator_keys_for_company(company):
        spec = INDICATOR_BY_KEY[key]
        try:
            pts = _points_for(spec, cid)
        except Exception as e:  # noqa: BLE001 —— 单指标异常不拖累整公司
            log.warning("indicator %s failed for %s: %s", key, cid, e)
            continue
        for anchor, value, inputs in pts:
            structured.upsert_fundamental(
                cid, spec.key, value,
                period=anchor.get("period"), period_end=anchor["period_end"],
                freq=anchor.get("freq") or "quarter", unit=spec.unit, source="derived",
                meta={"indicator": spec.key, "transform": spec.transform,
                      "base_metric": spec.base_metric,
                      "inputs": [str(p["period_end"]) for p in inputs]})
            written += 1
    return {"company": cid, "indicators": len(indicator_keys_for_company(company)), "written": written}


def compute_all(limit: int | None = None) -> dict:
    """遍历所有有原始 base 指标数据的公司,计算衍生指标。"""
    from ..ontology.indicators import BASE_METRICS
    from ..ingestion.registry import company_by_id
    rows = db.query(
        "SELECT DISTINCT company_id FROM fundamentals "
        "WHERE metric = ANY(%s) AND source<>'derived' ORDER BY company_id",
        (list(BASE_METRICS),))
    cids = [r["company_id"] for r in rows][: (limit or len(rows))]
    total = 0
    for cid in cids:
        total += compute_company(cid, company_by_id(cid))["written"]
    return {"companies": len(cids), "written": total}
