"""GET /metrics —— 指标读数与注册表元数据。

铁律：as-of 读数只走 engine.point_in_time.PointInTimeContext（knowledge_time<=as_of），
本路由严禁 SELECT latest / 读 v_observation_current。每条响应都带 identification 水印块
（见 api.deps.identification）；soft → unidentified，不可把相关当因果暴露为确定结论。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from slx.engine.point_in_time import NoData, PointInTimeContext

from ..deps import get_conn, identification

router = APIRouter(prefix="/metrics", tags=["metrics"])

# 注册表对外暴露字段（与 metric_registry 列一一对应；只读本体）。
_REGISTRY_COLS = (
    "metric_key, display_name_zh, family, theory_anchor, binding_scarcity, phase, "
    "mechanism, hardness, identification_strategy, falsification_condition, decision_window, "
    "source_grade, caveat, is_quantifiable, unit, geo_scope, status"
)


def _row_to_registry(row: tuple) -> dict:
    """把 metric_registry 一行翻成对外字典，并附 identification 水印块。"""
    (metric_key, display_name_zh, family, theory_anchor, binding_scarcity, phase,
     mechanism, hardness, identification_strategy, falsification_condition, decision_window,
     source_grade, caveat, is_quantifiable, unit, geo_scope, status) = row
    return {
        "metric_key": metric_key,
        "display_name_zh": display_name_zh,
        "family": family,
        "theory_anchor": list(theory_anchor) if theory_anchor is not None else [],
        "binding_scarcity": binding_scarcity,
        "phase": phase,
        "mechanism": mechanism,
        "hardness": hardness,
        "identification_strategy": identification_strategy,
        "falsification_condition": falsification_condition,
        "decision_window": decision_window,
        "source_grade": source_grade,
        "caveat": caveat,
        "is_quantifiable": is_quantifiable,
        "unit": unit,
        "geo_scope": geo_scope,
        "status": status,
        # 水印：把 hardness/identification_strategy/caveat 翻成对外识别状态（soft→unidentified）。
        "identification": identification(hardness, identification_strategy, caveat),
    }


def _fetch_registry_row(conn, metric_key: str) -> tuple | None:
    return conn.execute(
        f"SELECT {_REGISTRY_COLS} FROM metric_registry WHERE metric_key = %s",
        (metric_key,),
    ).fetchone()


@router.get("")
def list_metrics(
    conn=Depends(get_conn),
    family: str | None = Query(None, description="按 family 过滤，可选"),
    hardness: str | None = Query(None, description="按 hardness 过滤：hard|medium|soft|wall"),
) -> dict:
    """列出注册表：带 theory_anchor / hardness / identification_strategy / source_grade 等本体字段。

    这是"本体目录"，不含读数；要取 as-of 数值请调 GET /metrics/{metric_key}。
    """
    sql = f"SELECT {_REGISTRY_COLS} FROM metric_registry"
    conds, params = [], []
    if family is not None:
        conds.append("family = %s")
        params.append(family)
    if hardness is not None:
        conds.append("hardness = %s")
        params.append(hardness)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY family, metric_key"
    rows = conn.execute(sql, tuple(params)).fetchall()
    metrics = [_row_to_registry(r) for r in rows]
    return {
        "count": len(metrics),
        "metrics": metrics,
        # 全局水印：提醒消费者本目录不输出因果断言，soft 项一律未识别。
        "disclaimer": "注册表为理论本体目录；soft 指标 identification_status=unidentified，不得当因果结论使用。",
    }


@router.get("/{metric_key}")
def get_metric(
    metric_key: str,
    conn=Depends(get_conn),
    as_of: date = Query(..., description="point-in-time 截面日 YYYY-MM-DD；只取 knowledge_time<=as_of 的读数"),
    n_points: int = Query(12, ge=2, le=200, description="返回序列点数（按 valid_time 升序）"),
) -> dict:
    """as-of 读数 + 序列（point-in-time，防前视）+ 注册表本体 + identification 水印。

    设计要点：
      - 读数严格走 PointInTimeContext（knowledge_time<=as_of），与回测/登记簿同一真值入口；
      - wall / is_quantifiable=false 的指标：value 恒为 NULL，直接报 not_quantified，不去查 observation；
      - soft 指标：identification.identification_status=unidentified，并在 value 旁挂 caveat 水印；
      - 无 as_of 之前可用读数 → NoData → value/series=None，附 note，而非 500（缺数据是常态，不是错误）。
    """
    reg_row = _fetch_registry_row(conn, metric_key)
    if reg_row is None:
        raise HTTPException(status_code=404, detail=f"未登记的指标：{metric_key}")
    registry = _row_to_registry(reg_row)

    body: dict = {
        "metric_key": metric_key,
        "as_of": as_of.isoformat(),
        "unit": registry["unit"],
        "registry": registry,
        "identification": registry["identification"],  # 顶层再挂一份，消费者一眼可见水印
        "point_in_time": True,                          # 明示：所有数值都遵守 knowledge_time<=as_of
    }

    # 承重墙 / 不可量化项：value 永远 NULL，不查 observation。
    if registry["is_quantifiable"] is False:
        body.update({
            "value": None,
            "series": [],
            "note": "wall / 不可量化项：无数值读数，仅作定性边界（identification_status=not_quantified）。",
        })
        return body

    # as-of 当日能知道的最新读数（缺数据是常态，记 None + note，不抛 500）。
    try:
        body["value"] = ctx_value(conn, as_of, metric_key)
    except NoData:
        body["value"] = None
        body["note"] = f"as_of={as_of.isoformat()} 之前无可用读数（knowledge_time<=as_of 为空）。"

    # point-in-time 序列（升序），供前端画趋势；同样防前视。
    try:
        series = ctx_series(conn, as_of, metric_key, n_points)
        body["series"] = [{"valid_time": d.isoformat(), "value": v} for d, v in series]
    except NoData:
        body["series"] = []

    # 斜率（趋势方向；点数不足则 None，不报错）。
    try:
        body["slope"] = ctx_slope(conn, as_of, metric_key, n_points)
    except NoData:
        body["slope"] = None

    return body


# ── point-in-time 入口的薄封装（保证全路由共用同一 as_of 上下文语义）──────────────
def ctx_value(conn, as_of: date, metric_key: str) -> float:
    return PointInTimeContext(conn, as_of).value(metric_key)


def ctx_series(conn, as_of: date, metric_key: str, n_points: int):
    return PointInTimeContext(conn, as_of).series(metric_key, n_points)


def ctx_slope(conn, as_of: date, metric_key: str, n_points: int) -> float:
    return PointInTimeContext(conn, as_of).slope(metric_key, n_points)
