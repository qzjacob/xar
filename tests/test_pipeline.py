"""DB-gated, LLM-mocked end-to-end verification: ingest a synthetic document ->
parse+embed -> extract KG -> generate a report through the full agent pipeline.

Skips if no Postgres is reachable. Needs NO API key (LLM + embeddings mocked)."""
from __future__ import annotations

import pytest

from xar.config import get_settings


def _db_ok() -> bool:
    try:
        from xar.storage import db

        db.init_schema()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_ok(), reason="no Postgres available")


@pytest.fixture
def mocked(monkeypatch):
    dim = get_settings().embed_dim
    from xar.models import embeddings, llm
    from xar.ontology import ExtractedEvent, ExtractedNode, ExtractionResult

    def fake_docs(texts):  # deterministic non-zero vectors
        return [[float((i + 1) % 7) / 7.0] * dim for i, _ in enumerate(texts)]

    monkeypatch.setattr(embeddings, "embed_documents", fake_docs)
    monkeypatch.setattr(embeddings, "embed_query", lambda t: [0.14] * dim)
    # patch the names used inside vector.py / parse.py
    monkeypatch.setattr("xar.retrieval.vector.embeddings.embed_query", lambda t: [0.14] * dim)
    monkeypatch.setattr("xar.parsing.parse.embeddings.embed_documents", fake_docs)

    def fake_complete(prompt, **kw):
        return "Finding: NVIDIA data-center revenue is growing strongly [1]. 1.6T ramp underway [1]."

    def fake_complete_json(prompt, schema, **kw):
        if schema is ExtractionResult:
            return ExtractionResult(
                nodes=[ExtractedNode(name="NVIDIA", node_type="DownstreamCustomer")],
                edges=[],
                events=[ExtractedEvent(company="NVIDIA", event_type="product_ramp",
                                       event_date="2025-09-01", polarity="positive",
                                       summary="GB300 ramp", evidence="ramp")],
            )
        return schema()

    monkeypatch.setattr(llm, "complete", fake_complete)
    monkeypatch.setattr(llm, "complete_json", fake_complete_json)
    return True


def test_end_to_end(mocked):
    from xar.agents import run_report
    from xar.ingestion import seed_companies
    from xar.ingestion.base import Doc, save
    from xar.kg import extract, store
    from xar.parsing import parse
    from xar.storage import db

    # self-clean prior artifacts so reruns are deterministic
    db.execute("DELETE FROM kg_events WHERE company_id='nvidia' AND license_tag='extracted'")
    db.execute("DELETE FROM documents WHERE title='NVIDIA 8-K test'")

    seed_companies()
    store.bootstrap_seed()

    doc = Doc(company_id="nvidia", source="edgar", doc_type="8-K",
              title="NVIDIA 8-K test",
              text="NVIDIA reported record data center revenue. " * 40 +
                   "\n\nSegment A 100\nSegment B 50\nTotal 150\n")
    doc_id = save(doc)

    assert parse.parse_document(doc_id) > 0
    totals = extract.build_kg(limit=5)
    assert totals["docs"] >= 1

    # the agent pipeline (LLM mocked) must produce a grounded, gated report
    r = run_report({"kind": "takeaways", "company_id": "nvidia"}, auto_approve=True)
    assert r["status"] == "published"
    assert "NVIDIA" in r["content_md"]
    assert r["metrics"]["citation_count"] >= 1
    assert "Sources" in r["content_md"] or "引用来源" in r["content_md"]

    # graph query surfaces the extracted event
    evs = db.query("SELECT * FROM kg_events WHERE company_id='nvidia'")
    assert any(e["event_type"] == "product_ramp" for e in evs)
