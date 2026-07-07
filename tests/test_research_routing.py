"""RD-P2 语义路由测试:研报/纪要事实流进 thesis 的最小接线。

验证:ALT_SOURCES 含 gangtise;研报 doc_type 走 _SYSTEM_RESEARCH 提示词 + 锚公司 fallback;
build_kg 队列把 gangtise 研报升到优先级 1;kept 洞见 → semantic_facts 可见(evidence_link 可拾取)。
"""
from __future__ import annotations

import pytest

from xar.kg import expert
from xar.ontology.research_docs import EXPERT_DOC_TYPES
from xar.storage import db


def test_gangtise_in_alt_sources():
    assert "gangtise" in expert.ALT_SOURCES
    assert "broker_report" in EXPERT_DOC_TYPES and "meeting_minutes" in EXPERT_DOC_TYPES


def test_build_kg_priority_case_has_gangtise():
    from xar.ontology.research_docs import kg_priority_case
    case = kg_priority_case()
    assert "broker_report' THEN 1" in case.replace(" ", " ")


_DOC = "gangtise:report:PYTESTROUTE:innolight"


@pytest.fixture()
def _doc(seeded_db):
    db.execute("DELETE FROM expert_insights WHERE doc_id=%s", (_DOC,))
    db.execute("DELETE FROM kg_events WHERE source_doc_id=%s", (_DOC,))
    db.execute("DELETE FROM documents WHERE id=%s", (_DOC,))
    db.execute(
        "INSERT INTO documents(id, company_id, source, doc_type, title, text, permission, "
        "published_at) VALUES(%s,'innolight','gangtise','broker_report','中际旭创深度',"
        "'1.6T 放量超预期,毛利率企稳','grey','2099-06-30')", (_DOC,))
    yield
    db.execute("DELETE FROM expert_insights WHERE doc_id=%s", (_DOC,))
    db.execute("DELETE FROM kg_events WHERE source_doc_id=%s", (_DOC,))
    db.execute("DELETE FROM documents WHERE id=%s", (_DOC,))


def test_research_doc_uses_research_prompt_and_anchor_fallback(_doc, monkeypatch):
    captured = {}

    def fake_complete_json(prompt, schema, *, system=None, **kw):
        captured["system"] = system
        # 实体故意留空 → 触发锚公司 fallback(company_id='innolight')
        return schema(relevant=True, entity="", stance="bull", catalyst_type="earnings",
                      thesis="1.6T 超预期", evidence="1.6T 放量超预期", signal_quality=0.8)

    monkeypatch.setattr(expert.llm, "complete_json", fake_complete_json)
    out = expert.process_document(_DOC)
    assert "curated" in (captured["system"] or "").lower()      # 用了研报变体
    assert out["kept"] == 1                                       # 锚 fallback 让它保留
    ins = db.query("SELECT company_id, kept FROM expert_insights WHERE doc_id=%s", (_DOC,))
    assert ins[0]["company_id"] == "innolight" and ins[0]["kept"]
    # kept 洞见 → semantic_facts 可见(evidence_link._pending_facts 的来源)
    sf = db.query("SELECT count(*) c FROM semantic_facts WHERE company_id='innolight' "
                  "AND kind='insight' AND source_doc_id=%s", (_DOC,))
    assert sf[0]["c"] >= 1


def test_research_doc_anchor_wins_over_llm_entity(_doc, monkeypatch):
    # 评审 #6:研报已按公司锚定 → 即使 LLM 把 entity 解析到另一家,也用文档锚公司(防错挂)
    def fake(prompt, schema, **kw):
        return schema(relevant=True, entity="Tesla 特斯拉", stance="bull",  # 故意解析到别家
                      catalyst_type="earnings", thesis="1.6T", evidence="1.6T 放量超预期",
                      signal_quality=0.8)
    monkeypatch.setattr(expert.llm, "complete_json", fake)
    expert.process_document(_DOC)
    ins = db.query("SELECT company_id FROM expert_insights WHERE doc_id=%s", (_DOC,))
    assert ins[0]["company_id"] == "innolight"      # 文档锚公司,不是 LLM 说的 Tesla
