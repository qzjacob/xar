"""独立抓取审计智能体:对 CN 非标语义抓取+归档做每日复核。

两条道:
  · integrity_report()  零 LLM —— 每 doc_type 计数/24h 增量/水位线新鲜度 vs SLO、company
    链接率、KG 抽取积压、expert 覆盖率、评级行、clue 对账、回填游标、EDB 指标新鲜度;
  · spot_check()        LLM(TaskClass.AUDIT,**独立于生产 GLM 的强 token 模型**)—— 分层
    抽样近 48h 文档+其衍生物,零 LLM `_grounded` 预检后逐篇裁决 company/doc_type/接地/链接合理性。

run_audit() 整合 → kvstate 'research_audit';文档级失败 → kg_extracted_at=NULL 重排队 + meta 标记。
"""
from __future__ import annotations

import json

from pydantic import BaseModel

from ..logging import get_logger
from ..models import llm
from ..models.router import TaskClass
from ..ontology.research_docs import DOCS_BY_TYPE
from ..storage import db, kvstate

log = get_logger("xar.research_audit")

_RESEARCH_DOC_TYPES = tuple(DOCS_BY_TYPE)


class AuditVerdict(BaseModel):
    company_link_ok: bool = True
    doc_type_ok: bool = True
    extraction_grounded: bool = True
    link_sensible: bool = True
    severity: str = "low"          # low | medium | high
    notes_zh: str = ""


# ── 零 LLM 完整性对账 ─────────────────────────────────────────────────────────────
def integrity_report() -> dict:
    by_type = db.query(
        "SELECT doc_type, count(*) n, "
        "count(*) FILTER (WHERE ingested_at > now() - interval '24 hours') fresh24, "
        "count(*) FILTER (WHERE company_id IS NOT NULL) linked, "
        "count(*) FILTER (WHERE kg_extracted_at IS NULL) kg_pending, "
        "max(ingested_at) last_at "
        "FROM documents WHERE source IN ('gangtise','aifinmarket') "
        "AND doc_type = ANY(%s) GROUP BY doc_type ORDER BY doc_type",
        (list(_RESEARCH_DOC_TYPES),))
    docs = []
    for r in by_type:
        spec = DOCS_BY_TYPE.get(r["doc_type"])
        n = int(r["n"])
        docs.append({
            "doc_type": r["doc_type"], "n": n, "fresh24h": int(r["fresh24"]),
            "link_rate": round(int(r["linked"]) / n, 3) if n else None,
            "kg_pending": int(r["kg_pending"]),
            "cadence_sla_h": spec.cadence_hours if spec else None,
            "last_at": r["last_at"]})
    expert_cov = db.query(
        "SELECT count(*) processed, count(*) FILTER (WHERE kept) kept FROM expert_insights "
        "WHERE source='gangtise'")[0]
    ratings = db.query("SELECT count(*) c FROM analyst_ratings WHERE source='gangtise'")[0]["c"]
    edb = db.query(
        "SELECT signal_key, max(period_end) latest, count(*) n FROM alt_signals "
        "WHERE source='wind_edb' GROUP BY signal_key ORDER BY signal_key")
    return {
        "by_doc_type": docs,
        "expert": {"processed": int(expert_cov["processed"]), "kept": int(expert_cov["kept"])},
        "ratings_rows": int(ratings),
        "edb": [{"key": r["signal_key"], "latest": r["latest"], "n": int(r["n"])} for r in edb],
        "clue": kvstate.get_state("gangtise_clue_state").get("last"),
    }


# ── LLM 抽样复核(独立模型)──────────────────────────────────────────────────────
_SYSTEM_AUDIT = (
    "你是独立于生产抽取管线的审计员。给你一篇已归档的 CN 投研文档(券商研报/纪要/MD&A)及其"
    "在系统里的衍生物(公司链接、doc_type、抽取出的洞见)。判断:company_link_ok(公司链接是否正确)、"
    "doc_type_ok(文档类型标注是否正确)、extraction_grounded(抽取洞见是否有文中依据、非幻觉)、"
    "link_sensible(洞见的立场/催化类型是否合理)。任何一项存疑给 severity=medium/high 并在 notes_zh 说明。"
)


def _sample(n: int) -> list[dict]:
    return db.query(
        "SELECT d.id, d.doc_type, d.company_id, d.title, d.text, "
        "(SELECT thesis FROM expert_insights e WHERE e.doc_id=d.id) AS insight, "
        "(SELECT stance FROM expert_insights e WHERE e.doc_id=d.id) AS stance "
        "FROM documents d WHERE d.source='gangtise' AND d.doc_type = ANY(%s) "
        "AND d.ingested_at > now() - interval '48 hours' "
        "ORDER BY d.ingested_at DESC LIMIT %s", (list(_RESEARCH_DOC_TYPES), n))


def spot_check(n: int = 12, run_id: str | None = None) -> dict:
    from ..kg.extract import _grounded
    docs = _sample(n)
    verdicts, flagged = [], []
    for d in docs:
        # 零 LLM 预检:洞见证据是否在文中(降 LLM 负担 + 独立锚点)
        pre_ok = True
        if d.get("insight"):
            pre_ok = _grounded(str(d["insight"])[:120], d.get("text") or "")
        prompt = (f"doc_type={d['doc_type']} company={d['company_id']}\n标题:{d['title']}\n"
                  f"抽取洞见:{d.get('insight') or '(无)'} stance={d.get('stance')}\n"
                  f"预检接地={pre_ok}\n\n<CONTENT>\n{(d.get('text') or '')[:3000]}\n</CONTENT>")
        try:
            v = llm.complete_json(prompt, AuditVerdict, system=_SYSTEM_AUDIT,
                                  task=TaskClass.AUDIT, node="audit", run_id=run_id, max_tokens=800)
        except Exception as e:  # noqa: BLE001
            log.warning("audit spot_check %s failed: %s", d["id"], e)
            continue
        row = {"doc_id": d["id"], "doc_type": d["doc_type"], **v.model_dump()}
        verdicts.append(row)
        if not (v.company_link_ok and v.doc_type_ok and v.extraction_grounded and v.link_sensible):
            flagged.append(row)
    return {"checked": len(verdicts), "flagged": len(flagged), "verdicts": verdicts}


def run_audit(no_llm: bool = False, run_id: str | None = None) -> dict:
    run_id = run_id or llm.new_batch_run_id("batch")     # 独立预算帽
    out: dict = {"integrity": integrity_report()}
    if not no_llm:
        try:
            out["spot_check"] = spot_check(run_id=run_id)
        except Exception as e:  # noqa: BLE001
            out["spot_check"] = {"error": str(e)[:160]}
    # 处置:文档级失败 → 清 kg_extracted_at 重排队 + meta 标记
    requeued = 0
    for v in (out.get("spot_check") or {}).get("verdicts", []):
        if not (v["company_link_ok"] and v["extraction_grounded"] and v["link_sensible"]):
            db.execute(
                "UPDATE documents SET kg_extracted_at=NULL, "
                "meta = jsonb_set(COALESCE(meta,'{}'), '{audit}', %s::jsonb) WHERE id=%s",
                (json.dumps(v, default=str), v["doc_id"]))
            requeued += 1
    out["requeued"] = requeued
    out["ts"] = kvstate.get_state("counters").get("last_cycle_at")
    kvstate.save_state("research_audit", out)
    log.info("research_audit: docs=%s requeued=%s",
             sum(d["n"] for d in out["integrity"]["by_doc_type"]), requeued)
    return out
