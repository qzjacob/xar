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
                                       summary="GB300 ramp", evidence="record data center")],
            )
        # evidence-gate judge: a clean, low-risk verdict so a grounded report publishes
        if "risk" in getattr(schema, "model_fields", {}):
            return schema(risk=0.0, notes="ok")
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

    # graph query surfaces the extracted event (its evidence is grounded in the doc)
    evs = db.query("SELECT * FROM kg_events WHERE company_id='nvidia'")
    assert any(e["event_type"] == "product_ramp" for e in evs)


def test_gate_holds_low_confidence(mocked, monkeypatch):
    """The evidence gate is BINDING: a high hallucination-risk verdict holds the
    report for review even with auto_approve=True (review §1.1)."""
    from xar.agents import evidence_gate, run_report

    monkeypatch.setattr(evidence_gate, "_judge",
                        lambda state, body: {"risk": 0.9, "notes": "unsupported claims"})
    r = run_report({"kind": "takeaways", "company_id": "nvidia"}, auto_approve=True)
    assert r["status"] == "awaiting_approval"
    assert r["metrics"]["passed"] is False


def test_corroboration_idempotent_and_independent():
    """Seed edges must NOT drift on bootstrap re-runs (idempotency), and only an
    INDEPENDENT source corroborates (CODE_REVIEW appendix A.1.2)."""
    from xar.kg import store
    from xar.storage import db

    def conf():
        r = db.query("SELECT confidence FROM kg_edges WHERE src_id='a_t' AND dst_id='b_t'")
        return float(r[0]["confidence"]) if r else None

    db.execute("DELETE FROM kg_edges WHERE src_id='a_t' AND dst_id='b_t'")
    store.upsert_node("a_t", "Company", "A Test")  # kg_edges FKs kg_nodes + documents
    store.upsert_node("b_t", "Company", "B Test")
    for did in ("docA", "docB"):
        db.execute("INSERT INTO documents(id,source,doc_type,title,text) VALUES(%s,'test','t','t','t') "
                   "ON CONFLICT (id) DO NOTHING", (did,))
    store.add_edge("a_t", "b_t", "supplies", confidence=0.9, license_tag="seed")
    for _ in range(3):  # bootstrap_seed re-runs every startup
        store.add_edge("a_t", "b_t", "supplies", confidence=0.9, license_tag="seed")
    assert abs(conf() - 0.9) < 1e-9  # seed stays put — idempotent

    db.execute("DELETE FROM kg_edges WHERE src_id='a_t' AND dst_id='b_t'")
    store.add_edge("a_t", "b_t", "supplies", confidence=0.9, license_tag="extracted", source_doc_id="docA")
    store.add_edge("a_t", "b_t", "supplies", confidence=0.9, license_tag="extracted", source_doc_id="docA")
    assert abs(conf() - 0.9) < 1e-9  # same source -> no double count
    store.add_edge("a_t", "b_t", "supplies", confidence=0.9, license_tag="extracted", source_doc_id="docB")
    assert conf() > 0.9  # independent source -> boost
    db.execute("DELETE FROM kg_edges WHERE src_id='a_t' AND dst_id='b_t'")
    db.execute("DELETE FROM kg_nodes WHERE id IN ('a_t','b_t')")
    db.execute("DELETE FROM documents WHERE id IN ('docA','docB')")


def test_cycle_theme_overview_orders_by_cycle_rank():
    """A consumer cycle theme renders end-to-end: segments ordered by cycle rank
    (discount/QSR last), each carrying a serialized cycle profile; coverage marks
    the theme kind='cycle'; landscape works with no supply chain."""
    from xar.api import dashboard
    from xar.ingestion import seed_companies

    seed_companies()
    ov = dashboard.overview("retail")
    themes = {t["id"]: t for t in ov["coverage"]["themes"]}
    assert themes["retail"]["kind"] == "cycle"
    segs = ov["segments"]
    assert segs, "no retail segments rendered"
    tiers = [s["tier"] for s in segs]
    assert tiers == sorted(tiers)  # ordered along the cycle axis (early→counter)
    assert all(s.get("cycle") and s["cycle"].get("position") for s in segs)
    by_id = {s["id"]: s for s in segs}
    if "ret_discount" in by_id:  # the counter-cyclical end sits last (falls latest)
        assert by_id["ret_discount"]["tier"] == max(tiers)
        assert by_id["ret_discount"]["cycle"]["position"] == "counter_cyclical"
    # 行业格局/HHI is computed from segment membership, not chain edges
    assert dashboard.landscape("retail")["segments"]


def test_schema_idempotent_with_semantic_layer():
    """init_schema() re-runs cleanly on a populated DB and the additive semantic layer
    is present: new columns on kg_events/expert_insights, the semantic_facts view, and
    the ingest_runs table (the core idempotency guarantee for the daily system)."""
    from xar.storage import db

    db.init_schema()
    db.init_schema()  # second run must not error (ADD COLUMN IF NOT EXISTS / OR REPLACE)
    ev_cols = {r["column_name"] for r in db.query(
        "SELECT column_name FROM information_schema.columns WHERE table_name='kg_events'")}
    assert {"theme", "segment", "narrative", "time_orientation"} <= ev_cols
    ex_cols = {r["column_name"] for r in db.query(
        "SELECT column_name FROM information_schema.columns WHERE table_name='expert_insights'")}
    assert {"as_of", "theme", "segment", "time_orientation"} <= ex_cols
    assert db.query("SELECT to_regclass('semantic_facts') AS r")[0]["r"] == "semantic_facts"
    assert db.query("SELECT to_regclass('ingest_runs') AS r")[0]["r"] == "ingest_runs"
    db.query("SELECT * FROM semantic_facts LIMIT 1")  # view is queryable


def test_add_event_writes_semantic_columns_and_still_dedups():
    """add_event persists the semantic columns + attrs.drivers, anchors theme/segment
    from the company, and the dedup_key (unchanged) still collapses a re-assertion."""
    from xar.ingestion import seed_companies
    from xar.kg import store
    from xar.storage import db

    seed_companies()
    db.execute("DELETE FROM kg_events WHERE company_id='nvidia' AND event_type='order' "
               "AND summary='sem-col test'")
    ok = store.add_event("nvidia", "nvidia", "order", event_date="2025-09-01",
                         summary="sem-col test", narrative="AI capex drives orders",
                         time_orientation="forward_looking", drivers=["AI capex", "NVIDIA"])
    assert ok
    again = store.add_event("nvidia", "nvidia", "order", event_date="2025-09-01",
                            summary="sem-col test", narrative="x")
    assert again is False  # dedup_key unchanged -> second insert collapses
    row = db.query("SELECT theme, segment, narrative, time_orientation, attrs FROM kg_events "
                   "WHERE company_id='nvidia' AND summary='sem-col test'")[0]
    assert row["theme"] and row["segment"]  # anchored from the registry
    assert row["narrative"] == "AI capex drives orders"
    assert row["time_orientation"] == "forward_looking"
    assert row["attrs"]["drivers"] == ["AI capex", "NVIDIA"]
    db.execute("DELETE FROM kg_events WHERE company_id='nvidia' AND summary='sem-col test'")


def test_runlog_last_success_ts_cursor():
    """last_success_ts returns the most recent SUCCESSFUL finish for a source — the
    incremental pull cursor — ignoring running/failed rows."""
    from xar.storage import db, runlog

    db.execute("DELETE FROM ingest_runs WHERE kind='unit_src'")
    r1 = runlog.start("unit_src")
    runlog.finish(r1, "ok", stats={"pulled": 3})
    r2 = runlog.start("unit_src")
    runlog.finish(r2, "failed", error="boom")  # failed must not advance the cursor
    cur = runlog.last_success_ts("unit_src")
    assert cur is not None
    ok_ts = db.query("SELECT finished_at FROM ingest_runs WHERE id=%s", (r1,))[0]["finished_at"]
    assert cur == ok_ts
    assert runlog.last_success_ts("never_run_src") is None
    db.execute("DELETE FROM ingest_runs WHERE kind='unit_src'")


def test_semantic_facts_unifies_events_and_insights():
    """The semantic_facts view + graphrag.semantic() return BOTH a catalyst event and a
    kept expert insight for the same company — the unified point-queryable stream."""
    from xar.ingestion import seed_companies
    from xar.kg import store
    from xar.retrieval import graphrag
    from xar.storage import db

    seed_companies()
    store.bootstrap_seed()
    db.execute("DELETE FROM kg_events WHERE company_id='nvidia' AND summary='sf-event'")
    db.execute("DELETE FROM expert_insights WHERE doc_id='sf-doc'")
    db.execute("INSERT INTO documents(id,company_id,source,doc_type,title,text,published_at) "
               "VALUES('sf-doc','nvidia','finnhub','news','t','t','2025-09-02') "
               "ON CONFLICT (id) DO NOTHING")
    store.add_event("nvidia", "nvidia", "order", event_date="2025-09-01", summary="sf-event",
                    time_orientation="forward_looking")
    db.execute("INSERT INTO expert_insights(doc_id,source,company_id,catalyst_type,polarity,"
               "thesis,signal_quality,kept,as_of,time_orientation) "
               "VALUES('sf-doc','finnhub','nvidia','earnings','positive','sf-insight',0.8,TRUE,"
               "'2025-09-02','backward_looking') ON CONFLICT (doc_id) DO UPDATE SET kept=TRUE")

    facts = graphrag.semantic("nvidia", limit=200)
    kinds = {f["kind"] for f in facts}
    assert "event" in kinds and "insight" in kinds
    contents = {f["content"] for f in facts}
    assert "sf-event" in contents and "sf-insight" in contents
    # point-query upper bound excludes facts dated after the as_of
    early = graphrag.semantic("nvidia", as_of="2025-09-01", limit=200)
    early_contents = {f["content"] for f in early}
    assert "sf-event" in early_contents and "sf-insight" not in early_contents
    db.execute("DELETE FROM kg_events WHERE company_id='nvidia' AND summary='sf-event'")
    db.execute("DELETE FROM expert_insights WHERE doc_id='sf-doc'")
    db.execute("DELETE FROM documents WHERE id='sf-doc'")


def test_backtest_reads_semantic_facts():
    """backtest() drives off semantic_facts and groups by (category,polarity,kind,
    time_orientation) without error (no price data needed to exercise the query path)."""
    from xar.backtest import backtest

    out = backtest(horizons=(5,), limit=50)
    assert "by_signal" in out and "events_used" in out and "disclaimer" in out


def test_ungrounded_extraction_dropped(mocked, monkeypatch):
    """An edge/event whose evidence quote is NOT in the source document is dropped
    rather than written to the KG (review §1.2)."""
    from xar.kg import extract

    text = "Acme announced a strategic partnership with Beta Corp this quarter."
    # evidence that does not appear in `text` -> must be dropped
    assert extract._grounded("totally fabricated unrelated claim", text) is False
    assert extract._grounded("strategic partnership with Beta", text) is True
