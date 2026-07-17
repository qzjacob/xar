"""Andy 宏观数据库台（/api/andy/macro）—— XAR 原生 shadow 路由,不碰 vendored slx。

一次调用给全宏观组（rates/inflation/growth/liquidity/credit/fiscal/fx_commodity/
sentiment/macro_controls）的最新 PIT 读数 + 12 点序列 + 斜率,外加传导链本体序列化
与硅基核心族计数。与 link_theme 的逐指标循环不同,这里用两条批量 SQL（DISTINCT ON
+ 窗口函数）——~41 指标一次取齐,承载"数据库级"台面。?as_of= 是 PIT 上界
（knowledge_time <= as_of,与 slx PointInTimeContext 同谓词）。
"""
from __future__ import annotations

from datetime import date

from ..logging import get_logger
from ..ontology.macro_links import LINKS_BY_KEY, TRANSMISSIONS

log = get_logger("xar.andy_macro_db")

# 宏观组族清单（顺序即面板顺序;macro_controls 是既有占位族并入宏观组）。
# 前端 FAMILY_META 持有中文名/分组——此处只定"哪些族属于宏观台"。
MACRO_FAMILIES: tuple[str, ...] = (
    "rates", "inflation", "growth", "liquidity", "credit",
    "fiscal", "fx_commodity", "sentiment", "macro_controls",
)


def _slope(series: list[dict]) -> float | None:
    """12 点序列的简单斜率（末-首)/期数;与 slx ctx.slope 的最小二乘不同,仅面板箭头用。"""
    if len(series) < 2:
        return None
    return round((series[-1]["v"] - series[0]["v"]) / (len(series) - 1), 6)


def macro_overview(as_of: str | None = None) -> dict:
    from slx.db import connect

    asof = date.fromisoformat(as_of) if as_of else date.today()
    with connect() as conn:
        reg = conn.execute(
            "SELECT metric_key, display_name_zh, family, hardness, unit FROM metric_registry "
            "WHERE family = ANY(%s) ORDER BY family, metric_key",
            (list(MACRO_FAMILIES),)).fetchall()
        keys = [r[0] for r in reg]
        # 批量最新读数（每指标一行）
        latest = {r[0]: (r[1], float(r[2])) for r in conn.execute(
            "SELECT DISTINCT ON (metric_key) metric_key, valid_time, value FROM observation "
            "WHERE metric_key = ANY(%s) AND knowledge_time <= %s AND value IS NOT NULL "
            "ORDER BY metric_key, valid_time DESC, knowledge_time DESC",
            (keys, asof)).fetchall()}
        # 批量 12 点序列（每 (metric, valid_time) 取 PIT 最新版,再窗口取末 12 期）
        series: dict[str, list[dict]] = {}
        for mk, vt, val in conn.execute(
            "SELECT metric_key, valid_time, value FROM ("
            "  SELECT metric_key, valid_time, value,"
            "         row_number() OVER (PARTITION BY metric_key ORDER BY valid_time DESC) rn"
            "  FROM ("
            "    SELECT DISTINCT ON (metric_key, valid_time) metric_key, valid_time, value"
            "    FROM observation"
            "    WHERE metric_key = ANY(%s) AND knowledge_time <= %s AND value IS NOT NULL"
            "    ORDER BY metric_key, valid_time DESC, knowledge_time DESC"
            "  ) pit"
            ") w WHERE rn <= 12 ORDER BY metric_key, valid_time ASC",
                (keys, asof)).fetchall():
            series.setdefault(mk, []).append({"t": str(vt), "v": float(val)})
        # 硅基核心族计数（silicon_core 入口卡）
        core = conn.execute(
            "SELECT family, count(*) FROM metric_registry WHERE NOT (family = ANY(%s)) "
            "GROUP BY family ORDER BY family", (list(MACRO_FAMILIES),)).fetchall()

    fam_map: dict[str, list[dict]] = {}
    for mk, name_zh, family, hardness, unit in reg:
        link = LINKS_BY_KEY.get(mk)
        ser = series.get(mk, [])
        vt_val = latest.get(mk)
        fam_map.setdefault(family, []).append({
            "metric_key": mk, "name_cn": name_zh, "hardness": hardness, "unit": unit,
            "good_when": link.good_when if link else None,
            "value": vt_val[1] if vt_val else None,
            "valid_time": str(vt_val[0]) if vt_val else None,
            "slope": _slope(ser), "series": ser,
            "has_chain": bool(mk in {t.from_key for t in TRANSMISSIONS}
                              or mk in {t.to_key for t in TRANSMISSIONS}),
        })
    return {
        "as_of": str(asof),
        "families": [{"family": f, "metrics": fam_map.get(f, [])}
                     for f in MACRO_FAMILIES if fam_map.get(f)],
        "transmissions": [{"from": t.from_key, "to": t.to_key, "sign": t.sign,
                           "lag_hint": t.lag_hint, "rationale_zh": t.rationale_zh}
                          for t in TRANSMISSIONS],
        "silicon_families": [{"family": f, "count": int(n)} for f, n in core],
    }
