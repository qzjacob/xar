"""Pure unit tests — no DB, no network, no API key."""
from xar.parsing import tie_out
from xar.parsing.parse import chunk_text


def test_tie_out_reconciles():
    ok, _ = tie_out.check("Revenue 100\nCosts 60\nProfit 40\nTotal 100")
    assert ok


def test_tie_out_flags_bad_total():
    ok, reason = tie_out.check("Segment A 100\nSegment B 50\nTotal 999")
    assert ok is False
    assert "!=" in reason


def test_tie_out_passes_prose():
    ok, _ = tie_out.check("The company expects strong demand for 1.6T modules.")
    assert ok


def test_chunking():
    chunks = chunk_text("para one.\n\n" + ("x" * 5000))
    assert len(chunks) >= 2
    assert all(len(c) <= 1700 for c in chunks)


def test_ontology_taxonomy_extends_without_breaking_legacy():
    from xar.ontology import CATALYST_TYPES, EDGE_TYPES, NODE_TYPES

    # legacy values preserved verbatim (seed data / graphrag / dashboards depend on them)
    for v in ("ModuleMaker", "UpstreamComponent", "DownstreamCustomer", "TechRoute"):
        assert v in NODE_TYPES
    for v in ("supplies", "single_source_risk", "invests_in", "uses_techroute"):
        assert v in EDGE_TYPES
    for v in ("capex_guidance", "order", "earnings", "tech_substitution"):
        assert v in CATALYST_TYPES
    # generalized additions present
    assert {"Company", "Product", "EndMarket", "Geography"} <= set(NODE_TYPES)
    assert {"customer_of", "competes_in", "partners_with", "acquires"} <= set(EDGE_TYPES)
    assert {"mna", "guidance_change", "regulatory_action"} <= set(CATALYST_TYPES)
    assert len(NODE_TYPES) > 4 and len(EDGE_TYPES) > 8 and len(CATALYST_TYPES) > 10


def test_metric_packs_software():
    from xar.ontology import canonical_kpi, is_higher_better, kpis_for_industry

    sw = {s.key for s in kpis_for_industry("software")}
    assert {"arr", "nrr", "grr", "rpo", "crpo", "rule_of_40"} <= sw
    # alias resolution feeds the LLM + the grounded write
    assert canonical_kpi("NRR") == "nrr"
    assert canonical_kpi("net revenue retention") == "nrr"
    assert canonical_kpi("GMV") == "gmv"
    assert canonical_kpi("not a metric at all") is None
    # direction drives ranking/percentiles
    assert is_higher_better("nrr") is True
    assert is_higher_better("cac_payback") is False  # months -> lower is better


def test_sector_classification_and_company_kpis():
    from xar.ontology import classify, kpis_for_company

    swe = {"themes": ["ai_software"], "seg": {"ai_software": "swe_devinfra"}}
    assert classify(swe) == {"industry": "software", "sector": "information_technology"}
    keys = {s.key for s in kpis_for_company(swe)}
    assert {"nrr", "rpo"} <= keys

    foundry = {"themes": ["ai_chip"], "seg": {"ai_chip": "chip_foundry"}}
    assert classify(foundry)["industry"] == "semiconductors"
    assert "book_to_bill" in {s.key for s in kpis_for_company(foundry)}

    # explicit override for a net-new-sector company
    bank = {"themes": [], "industry": "banks"}
    assert classify(bank)["sector"] == "financials"
    assert "nim" in {s.key for s in kpis_for_company(bank)}


def test_metric_packs_span_the_economy():
    from xar.ontology import metric_packs as mp

    # every major industry has a non-empty operating-metric pack (the moat breadth)
    for ind in ("software", "semiconductors", "banks", "insurance", "energy_ep",
                "utilities", "internet_media", "ecommerce", "retail", "restaurants",
                "pharma", "biotech", "aerospace_defense", "capital_goods", "reits"):
        assert mp.kpis_for_industry(ind), f"empty pack: {ind}"
    # canonical keys are globally unique
    keys = [s.key for s in mp.ALL_SPECS]
    assert len(keys) == len(set(keys))
    # every spec classifier resolves (no orphan tags)
    from xar.ontology.sectors import INDUSTRIES, SECTORS
    valid = set(INDUSTRIES) | set(SECTORS) | {"*"}
    for s in mp.ALL_SPECS:
        for c in s.classifiers:
            assert c in valid, f"unknown classifier {c} on {s.key}"


def test_extraction_schema_has_metrics():
    from xar.ontology import ExtractionResult

    r = ExtractionResult()
    assert r.metrics == []  # additive + backward-compatible (mock returns no metrics)


def test_grounded_handles_chinese_recall():
    """Evidence grounding must give CN evidence a real partial-overlap measure, not
    degrade to strict substring (CODE_REVIEW appendix A.1.3)."""
    from xar.kg.extract import _grounded

    # English: light paraphrase tolerated via token overlap (unchanged behavior)
    assert _grounded("record data center revenue", "NVIDIA reported record data-center revenue growth")
    # Chinese: light paraphrase (inserted 第三季度) must still ground via char bigrams
    assert _grounded("公司Q3营收创下历史新高", "本季度，公司在第三季度营收创下历史新高，超出预期。")
    # precision preserved: fabricated CN evidence is still rejected
    assert not _grounded("公司完全捏造的无关声明内容", "本季度营收创下历史新高，超出预期。")


def test_canonical_metric_normalization():
    from xar.ontology import canonical_metric

    # three providers, same fact -> one canonical key
    assert canonical_metric("fmp", "grossProfitRatio") == "gross_margin"
    assert canonical_metric("finnhub", "grossMargin") == "gross_margin"
    assert canonical_metric("yahoo", "grossMargins") == "gross_margin"
    assert canonical_metric("fmp", "totally_unknown_field") is None


def test_ontology_iri_anchors():
    from xar.ontology import edge_iri, node_iri

    assert node_iri("DownstreamCustomer", "schema").endswith("/Corporation")
    assert "competitor" in edge_iri("competes_with")


def test_sentiment_scorer():
    from xar.providers.sentiment import score

    assert score("record demand, design win, strong ramp") > 0
    assert score("guidance cut, weak demand, downgrade") < 0
    assert score("the module is blue") == 0.0


def test_wechat_rss_parsing_and_linking():
    from xar.ingestion import wechat

    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
      <channel><title>测试公众号</title>
        <item>
          <title>中际旭创 1.6T 模块放量，英伟达需求强劲</title>
          <link>https://mp.weixin.qq.com/s/abc</link>
          <pubDate>Mon, 15 Jun 2026 08:00:00 +0800</pubDate>
          <content:encoded><![CDATA[<p>公司预计 <b>1.6T</b> 出货加速。</p>]]></content:encoded>
        </item>
      </channel></rss>"""
    items = wechat._items_from_xml(xml)
    assert len(items) == 1
    assert "1.6T" in items[0]["title"]
    # HTML stripped to clean text
    assert "<b>" not in wechat._clean(items[0]["content"])
    assert "1.6T" in wechat._clean(items[0]["content"])
    # Chinese alias resolves to the watched company
    aliases = wechat._alias_index()
    assert wechat._link_company(items[0]["title"], aliases, None) == "innolight"
    # explicit feed->company mapping wins over alias scan
    assert wechat._link_company(items[0]["title"], aliases, "nvidia") == "nvidia"


def test_wechat_json_feed_parsing():
    from xar.ingestion import wechat

    payload = {"items": [{"title": "T", "url": "u", "content_html": "<p>x</p>",
                          "date_published": "2026-06-15T00:00:00Z"}]}
    items = wechat._items_from_json(payload)
    assert items[0]["url"] == "u" and items[0]["title"] == "T"
    assert wechat._parse_date(items[0]["date"]) is not None
    # disabled (no base url) -> safe no-op
    assert wechat.ingest() == [] or wechat.available()


def test_cycle_ontology():
    """The economic-cycle dimension: monotonic rank where counter-cyclical (discount/
    QSR) sits LATEST (falls last), and a serialized profile carries label+rank."""
    from xar.ontology import cycle
    from xar.ontology.cycle import CyclePosition as CP
    from xar.ontology.cycle import Cyclicality as CY

    assert [cycle.rank(p.value) for p in CP] == [1, 2, 3, 4, 5]
    assert cycle.rank(CP.COUNTER.value) > cycle.rank(CP.EARLY.value)  # discount falls later than apparel
    d = cycle.as_dict(cycle.profile(CP.COUNTER, CY.COUNTER_CYCLICAL, 0.6, noteCn="折扣"))
    assert d["position"] == "counter_cyclical" and d["rank"] == 5 and d["short"] == "CC"
    assert cycle.as_dict(None) is None


def test_cycle_as_dict_uniform_shape():
    """as_dict() always yields the full frontend CycleInfo shape — for a CycleProfile,
    a round-tripped dict, AND a partial company-level override (CODE_REVIEW B.1.1:
    the partial-override path previously dropped 5 required fields → undefined on the
    client)."""
    from xar.ontology import cycle
    from xar.ontology.cycle import CyclePosition as CP
    from xar.ontology.cycle import Cyclicality as CY

    required = {"position", "cyclicality", "sensitivity", "label", "labelCn", "short", "rank", "note", "noteCn"}
    full = cycle.as_dict(cycle.profile(CP.MID, CY.CYCLICAL, 1.1))
    assert set(full) == required
    # round-trip a serialized dict (the companies.meta.cycle → company_detail path): no legacy en/cn leak
    rt = cycle.as_dict(full)
    assert set(rt) == required and "en" not in rt and "cn" not in rt
    # PARTIAL company-level override now resolves EVERY required field (was the latent bug)
    part = cycle.as_dict({"position": "mid_cycle"})
    assert set(part) == required
    assert part["label"] == "Mid-Cycle" and part["labelCn"] == "中周期" and part["rank"] == 2
    assert part["short"] == "MC" and part["cyclicality"] == "cyclical" and part["sensitivity"] == 1.0


def test_new_consumer_themes_registered():
    from xar.ingestion.registry import COMPANIES, SEGMENTS, THEMES
    from xar.ontology import cycle

    for t in ("internet", "retail", "restaurants"):
        assert THEMES[t]["kind"] == "cycle"
        segs = [s for s, m in SEGMENTS.items() if m["theme"] == t]
        assert segs, f"no segments for {t}"
        for sid in segs:
            m = SEGMENTS[sid]
            assert m.get("cycle") is not None
            assert m["tier"] == cycle.rank(m["cycle"].position)  # tier IS the cycle rank
    # legacy themes still resolve as chain (back-compat)
    assert THEMES["ai_optical"].get("kind", "chain") == "chain"
    # ids + tickers globally unique after adding the rosters
    ids = [c["id"] for c in COMPANIES]
    assert len(ids) == len(set(ids))
    ticks = [tk for c in COMPANIES for tk in c["tickers"]]
    assert len(ticks) == len(set(ticks))


def test_cycle_company_inheritance():
    """Companies inherit their cycle profile from their segment; an explicit override
    wins; chain-theme names have none; non-US-cycle tickers are excluded."""
    from xar.ingestion.registry import COMPANIES, company_by_id
    from xar.ontology import cycle

    dg = cycle.cycle_of_company(company_by_id("dg"))     # Dollar General — discount
    gap = cycle.cycle_of_company(company_by_id("gap"))   # Gap — apparel
    assert dg["position"] == "counter_cyclical"
    assert dg["rank"] > gap["rank"]                       # discount later-cycle than apparel
    assert cycle.cycle_of_company(company_by_id("mcd"))["position"] == "counter_cyclical"  # QSR
    assert cycle.cycle_of_company(company_by_id("dri"))["position"] == "early_cycle"        # casual dining
    # explicit per-company override beats the segment default
    over = cycle.cycle_of_company({"cycle": {"position": "defensive"}, "seg": {"retail": "ret_apparel"}})
    assert over["position"] == "defensive"
    # chain-theme names carry no cycle profile
    assert cycle.cycle_of_company(company_by_id("nvidia")) is None
    # non-US-cycle names are NOT in the rosters
    ticks = {tk for c in COMPANIES for tk in c["tickers"]}
    assert not ({"PDD", "BABA", "JD", "YUMC", "CPNG", "MELI"} & ticks)


def test_restaurants_pack_and_classification():
    from xar.ontology import canonical_kpi, classify, kpis_for_industry

    keys = {s.key for s in kpis_for_industry("restaurants")}
    assert {"same_store_sales", "average_unit_volume", "unit_count", "check_size"} <= keys
    assert canonical_kpi("AUV") == "average_unit_volume"
    assert canonical_kpi("comps") == "same_store_sales"
    rst = {"themes": ["restaurants"], "seg": {"restaurants": "rst_qsr"}}
    assert classify(rst) == {"industry": "restaurants", "sector": "consumer_discretionary"}


def test_extracted_event_semantic_fields_additive():
    """The semantic-layer fields are additive with safe defaults (a mock that omits
    them still validates), and the causal edge type anchors cleanly for JSON-LD."""
    from xar.ontology import EDGE_TYPES, EdgeType, ExtractedEvent, edge_iri

    e = ExtractedEvent(company="NVIDIA", event_type="order", summary="s", evidence="q")
    assert e.time_orientation == "backward_looking"
    assert e.narrative == "" and e.drivers == []
    fwd = ExtractedEvent(company="X", event_type="capex_guidance", summary="s", evidence="q",
                         time_orientation="forward_looking", narrative="AI capex drives orders",
                         drivers=["AI capex"])
    assert fwd.time_orientation == "forward_looking" and fwd.drivers == ["AI capex"]
    # the new causal edge type is registered + has an (empty, domain-specific) IRI mapping
    assert "causally_linked" in EDGE_TYPES
    assert EdgeType.CAUSALLY_LINKED.value == "causally_linked"
    assert edge_iri("causally_linked") == ""  # no clean schema.org/FIBO analogue, but mapped


def test_finnhub_pull_news_builds_grey_docs(monkeypatch):
    """Finnhub company-news → Doc with grey/self-use posture, epoch→published_at, and a
    content-hash id that's stable across overlapping windows (so re-runs dedup)."""
    from xar.providers import finnhub

    sample = [
        {"datetime": 1719230400, "headline": "NVIDIA lands large AI order",
         "summary": "NVIDIA secured a multi-billion dollar accelerator order.",
         "url": "https://ex/1", "id": 42, "category": "company news", "source": "Reuters"},
        {"datetime": 1719240400, "headline": "x", "summary": "too short", "url": "https://ex/2"},
    ]
    monkeypatch.setattr(finnhub, "available", lambda: True)
    monkeypatch.setattr(finnhub, "get_json", lambda *a, **k: sample)
    saved = []
    monkeypatch.setattr(finnhub, "save", lambda doc: saved.append(doc) or doc.id)

    n = finnhub.pull_news("nvidia")
    assert n == 1  # the <24-char one is dropped
    d = saved[0]
    assert d.source == "finnhub" and d.doc_type == "news" and d.company_id == "nvidia"
    assert d.permission == "grey" and d.license_tag == "finnhub-news-extracted-facts-self-use"
    assert d.published_at is not None and d.published_at.year == 2024
    assert d.meta["finnhub_id"] == 42
    first_id = d.id
    # overlapping window re-pull yields the SAME id (content hash) -> ON CONFLICT dedups
    saved.clear()
    finnhub.pull_news("nvidia", since="2024-06-01")
    assert saved[0].id == first_id


def test_run_daily_isolates_source_failures(monkeypatch):
    """One source raising must not abort the round: it gets a 'failed' ingest_runs row
    while the others stay 'ok', and the parse/extract/expert stages still run. No DB."""
    from xar.orchestration import daily

    calls: dict = {"pull": [], "stages": []}
    # neutralize the DB-backed scaffolding
    monkeypatch.setattr(daily, "seed_companies", lambda: 0)
    monkeypatch.setattr("xar.kg.store.bootstrap_seed", lambda: None)
    finishes: list = []
    monkeypatch.setattr("xar.storage.runlog.start", lambda kind, since_ts=None: 1)
    monkeypatch.setattr("xar.storage.runlog.finish",
                        lambda rid, status, stats=None, error=None: finishes.append((status, error)))
    monkeypatch.setattr("xar.storage.runlog.last_success_ts", lambda kind: None)
    monkeypatch.setattr("xar.parsing.parse.parse_pending",
                        lambda: calls["stages"].append("parse") or 3)
    monkeypatch.setattr("xar.kg.extract.build_kg",
                        lambda **k: calls["stages"].append("kg") or {"docs": 1})
    monkeypatch.setattr("xar.kg.expert.process",
                        lambda **k: calls["stages"].append("expert") or {"kept": 0})
    monkeypatch.setattr("xar.kg.signals.derive_for_company", lambda cid: {})
    # restrict the universe so we don't iterate ~947 companies
    monkeypatch.setattr(daily, "COMPANIES", [{"id": "nvidia"}, {"id": "amd"}])

    from xar.providers import finnhub, polymarket, reddit
    monkeypatch.setattr(finnhub, "pull_news",
                        lambda cid, since=None: calls["pull"].append(("finnhub", cid)) or 1)
    monkeypatch.setattr(finnhub, "pull", lambda cid: {})
    monkeypatch.setattr(reddit, "pull_basket",
                        lambda ids: calls["pull"].append(("reddit", tuple(ids))) or 0)

    def _boom(*a, **k):
        raise RuntimeError("polymarket down")
    monkeypatch.setattr(polymarket, "pull", _boom)

    stats = daily.run_daily(sources=["finnhub", "reddit", "polymarket"], since="auto")

    # every enabled source attempted across the company shard
    assert ("finnhub", "nvidia") in calls["pull"] and ("finnhub", "amd") in calls["pull"]
    assert any(c[0] == "reddit" for c in calls["pull"])
    # the failing source is recorded failed; the others ok; the parent run still ok
    assert ("failed", "polymarket down") in finishes
    assert stats["sources"]["polymarket"]["error"] == "polymarket down"
    assert "error" not in stats["sources"]["finnhub"]
    # downstream stages ran despite the one source failure
    assert calls["stages"] == ["parse", "kg", "expert"]
    assert stats["chunks"] == 3 and stats["companies"] == 2


def test_providers_gate_without_keys(monkeypatch):
    # With no keys configured, key-gated providers report unavailable and never raise.
    import xar.config as cfg
    from xar.providers import finnhub, fmp, polygon

    cfg.get_settings.cache_clear()
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    cfg.get_settings.cache_clear()
    # provider.pull() must be a safe no-op (returns {}) when unconfigured
    assert finnhub.pull("nvidia") == {} or finnhub.available()
    assert fmp.pull("nvidia") == {} or fmp.available()
    assert polygon.pull("nvidia") == {} or polygon.available()
    cfg.get_settings.cache_clear()
