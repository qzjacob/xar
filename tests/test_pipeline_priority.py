"""Ingest-pipeline 优先流排序测试。

验证 pipeline_priority.priority_order_sql 语义 + build_kg 把优先流(aifinmarket)
排在存量之前。build_kg 排序测试用 isolated_db 事务回滚:事务内先把既有 pending 全盖戳、
再插两篇(优先流 + 存量),断言优先流先被抽取,teardown 整体 rollback、绝不落库。
"""
from __future__ import annotations

from xar import pipeline_priority as pp


def test_priority_order_sql_and_membership():
    assert "aifinmarket" in pp.PRIORITY_SOURCES and "alphapai" in pp.PRIORITY_SOURCES
    lit = ", ".join("'" + s + "'" for s in pp.PRIORITY_SOURCES)
    assert pp.priority_order_sql("d.source") == f"(d.source IN ({lit}))"
    assert pp.priority_order_sql("source") == f"(source IN ({lit}))"


def test_build_kg_prioritizes_aifinmarket(isolated_db, monkeypatch):
    from xar.kg import extract
    from xar.storage import db

    # 事务内:盖戳所有既有 pending → 只留本测试两篇,顺序可判定(rollback 复原)。
    db.execute("UPDATE documents SET kg_extracted_at=now() WHERE kg_extracted_at IS NULL")
    db.execute(
        "INSERT INTO documents(id, source, doc_type, title, text, permission) VALUES "
        "('t-bulk','edgar','10-K','bulk','xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx','green'),"
        "('t-aifin','aifinmarket','news','aifin','xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx','grey')")

    seen: list[str] = []
    monkeypatch.setattr(extract, "extract_from_document",
                        lambda doc_id, run_id=None: seen.append(doc_id) or None)
    extract.build_kg(limit=10)

    assert "t-aifin" in seen and "t-bulk" in seen
    assert seen.index("t-aifin") < seen.index("t-bulk")   # 优先流排在存量 10-K 之前
