"""独立审计智能体测试(seeded_db + monkeypatch complete_json)。

验证:integrity 计数确定性;失败裁决触发 kg_extracted_at 清空 + meta.audit 标记;
router AUDIT 首选 token 强模型(硬锁独立性:审计模型 ≠ 生产 GLM)。
"""
from __future__ import annotations

import pytest

from xar.models.router import TaskClass, resolve
from xar.orchestration import research_audit
from xar.storage import db

_DOC = "gangtise:report:PYTESTAUDIT:innolight"


def test_router_audit_is_independent_token_model():
    specs = resolve(TaskClass.AUDIT)
    assert specs, "no models for AUDIT"
    assert specs[0].billing == "token"                     # 强 token 模型,非 GLM 订阅池
    assert specs[0].billing != "subscription"


@pytest.fixture()
def _doc(seeded_db):
    def wipe():
        db.execute("DELETE FROM documents WHERE id=%s", (_DOC,))
    wipe()
    db.execute(
        "INSERT INTO documents(id, company_id, source, doc_type, title, text, permission, "
        "kg_extracted_at, ingested_at) VALUES(%s,'innolight','gangtise','broker_report','t',"
        "'1.6T 放量','grey', now(), now())", (_DOC,))
    yield
    wipe()


def test_integrity_report_counts(_doc):
    rep = research_audit.integrity_report()
    br = next((d for d in rep["by_doc_type"] if d["doc_type"] == "broker_report"), None)
    assert br is not None and br["n"] >= 1
    assert br["link_rate"] is not None
    assert "edb" in rep and "expert" in rep


def test_failed_verdict_requeues_doc(_doc, monkeypatch):
    def fake(prompt, schema, **kw):
        return schema(company_link_ok=False, doc_type_ok=True, extraction_grounded=False,
                      link_sensible=True, severity="high", notes_zh="公司链接可疑")
    monkeypatch.setattr(research_audit.llm, "complete_json", fake)
    out = research_audit.run_audit()
    assert out["requeued"] >= 1
    r = db.query("SELECT kg_extracted_at, meta FROM documents WHERE id=%s", (_DOC,))
    assert r[0]["kg_extracted_at"] is None                 # 已重排队
    assert (r[0]["meta"] or {}).get("audit")               # meta 打了审计标记
