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


def test_resolve_forward_claims():
    """The forward-claim loop closes correctly: a forward_looking catalyst becomes 'hit'
    when a later same-company realized event arrives in-window with consistent polarity,
    'miss' on opposite polarity, and 'stale' when the window lapses with no realizer.
    Backward (hard-fact) rows are never given a resolution."""
    from xar.kg import store
    from xar.kg.resolve_claims import resolve_forward_claims
    from xar.storage import db

    db.init_schema()
    cids = ("rc_hit", "rc_miss", "rc_stale")
    for cid in cids:
        db.execute("DELETE FROM kg_events WHERE company_id=%s", (cid,))
        db.execute("DELETE FROM kg_nodes WHERE id=%s", (cid,))
        store.upsert_node(cid, "Company", cid)
    # hit: forward(+) then realized(+) in-window
    store.add_event("rc_hit", "rc_hit", "guidance_change", event_date="2024-01-01",
                    polarity="positive", summary="fwd", time_orientation="forward_looking")
    store.add_event("rc_hit", "rc_hit", "earnings", event_date="2024-02-15",
                    polarity="positive", summary="real", time_orientation="backward_looking")
    # miss: forward(+) then realized(-)
    store.add_event("rc_miss", "rc_miss", "guidance_change", event_date="2024-01-01",
                    polarity="positive", summary="fwd", time_orientation="forward_looking")
    store.add_event("rc_miss", "rc_miss", "earnings", event_date="2024-02-15",
                    polarity="negative", summary="real", time_orientation="backward_looking")
    # stale: forward only, window long lapsed
    store.add_event("rc_stale", "rc_stale", "guidance_change", event_date="2024-01-01",
                    polarity="positive", summary="fwd", time_orientation="forward_looking")

    resolve_forward_claims(window_days=120, grace_days=21)

    def fwd(cid):
        return db.query("SELECT resolution, realizes_event_id FROM kg_events "
                        "WHERE company_id=%s AND time_orientation='forward_looking'", (cid,))[0]
    assert fwd("rc_hit")["resolution"] == "hit" and fwd("rc_hit")["realizes_event_id"] is not None
    assert fwd("rc_miss")["resolution"] == "miss"
    assert fwd("rc_stale")["resolution"] == "stale"
    # the realized backward rows must stay unresolved (log immutable where it matters)
    assert db.query("SELECT resolution FROM kg_events WHERE company_id='rc_hit' "
                    "AND time_orientation='backward_looking'")[0]["resolution"] is None
    for cid in cids:
        db.execute("DELETE FROM kg_events WHERE company_id=%s", (cid,))
        db.execute("DELETE FROM kg_nodes WHERE id=%s", (cid,))


def test_resolve_expert_forward_claim_visible_via_view():
    """The production population: an expert-licensed forward claim (event_date NULL, dated by
    observed_at, license_tag='expert' so it is filtered out of the view's event arm) resolves
    against a later realization-type event, and the resolution is READABLE through
    semantic_facts — the insight arm joins the mirror on kg_event_id. Guards the P0 view-
    invisibility and the NULL-event_date realizer-match defects together."""
    from xar.kg import store
    from xar.kg.resolve_claims import resolve_forward_claims
    from xar.storage import db

    db.init_schema()
    db.execute("DELETE FROM kg_events WHERE company_id='rc_exp'")
    db.execute("DELETE FROM expert_insights WHERE doc_id='rc_exp_doc'")
    db.execute("DELETE FROM documents WHERE id='rc_exp_doc'")
    db.execute("DELETE FROM kg_nodes WHERE id='rc_exp'")
    store.upsert_node("rc_exp", "Company", "rc_exp")
    # expert forward-claim mirror: event_date NULL, observed_at backdated, license 'expert'
    db.execute("""INSERT INTO kg_events(company_id,node_id,event_type,polarity,summary,
                    time_orientation,license_tag,observed_at,dedup_key)
                  VALUES('rc_exp','rc_exp','guidance_change','positive','exp fwd',
                    'forward_looking','expert','2024-01-01','rc_exp_k1')""")
    claim_id = db.query("SELECT id FROM kg_events WHERE dedup_key='rc_exp_k1'")[0]["id"]
    # later realized earnings (backward, positive, realization-type), in-window via observed_at
    db.execute("""INSERT INTO kg_events(company_id,node_id,event_type,polarity,summary,
                    time_orientation,license_tag,observed_at,dedup_key)
                  VALUES('rc_exp','rc_exp','earnings','positive','exp real',
                    'backward_looking','extracted','2024-02-15','rc_exp_k2')""")
    # the kept expert_insights row pointing at the mirror, so the insight arm surfaces it
    db.execute("INSERT INTO documents(id,source,doc_type,title,text) VALUES('rc_exp_doc','x','t','t','t') "
               "ON CONFLICT (id) DO NOTHING")
    db.execute("""INSERT INTO expert_insights(doc_id,source,company_id,stance,polarity,catalyst_type,
                    thesis,kept,kg_event_id,time_orientation)
                  VALUES('rc_exp_doc','x','rc_exp','bull','positive','guidance_change','exp fwd',
                    true,%s,'forward_looking')""", (claim_id,))

    resolve_forward_claims(window_days=120, grace_days=21)

    assert db.query("SELECT resolution FROM kg_events WHERE id=%s", (claim_id,))[0]["resolution"] == "hit"
    sf = db.query("SELECT resolution FROM semantic_facts WHERE company_id='rc_exp' AND kind='insight'")
    assert sf and sf[0]["resolution"] == "hit"   # P0 fix: readable through the canonical surface

    db.execute("DELETE FROM kg_events WHERE company_id='rc_exp'")
    db.execute("DELETE FROM expert_insights WHERE doc_id='rc_exp_doc'")
    db.execute("DELETE FROM documents WHERE id='rc_exp_doc'")
    db.execute("DELETE FROM kg_nodes WHERE id='rc_exp'")


def test_ungrounded_extraction_dropped(mocked, monkeypatch):
    """An edge/event whose evidence quote is NOT in the source document is dropped
    rather than written to the KG (review §1.2)."""
    from xar.kg import extract

    text = "Acme announced a strategic partnership with Beta Corp this quarter."
    # evidence that does not appear in `text` -> must be dropped
    assert extract._grounded("totally fabricated unrelated claim", text) is False
    assert extract._grounded("strategic partnership with Beta", text) is True


class _FakeResp:
    def __init__(self, text="OK", in_tok=5, out_tok=7):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]
        self.usage = type("U", (), {"prompt_tokens": in_tok, "completion_tokens": out_tok})()


def test_llm_fallback_rotates_to_next_provider(monkeypatch):
    """A failing first candidate (incl. its in-candidate retry) rotates to the next
    provider in the chain rather than failing the whole call."""
    import litellm
    import litellm.exceptions as le

    from xar.models import llm
    # configure BOTH the first candidate (glm) and a rotation target (deepseek) so the test
    # doesn't depend on the ambient .env leaking a key for the rotation target.
    monkeypatch.setenv("GLM_API_KEY", "test-glm")
    monkeypatch.setenv("GLM_SUB_API_KEY", "test-glm-sub")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek")
    seen: list = []

    def fake(**kw):
        seen.append(kw["model"])
        if kw["model"] == "openai/glm-4.6":     # first KG_EXTRACT candidate always fails
            raise le.RateLimitError("rate", "zhipu", "glm-4.6")
        return _FakeResp("ROTATED")

    monkeypatch.setattr(litellm, "completion", fake)
    out = llm.complete("hi", task="kg_extract", node="t", run_id=None, max_tokens=50)
    assert out == "ROTATED"
    assert "openai/glm-4.6" in seen and any(m != "openai/glm-4.6" for m in seen)


def test_llm_subscription_rides_over_budget(monkeypatch):
    """A subscription candidate serves bulk even with the token budget exhausted — never
    trips BudgetExceeded — and records usd=0 with provider/task_class/billing columns."""
    import litellm

    from xar.models import llm
    from xar.storage import db
    db.init_schema()
    monkeypatch.setenv("GLM_API_KEY", "test-glm")
    monkeypatch.setenv("GLM_SUB_API_KEY", "test-glm-sub")             # genuine flat plan → usd=0
    monkeypatch.setattr(llm, "_spent", lambda rid: 9_999.0)            # token budget blown
    monkeypatch.setattr(litellm, "completion", lambda **kw: _FakeResp("SUB"))
    rid = "batch-routetest"
    db.execute("DELETE FROM llm_usage WHERE run_id=%s", (rid,))
    out = llm.complete("hi", task="kg_extract", node="t", run_id=rid, max_tokens=50)
    assert out == "SUB"                                                # glm-4.6-sub served
    row = db.query("SELECT provider, task_class, billing, usd FROM llm_usage WHERE run_id=%s", (rid,))[0]
    assert row["billing"] == "subscription" and float(row["usd"]) == 0.0
    assert row["task_class"] == "kg_extract" and row["provider"] == "zhipu"
    db.execute("DELETE FROM llm_usage WHERE run_id=%s", (rid,))


def test_llm_subscription_without_sub_key_bills_as_token(monkeypatch):
    """The billing hole guard: a SUBSCRIPTION spec with NO sub key configured falls back to
    the provider's metered key, so it must record billing='token' with REAL usd (not 0) — the
    metered spend stays visible to the budget cap instead of being silently free."""
    import litellm

    from xar.models import llm
    from xar.storage import db
    db.init_schema()
    monkeypatch.setenv("GLM_API_KEY", "test-glm")          # token key only; NO GLM_SUB_API_KEY
    monkeypatch.delenv("GLM_SUB_API_KEY", raising=False)
    monkeypatch.setattr(llm, "_spent", lambda rid: 0.0)
    monkeypatch.setattr(litellm, "completion", lambda **kw: _FakeResp("TOK", in_tok=1_000_000, out_tok=0))
    rid = "batch-billtest"
    db.execute("DELETE FROM llm_usage WHERE run_id=%s", (rid,))
    llm.complete("hi", task="kg_extract", node="t", run_id=rid, max_tokens=50)
    row = db.query("SELECT billing, usd FROM llm_usage WHERE run_id=%s", (rid,))[0]
    assert row["billing"] == "token" and float(row["usd"]) > 0.0   # metered, not free
    db.execute("DELETE FROM llm_usage WHERE run_id=%s", (rid,))


def test_llm_token_only_chain_hardstops_on_budget(monkeypatch):
    """No subscription candidate + exhausted token budget → BudgetExceeded (preserves the
    daily.py catch)."""
    import pytest

    from xar.models import llm, registry, router
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek")   # candidate configured → skip is budget, not key
    monkeypatch.setattr(llm, "_spent", lambda rid: 9_999.0)
    monkeypatch.setattr(router, "resolve", lambda tc: [registry.get("deepseek-v4-flash")])  # token-only
    with pytest.raises(llm.BudgetExceeded):
        llm.complete("hi", task="kg_extract", node="t", run_id="batch-x", max_tokens=50)


def test_route_override_persists_and_reroutes():
    """ops.set_route persists a route_overrides row and re-routes resolve() live; clearing
    reverts. Exercises the additive schema (columns + route_overrides) end to end."""
    from xar.api import ops
    from xar.models import router
    from xar.models.router import TaskClass
    from xar.storage import db
    db.init_schema()
    db.execute("DELETE FROM route_overrides WHERE key='cheap_bulk'")
    router.registry.refresh_overrides()
    try:
        assert ops.set_route("cheap_bulk", "kimi-k2-sub")["ok"] is True
        assert router.resolve(TaskClass.KG_EXTRACT)[0].id == "kimi-k2-sub"
        assert ops.set_route("cheap_bulk", "")["cleared"] is True
        assert router.resolve(TaskClass.KG_EXTRACT)[0].id == "glm-4.6-sub"
        assert ops.set_route("cheap_bulk", "nonexistent")["ok"] is False
    finally:  # never leak a live override into the shared DB, even on assertion failure
        db.execute("DELETE FROM route_overrides WHERE key='cheap_bulk'")
        router.registry.refresh_overrides()
