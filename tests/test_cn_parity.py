"""Workstream D: cninfo research-rating parser (offline regex, no network) and
KG orphan repair (fixture rows against a local Postgres; skipped without one)."""
from __future__ import annotations

import pytest

from xar.ingestion.cninfo import extract_rating_fields


def _db_ok() -> bool:
    try:
        from xar.storage import db

        db.init_schema()
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_ok(), reason="no Postgres available")


# --- title/meta regex parser (pure, offline) ---------------------------------
def test_rating_from_title_with_target_price():
    f = extract_rating_fields('中际旭创(300308):800G放量在即,维持"买入"评级,目标价165.5元', {})
    assert f["rating"] == "buy"
    assert f["target_price"] == 165.5


def test_specific_rating_words_win_over_generic():
    assert extract_rating_fields("首次覆盖给予强烈推荐评级", {})["rating"] == "strong_buy"
    assert extract_rating_fields("下调至谨慎增持", {})["rating"] == "buy"
    assert extract_rating_fields("维持增持评级", {})["rating"] == "buy"
    assert extract_rating_fields("评级下调至中性", {})["rating"] == "hold"
    assert extract_rating_fields("光模块景气回落,下调至减持", {})["rating"] == "sell"
    assert extract_rating_fields("估值过高,卖出", {})["rating"] == "strong_sell"


def test_target_price_range_takes_midpoint_and_commas():
    assert extract_rating_fields("上调目标价55-60元,维持买入", {})["target_price"] == 57.5
    assert extract_rating_fields("推荐,目标价上调至1,200元", {})["target_price"] == 1200.0


def test_meta_columns_win_and_org_date_extracted():
    meta = {"东财评级": "增持", "机构": "中信建投", "日期": "2025-06-12", "报告名称": "深度报告"}
    f = extract_rating_fields("深度报告", meta)
    assert f["rating"] == "buy"
    assert f["org"] == "中信建投"
    assert str(f["date"]) == "2025-06-12"


def test_org_from_title_brackets():
    f = extract_rating_fields("【华泰证券】光模块龙头地位稳固,推荐", {})
    assert f["org"] == "华泰证券"
    assert f["rating"] == "buy"


def test_no_rating_signal_returns_none():
    assert extract_rating_fields("2025年半年度报告", {}) is None
    assert extract_rating_fields("", {"机构": "nan", "日期": "2025-06-12"}) is None


# --- DB-backed: parser -> analyst_ratings + orphan repair --------------------
@requires_db
def test_parse_research_ratings_aggregates_and_is_idempotent(seeded_db):
    from xar.ingestion.cninfo import parse_research_ratings
    from xar.storage import db

    doc_ids = ("research_meta:cnp_t1", "research_meta:cnp_t2", "research_meta:cnp_t3")

    def clean():
        db.execute("DELETE FROM analyst_ratings WHERE company_id='nvidia' AND source='cninfo' "
                   "AND as_of='2025-06-12'")
        for did in doc_ids:
            db.execute("DELETE FROM documents WHERE id=%s", (did,))

    clean()
    db.execute(
        "INSERT INTO documents(id,company_id,source,doc_type,title,text,meta) VALUES "
        "(%s,'nvidia','research_meta','research_report_meta',%s,'',%s)",
        (doc_ids[0], "英伟达:AI需求强劲,维持买入评级,目标价165元",
         '{"机构":"华泰证券","日期":"2025-06-12"}'))
    db.execute(
        "INSERT INTO documents(id,company_id,source,doc_type,title,text,meta) VALUES "
        "(%s,'nvidia','research_meta','research_report_meta',%s,'',%s)",
        (doc_ids[1], "英伟达深度:上调至强烈推荐",
         '{"机构":"中信建投","日期":"2025-06-12"}'))
    db.execute(  # no rating / no target -> must be skipped, not written
        "INSERT INTO documents(id,company_id,source,doc_type,title,text,meta) VALUES "
        "(%s,'nvidia','research_meta','research_report_meta',%s,'',%s)",
        (doc_ids[2], "英伟达:2025年报点评", '{"日期":"2025-06-12"}'))

    rep = parse_research_ratings("nvidia")
    assert rep["parsed"] >= 2 and rep["skipped"] >= 1

    def rows():
        return db.query(
            "SELECT * FROM analyst_ratings WHERE company_id='nvidia' AND source='cninfo' "
            "AND as_of='2025-06-12'")

    r = rows()
    assert len(r) == 1  # one aggregated (company, day, source) row
    assert r[0]["buy"] == 1 and r[0]["strong_buy"] == 1
    assert r[0]["pt_mean"] == 165.0 and r[0]["pt_high"] == 165.0
    assert set(r[0]["meta"]["orgs"]) == {"华泰证券", "中信建投"}

    parse_research_ratings("nvidia")  # re-run: upsert key collapses, no duplicate
    assert len(rows()) == 1
    clean()


@requires_db
def test_repair_orphan_events_repairs_tags_and_anchors(seeded_db):
    from xar.ingestion.registry import company_by_id
    from xar.kg import resolve
    from xar.kg.repair import _company_lookup, repair_orphan_events
    from xar.storage import db

    good, bad = "ent_testrepair01", "ent_testrepair02"
    keys = ("test_repair_k1", "test_repair_k2", "test_repair_k3")

    def clean():
        for k in keys:
            db.execute("DELETE FROM kg_events WHERE dedup_key=%s", (k,))
        for oid in (good, bad):
            db.execute("DELETE FROM entity_aliases WHERE node_id=%s", (oid,))
            db.execute("DELETE FROM kg_nodes WHERE id=%s", (oid,))

    clean()
    name = db.query("SELECT name FROM companies WHERE id='nvidia'")[0]["name"]
    # orphan whose node name normalizes onto a watched company (suffix stripped)
    db.execute("INSERT INTO kg_nodes(id,node_type,name) VALUES(%s,'DownstreamCustomer',%s)",
               (good, name + " Corp"))
    # orphan nothing in the registry can confidently match
    db.execute("INSERT INTO kg_nodes(id,node_type,name) VALUES(%s,'ModuleMaker',%s)",
               (bad, "Zqxv Unmatchable Widgets"))
    db.execute("INSERT INTO kg_events(company_id,node_id,event_type,summary,dedup_key) "
               "VALUES(%s,%s,'order','repair test A',%s)", (good, good, keys[0]))
    db.execute("INSERT INTO kg_events(company_id,node_id,event_type,summary,dedup_key) "
               "VALUES(%s,%s,'order','repair test B',%s)", (bad, bad, keys[1]))
    # watched-company event missing its ontology anchor (raw insert bypasses add_event)
    db.execute("INSERT INTO kg_events(company_id,event_type,summary,dedup_key) "
               "VALUES('nvidia','order','repair test C',%s)", (keys[2],))

    expected = _company_lookup()[resolve.normalize(name)]
    rep = repair_orphan_events(verbose=False)
    assert rep["repaired_ids"] >= 1

    a = db.query("SELECT company_id, attrs FROM kg_events WHERE dedup_key=%s", (keys[0],))[0]
    assert a["company_id"] == expected  # confidently re-pointed to a watched company
    assert db.query("SELECT 1 FROM companies WHERE id=%s", (a["company_id"],))
    b = db.query("SELECT company_id, attrs FROM kg_events WHERE dedup_key=%s", (keys[1],))[0]
    assert b["company_id"] == bad  # no confident match: left in place …
    assert b["attrs"].get("orphan") is True  # … and tagged for a human
    c = db.query("SELECT theme FROM kg_events WHERE dedup_key=%s", (keys[2],))[0]
    assert c["theme"] == company_by_id("nvidia")["themes"][0]  # anchor backfilled

    rep2 = repair_orphan_events(verbose=False)  # idempotent second pass
    a2 = db.query("SELECT company_id FROM kg_events WHERE dedup_key=%s", (keys[0],))[0]
    assert a2["company_id"] == expected
    b2 = db.query("SELECT company_id, attrs FROM kg_events WHERE dedup_key=%s", (keys[1],))[0]
    assert b2["company_id"] == bad and b2["attrs"].get("orphan") is True
    assert rep2["repaired_ids"] == 0  # nothing left to rebind
    clean()
