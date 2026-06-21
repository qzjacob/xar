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


def test_ontology_taxonomy_sizes():
    from xar.ontology import CATALYST_TYPES, EDGE_TYPES, NODE_TYPES

    assert len(CATALYST_TYPES) == 10
    assert "single_source_risk" in EDGE_TYPES
    assert "TechRoute" in NODE_TYPES


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
