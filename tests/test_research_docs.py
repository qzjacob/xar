"""非标投研文档本体 + Doc.doc_id 的离线一致性测试(代码即真相守卫)。

不碰 DB、不联网:注册表完整性不变式(doc_type 唯一、催化/支柱词表合法、kg_priority_case
覆盖全类型)+ Doc.doc_id 覆写正确且**未设置时哈希逐字节不变**(锁旧行为,防存量 id 漂移)。
"""
from __future__ import annotations

import hashlib

from xar.ingestion.base import Doc
from xar.ontology.catalysts import CATALYST_TYPES
from xar.ontology.research_docs import (
    DOCS_BY_TYPE,
    EXPERT_DOC_TYPES,
    RATED_DOC_TYPES,
    RESEARCH_DOCS,
    kg_priority_case,
)
from xar.ontology.thesis import PILLAR_KINDS


def test_doc_types_unique_and_vocab_legal():
    keys = [s.doc_type for s in RESEARCH_DOCS]
    assert len(keys) == len(set(keys)), "duplicate doc_type"
    for s in RESEARCH_DOCS:
        assert set(s.catalyst_types) <= set(CATALYST_TYPES), f"{s.doc_type}: catalyst"
        assert set(s.pillar_kinds) <= set(PILLAR_KINDS), f"{s.doc_type}: pillar"
        assert s.extraction in ("expert", "kg_only", "none")
        assert s.body in ("brief", "full_core")


def test_derived_sets():
    assert "broker_report" in DOCS_BY_TYPE
    assert EXPERT_DOC_TYPES <= set(DOCS_BY_TYPE)
    assert RATED_DOC_TYPES == frozenset({"broker_report"})
    assert "broker_report" in EXPERT_DOC_TYPES        # 研报也走 expert 语义道


def test_kg_priority_case_covers_all():
    sql = kg_priority_case()
    for s in RESEARCH_DOCS:
        assert f"WHEN '{s.doc_type}' THEN {s.kg_priority}" in sql
    assert sql.endswith("ELSE 3 END")


def test_doc_id_override():
    d = Doc(company_id="now", source="gangtise", doc_type="broker_report",
            title="t", text="body", doc_id="gangtise:report:12345")
    assert d.id == "gangtise:report:12345"


def test_doc_id_absent_hash_unchanged():
    # 未设 doc_id 时必须与旧公式逐字节相同:sha256((url+title+text[:200]))[:20],前缀 source:
    d = Doc(company_id=None, source="news", doc_type="article",
            title="Hello", text="X" * 500, url="http://e/x")
    basis = "http://e/x" + "Hello" + ("X" * 200)
    expected = f"news:{hashlib.sha256(basis.encode()).hexdigest()[:20]}"
    assert d.id == expected
