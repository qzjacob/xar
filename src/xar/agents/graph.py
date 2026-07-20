"""The controllable report DAG. Deterministic node order, checkpoint after each
node, and a human-approval interrupt before publication.

scope -> graph_retrieve -> analysts -> [debate -> risk] -> editor -> evidence_gate
      -> AWAIT HUMAN APPROVAL -> publish
"""
from __future__ import annotations

import json

from ..logging import get_logger
from ..models.llm import BudgetExceeded
from ..storage import db
from . import debate, evidence_gate, nodes, report
from .state import RunState, new_run_id

log = get_logger("xar.agents.graph")


def run_report(request: dict, *, auto_approve: bool = False) -> dict:
    kind = request.get("kind", "deep_report")
    rs = RunState(new_run_id(), kind, request)
    rs.create()
    try:
        nodes.scope(rs)
        rs.checkpoint()
        nodes.graph_retrieve(rs)
        rs.checkpoint()
        nodes.run_analysts(rs)
        rs.checkpoint()
        if kind == "deep_report":
            debate.run_debate(rs)
            rs.checkpoint()
            debate.run_risk(rs)
            rs.checkpoint()
        content = report.synthesize(rs)
        metrics = evidence_gate.compute(rs, content)
        rs.put("metrics", metrics)
        _store_report(rs, content, metrics)
        # The gate is binding: auto_approve only fast-tracks reports that PASS. A failing
        # report (low coverage or high hallucination risk) is always held for human review
        # rather than silently published — so "high-confidence" and "low-confidence"
        # reports no longer share the same publication path.
        status = "published" if (auto_approve and metrics["passed"]) else "awaiting_approval"
        if auto_approve and not metrics["passed"]:
            log.warning("run %s held for review (gate failed: coverage=%.2f risk=%.2f)",
                        rs.run_id, metrics["evidence_coverage"], metrics["hallucination_risk"])
        rs.checkpoint(status=status)
        log.info("run %s -> %s (coverage=%.2f risk=%.2f)", rs.run_id, status,
                 metrics["evidence_coverage"], metrics["hallucination_risk"])
        return {"run_id": rs.run_id, "status": status, "kind": kind,
                "content_md": content, "metrics": metrics, "citations": rs.citations}
    except BudgetExceeded as e:
        rs.checkpoint(status="failed")
        log.warning("run %s failed: %s", rs.run_id, e)
        return {"run_id": rs.run_id, "status": "failed", "error": str(e)}
    except Exception as e:  # noqa: BLE001
        # 复评修复(CODE_REVIEW §3.7/ARCH P0-2 前置):此前仅 BudgetExceeded 被接住,
        # 任何其它异常(网络/LLM/DB)会让 run 永久卡在 'running'——既误导 ops 面板,
        # 也堵死同 scope 重跑。统一落 failed,错误入返回体与日志。
        rs.checkpoint(status="failed")
        log.exception("run %s failed (unhandled)", rs.run_id)
        return {"run_id": rs.run_id, "status": "failed", "error": f"{type(e).__name__}: {e}"}


def _store_report(rs: RunState, content: str, metrics: dict) -> None:
    db.execute(
        "INSERT INTO reports(run_id,kind,content_md,citations,metrics) VALUES(%s,%s,%s,%s,%s)",
        (rs.run_id, rs.kind, content, json.dumps(rs.citations, default=str),
         json.dumps(metrics, default=str)),
    )


def approve(run_id: str) -> dict:
    rs = RunState.load(run_id)
    if not rs:
        return {"error": "run not found"}
    rows = db.query("SELECT content_md, metrics FROM reports WHERE run_id=%s ORDER BY id DESC LIMIT 1",
                    (run_id,))
    r = rows[0] if rows else {}
    if rs.status == "published":   # 幂等:重复批准返回既有产物,不重写状态
        return {"run_id": run_id, "status": "published",
                "content_md": r.get("content_md", ""), "metrics": r.get("metrics", {})}
    if rs.status != "awaiting_approval":
        # 复评修复(CODE_REVIEW §3.1 局部):running/failed 的 run 不可被批准——
        # 否则半成品或失败 run 可被直接盖成 published,人审闸名存实亡。
        return {"error": f"run 状态为 {rs.status},仅 awaiting_approval 可批准",
                "run_id": run_id, "status": rs.status}
    rs.checkpoint(status="published")
    return {"run_id": run_id, "status": "published",
            "content_md": r.get("content_md", ""), "metrics": r.get("metrics", {})}


def get_report(run_id: str) -> dict | None:
    rows = db.query(
        "SELECT r.content_md, r.citations, r.metrics, rr.status, rr.kind "
        "FROM reports r JOIN report_runs rr ON rr.id=r.run_id "
        "WHERE r.run_id=%s ORDER BY r.id DESC LIMIT 1",
        (run_id,),
    )
    return rows[0] if rows else None
