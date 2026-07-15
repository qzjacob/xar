"""Offline tests for the curated industry-news RSS provider (no network, no DB)."""
from __future__ import annotations

from datetime import datetime, timezone

from xar.ingestion.feeds import FEEDS, feed_by_id, feeds_for_theme
from xar.ingestion.registry import THEMES
from xar.providers import rss

RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
 <channel>
  <title>Example Industry Wire</title>
  <item>
   <title>HBM4 qualification &amp; the next AI-memory ramp</title>
   <link>https://example.com/hbm4-ramp</link>
   <description><![CDATA[<p>Vendor <b>qualifies</b> HBM4 stacks for 2027 accelerators.</p>]]></description>
   <pubDate>Tue, 30 Jun 2026 08:00:00 +0000</pubDate>
  </item>
  <item>
   <title>Old story about legacy nodes that should be cursor-skipped</title>
   <link>https://example.com/old-legacy-nodes</link>
   <description>Trailing-edge capacity update from last year, long enough to keep.</description>
   <pubDate>Mon, 05 Jan 2026 09:30:00 +0000</pubDate>
  </item>
  <item>
   <title>short</title>
   <link>https://example.com/too-short</link>
   <description>tiny</description>
   <pubDate>Tue, 30 Jun 2026 10:00:00 +0000</pubDate>
  </item>
 </channel>
</rss>"""

ATOM_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <title>Example Atom</title>
 <entry>
  <title>Starship flight window confirmed for orbital-compute demo</title>
  <link href="https://example.com/starship-window"/>
  <summary>Launch provider confirms the next integrated flight test window.</summary>
  <published>2026-06-29T12:00:00Z</published>
 </entry>
</feed>"""


# --- registry invariants -----------------------------------------------------
def test_registry_shape_and_theme_coverage():
    ids = [f["id"] for f in FEEDS]
    assert len(ids) == len(set(ids)), "feed ids must be unique"
    for f in FEEDS:
        assert f["url"].startswith("https://")
        assert f["name"] and f["lang"]
        # themes 可为空(市场级资金流源不挂链主题,走 flow_extract 语义道);非空必须合法
        assert all(t in THEMES for t in f["themes"]), f"bad themes on {f['id']}"
    covered = {t for f in FEEDS for t in f["themes"]}
    assert covered == set(THEMES), f"themes without a feed: {set(THEMES) - covered}"
    assert feed_by_id("spacenews")["name"] == "SpaceNews"
    assert feed_by_id("nope") is None
    assert all("restaurants" in f["themes"] for f in feeds_for_theme("restaurants"))


# --- pure parsing ------------------------------------------------------------
def test_parse_rss2():
    items = rss.parse_feed(RSS_FIXTURE)
    assert len(items) == 3
    first = items[0]
    assert first["title"] == "HBM4 qualification & the next AI-memory ramp"
    assert first["url"] == "https://example.com/hbm4-ramp"
    assert "qualifies HBM4 stacks" in first["summary"] and "<" not in first["summary"]
    assert first["published"] == datetime(2026, 6, 30, 8, 0, tzinfo=timezone.utc)


def test_parse_atom():
    items = rss.parse_feed(ATOM_FIXTURE)
    assert len(items) == 1
    assert items[0]["url"] == "https://example.com/starship-window"
    assert items[0]["published"] == datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)


def test_parse_garbage_is_empty():
    assert rss.parse_feed("this is not xml") == []


# --- pull_feed end-to-end (fetch + save + db stubbed) --------------------------
def test_pull_feed_saves_theme_tagged_docs(monkeypatch):
    saved, theme_updates = [], []
    monkeypatch.setattr(rss, "_fetch", lambda url: RSS_FIXTURE)
    monkeypatch.setattr(rss, "save", lambda doc: saved.append(doc) or doc.id)
    monkeypatch.setattr(rss.db, "execute", lambda sql, params=None: theme_updates.append(params))

    n = rss.pull_feed("semiwiki")  # no cursor -> both long-enough items
    assert n == 2 and len(saved) == 2  # the <24-char item is dropped
    doc = saved[0]
    assert doc.source == "rss" and doc.doc_type == "news" and doc.permission == "grey"
    assert doc.company_id is None and doc.url == "https://example.com/hbm4-ramp"
    assert doc.license_tag == "rss-headline-extracted-facts-self-use"
    assert doc.meta["feed_id"] == "semiwiki" and doc.meta["themes"] == ["ai_chip"]
    assert theme_updates[0][0] == "ai_chip"  # documents.theme tag


def test_pull_feed_since_cursor_skips_old(monkeypatch):
    saved = []
    monkeypatch.setattr(rss, "_fetch", lambda url: RSS_FIXTURE)
    monkeypatch.setattr(rss, "save", lambda doc: saved.append(doc) or doc.id)
    monkeypatch.setattr(rss.db, "execute", lambda sql, params=None: None)

    n = rss.pull_feed("semiwiki", since=datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert n == 1 and saved[0].url == "https://example.com/hbm4-ramp"
    # string cursors (CLI --since) work too
    saved.clear()
    assert rss.pull_feed("semiwiki", since="2026-06-01T00:00:00+00:00") == 1


def test_pull_unknown_feed_is_zero(monkeypatch):
    monkeypatch.setattr(rss, "_fetch", lambda url: (_ for _ in ()).throw(AssertionError("no fetch")))
    assert rss.pull_feed("does-not-exist") == 0
