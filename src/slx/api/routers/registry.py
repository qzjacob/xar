"""GET /registry —— 暴露理论本体：A1–A8 锚点 + 指标本体字段。

纯只读元数据，不含读数。与 /metrics 列表的区别：这里以"本体"为中心（锚点为一等公民、
指标可按 anchor 聚合），用于看板的"理论咬合点"视图。soft 指标同样带 identification 水印。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_conn, identification

router = APIRouter(prefix="/registry", tags=["registry"])

_ANCHOR_COLS = "anchor_key, title, industrial_assumption, silicon_restatement, verdict"
_METRIC_COLS = (
    "metric_key, display_name_zh, family, theory_anchor, binding_scarcity, phase, "
    "mechanism, hardness, identification_strategy, falsification_condition, decision_window, "
    "source_grade, caveat, is_quantifiable, unit, geo_scope, status"
)


def _anchor_to_dict(row: tuple) -> dict:
    anchor_key, title, industrial_assumption, silicon_restatement, verdict = row
    return {
        "anchor_key": anchor_key,
        "title": title,
        "industrial_assumption": industrial_assumption,
        "silicon_restatement": silicon_restatement,
        "verdict": verdict,
    }


def _metric_to_dict(row: tuple) -> dict:
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
        "identification": identification(hardness, identification_strategy, caveat),
    }


@router.get("/anchors")
def list_anchors(conn=Depends(get_conn)) -> dict:
    """列出 A1–A8 八公理 + 两条元定律（theory_anchor 表全量）。"""
    rows = conn.execute(
        f"SELECT {_ANCHOR_COLS} FROM theory_anchor ORDER BY anchor_key"
    ).fetchall()
    return {"count": len(rows), "anchors": [_anchor_to_dict(r) for r in rows]}


@router.get("/anchors/{anchor_key}")
def get_anchor(anchor_key: str, conn=Depends(get_conn)) -> dict:
    """单个锚点 + 挂在其下的指标（theory_anchor 数组成员含该 key 的）。"""
    row = conn.execute(
        f"SELECT {_ANCHOR_COLS} FROM theory_anchor WHERE anchor_key = %s", (anchor_key,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"未知锚点：{anchor_key}")
    anchor = _anchor_to_dict(row)
    # theory_anchor 是 text[]；用数组包含运算符 @> 找挂在该锚点下的指标。
    metric_rows = conn.execute(
        f"SELECT {_METRIC_COLS} FROM metric_registry WHERE theory_anchor @> ARRAY[%s]::text[] "
        "ORDER BY family, metric_key",
        (anchor_key,),
    ).fetchall()
    anchor["metrics"] = [_metric_to_dict(r) for r in metric_rows]
    anchor["metric_count"] = len(metric_rows)
    return anchor


@router.get("/metrics")
def list_registry_metrics(
    conn=Depends(get_conn),
    anchor: str | None = Query(None, description="只返回挂在该 theory_anchor 下的指标"),
    family: str | None = Query(None, description="按 family 过滤"),
) -> dict:
    """暴露指标本体字段（与 /metrics 列表同源，但以本体视角，可按 anchor 过滤）。"""
    sql = f"SELECT {_METRIC_COLS} FROM metric_registry"
    conds, params = [], []
    if anchor is not None:
        conds.append("theory_anchor @> ARRAY[%s]::text[]")
        params.append(anchor)
    if family is not None:
        conds.append("family = %s")
        params.append(family)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY family, metric_key"
    rows = conn.execute(sql, tuple(params)).fetchall()
    return {
        "count": len(rows),
        "metrics": [_metric_to_dict(r) for r in rows],
        "disclaimer": "本体目录；soft 指标 identification_status=unidentified，不得当因果结论使用。",
    }
