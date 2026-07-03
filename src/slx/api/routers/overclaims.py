"""GET/POST /overclaims —— 过度宣称登记簿的对外面：当前 status + 最近 eval_log，及触发评估。

verdict ∈ {open, fixation_triggered, falsified, expired, inconclusive}（见 engine.overclaim）。
纪律：含 DID/面板/统计显著的 soft 断言在 v1 判 inconclusive——本服务把这点显式标成
identification_status=unidentified + needs_identification=true，绝不把它渲染成"已成立的因果结论"。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query

from slx.engine import overclaim as overclaim_engine

from ..deps import get_conn, identification

router = APIRouter(prefix="/overclaims", tags=["overclaims"])

# 注册表对外列（含 fixation/falsify 规则，便于看板展示"成立/证伪条件"）。
_CLAIM_COLS = (
    "claim_key, claim_text_zh, related_metrics, hardness, decision_window, window_start, "
    "fixation_rule, falsify_rule, status, last_evaluated, evidence_snapshot, owner"
)

# verdict → 对外语义注解（中文，与 engine.overclaim 的 mark 表呼应）。
_VERDICT_NOTE = {
    "open": "判定窗内、规则未触发：未决。",
    "fixation_triggered": "fixation_rule 成立：断言在 point-in-time 视图上固化（仍是观测，非因果证明）。",
    "falsified": "falsify_rule 成立：断言被证伪/收敛。",
    "expired": "判定窗已过仍未决：过期。",
    "inconclusive": "规则含 v1 未支持算子（DID/面板/显著性）或缺数据：暂不可判，需识别后重跑。",
}


def _claim_row_to_dict(row: tuple) -> dict:
    (claim_key, claim_text_zh, related_metrics, hardness, decision_window, window_start,
     fixation_rule, falsify_rule, status, last_evaluated, evidence_snapshot, owner) = row
    is_soft = hardness == "soft"
    return {
        "claim_key": claim_key,
        "claim_text_zh": claim_text_zh,
        "related_metrics": list(related_metrics) if related_metrics is not None else [],
        "hardness": hardness,
        "decision_window": decision_window,
        "window_start": window_start.isoformat() if isinstance(window_start, date) else window_start,
        "fixation_rule": fixation_rule,
        "falsify_rule": falsify_rule,
        "status": status,
        "verdict_note": _VERDICT_NOTE.get(status, ""),
        "last_evaluated": last_evaluated.isoformat() if last_evaluated is not None else None,
        "evidence_snapshot": evidence_snapshot,
        "owner": owner,
        # 断言级水印：soft 断言（多含 DID/面板）一律 unidentified，且明示"需识别"。
        "identification": identification(hardness, None,
                                         "登记簿断言：soft 含 DID/面板/显著性的规则在 v1 判 inconclusive。"),
        # 显式信号：该断言是否在等待识别策略（看板据此打"未识别"水印，禁止当结论）。
        "needs_identification": bool(is_soft or status == "inconclusive"),
    }


def _recent_eval_log(conn, claim_key: str, limit: int) -> list[dict]:
    """该断言最近 N 次评估留痕（按 evaluated_at 降序）。"""
    rows = conn.execute(
        "SELECT evaluated_at, as_of_date, verdict, metric_readings, triggered "
        "FROM overclaim_eval_log WHERE claim_key = %s "
        "ORDER BY evaluated_at DESC LIMIT %s",
        (claim_key, limit),
    ).fetchall()
    return [
        {
            "evaluated_at": ev.isoformat() if ev is not None else None,
            "as_of_date": ad.isoformat() if isinstance(ad, date) else ad,
            "verdict": verdict,
            "verdict_note": _VERDICT_NOTE.get(verdict, ""),
            "metric_readings": readings,
            "triggered": triggered,
        }
        for ev, ad, verdict, readings, triggered in rows
    ]


@router.get("")
def list_overclaims(
    conn=Depends(get_conn),
    log_limit: int = Query(5, ge=0, le=50, description="每条断言附带的最近 eval_log 条数"),
) -> dict:
    """各断言当前 status + 最近 eval_log（point-in-time 证据快照）。"""
    rows = conn.execute(
        f"SELECT {_CLAIM_COLS} FROM overclaim_registry ORDER BY claim_key"
    ).fetchall()
    claims = []
    for r in rows:
        item = _claim_row_to_dict(r)
        if log_limit:
            item["recent_eval_log"] = _recent_eval_log(conn, item["claim_key"], log_limit)
        claims.append(item)
    return {
        "count": len(claims),
        "claims": claims,
        "disclaimer": "登记簿是活监控，非裁决：verdict 为 point-in-time 观测；soft 断言未识别前不构成因果结论。",
    }


@router.get("/{claim_key}")
def get_overclaim(
    claim_key: str,
    conn=Depends(get_conn),
    log_limit: int = Query(20, ge=0, le=200),
) -> dict:
    """单条断言：当前 status + 较长 eval_log 历史（用于趋势）。"""
    from fastapi import HTTPException

    row = conn.execute(
        f"SELECT {_CLAIM_COLS} FROM overclaim_registry WHERE claim_key = %s", (claim_key,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"未知断言：{claim_key}")
    item = _claim_row_to_dict(row)
    item["recent_eval_log"] = _recent_eval_log(conn, claim_key, log_limit)
    return item


@router.post("/evaluate")
def evaluate_overclaims(
    as_of: date = Query(..., description="评估截面日 YYYY-MM-DD；引擎只取 knowledge_time<=as_of 的读数"),
) -> dict:
    """触发 engine.overclaim.run(as_of)：批量评估全部断言，写 eval_log + 更新 status。

    注意：run() 自己开连接并 commit（写 overclaim_eval_log / overclaim_registry），
    故此处不复用 get_conn 的只读连接，避免事务边界混淆。
    """
    results = overclaim_engine.run(as_of)  # list[(claim_key, verdict)]
    return {
        "as_of": as_of.isoformat(),
        "evaluated": len(results),
        "results": [
            {"claim_key": ck, "verdict": v, "verdict_note": _VERDICT_NOTE.get(v, "")}
            for ck, v in results
        ],
        "disclaimer": "verdict 为 point-in-time 观测；inconclusive=待识别（DID/面板未接），不可当结论。",
    }
