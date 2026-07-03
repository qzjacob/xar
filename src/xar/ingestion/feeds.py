"""Curated industry-news RSS/Atom feed registry (丰富资讯来源) — code-as-truth.

Free, publicly served feeds only, hand-verified (HTTP 200 + valid RSS/Atom with
the project User-Agent) at build time. Each entry tags the THEMES it informs
(ids from `xar.ingestion.registry.THEMES`) so headlines land theme-tagged in
`documents` and flow through the same parse → KG-extract → expert pipeline as
every other news source. Adding a source = adding a dict here; nothing else
changes (`xar pull-rss` and the daily 'rss' source pick it up).

Verified 2026-07 — dead/unusable candidates, kept out on purpose:
    TrendForce press RSS (trendforce.com/presscenter/rss ...)  -> 404, no public RSS
    SIA (semiconductors.org/feed/)                             -> returns HTML, not RSS
    AnandTech (anandtech.com/rss/)                             -> publication ceased; HTML
    optics.org/rss                                             -> HTML interstitial
    EE Times (eetimes.com/feed/)                               -> 403 to non-browser UAs
    NASASpaceflight (nasaspaceflight.com/feed/)                -> Cloudflare 403 to bots
    The Robot Report (therobotreport.com/feed/)                -> Cloudflare 403 to bots
    Restaurant Business (restaurantbusinessonline.com/rss.xml) -> HTML app shell
    The Information                                            -> paywalled, skipped
"""
from __future__ import annotations

# Each feed: id (stable slug), name, url, themes (registry THEMES ids), lang.
# Optional: license_tag override (default set by the rss provider).
FEEDS: list[dict] = [
    # --- ai_chip / ai_optical (semis, WFE, HBM, datacenter interconnect) -----
    {"id": "semiwiki", "name": "SemiWiki", "lang": "en",
     "url": "https://semiwiki.com/feed/", "themes": ["ai_chip"]},
    {"id": "tomshardware", "name": "Tom's Hardware", "lang": "en",
     "url": "https://www.tomshardware.com/feeds/all", "themes": ["ai_chip"]},
    {"id": "digitimes_daily", "name": "DIGITIMES Asia — Daily Headlines", "lang": "en",
     "url": "https://www.digitimes.com/rss/daily.xml", "themes": ["ai_chip", "ai_optical"]},
    {"id": "servethehome", "name": "ServeTheHome", "lang": "en",
     "url": "https://www.servethehome.com/feed/", "themes": ["ai_chip", "ai_optical"]},
    # --- ai_software ----------------------------------------------------------
    {"id": "techcrunch_ai", "name": "TechCrunch — AI", "lang": "en",
     "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
     "themes": ["ai_software"]},
    {"id": "infoq", "name": "InfoQ", "lang": "en",
     "url": "https://feed.infoq.com/", "themes": ["ai_software"]},
    # --- space_exploration ----------------------------------------------------
    {"id": "spacenews", "name": "SpaceNews", "lang": "en",
     "url": "https://spacenews.com/feed/", "themes": ["space_exploration"]},
    {"id": "spaceflightnow", "name": "Spaceflight Now", "lang": "en",
     "url": "https://spaceflightnow.com/feed/", "themes": ["space_exploration"]},
    # --- humanoid_robotics ----------------------------------------------------
    {"id": "ieee_robotics", "name": "IEEE Spectrum — Robotics", "lang": "en",
     "url": "https://spectrum.ieee.org/feeds/topic/robotics.rss",
     "themes": ["humanoid_robotics"]},
    {"id": "robohub", "name": "Robohub", "lang": "en",
     "url": "https://robohub.org/feed/", "themes": ["humanoid_robotics"]},
    # --- internet (platforms / consumer tech) ---------------------------------
    {"id": "techcrunch", "name": "TechCrunch", "lang": "en",
     "url": "https://techcrunch.com/feed/", "themes": ["internet"]},
    {"id": "arstechnica", "name": "Ars Technica", "lang": "en",
     "url": "https://feeds.arstechnica.com/arstechnica/index", "themes": ["internet"]},
    # --- retail ---------------------------------------------------------------
    {"id": "retaildive", "name": "Retail Dive", "lang": "en",
     "url": "https://www.retaildive.com/feeds/news/", "themes": ["retail"]},
    {"id": "modernretail", "name": "Modern Retail", "lang": "en",
     "url": "https://www.modernretail.co/feed/", "themes": ["retail", "internet"]},
    # --- restaurants ----------------------------------------------------------
    {"id": "restaurantdive", "name": "Restaurant Dive", "lang": "en",
     "url": "https://www.restaurantdive.com/feeds/news/", "themes": ["restaurants"]},
    {"id": "nrn", "name": "Nation's Restaurant News", "lang": "en",
     "url": "https://www.nrn.com/rss.xml", "themes": ["restaurants"]},
]


def feed_by_id(feed_id: str) -> dict | None:
    return next((f for f in FEEDS if f["id"] == feed_id), None)


def feeds_for_theme(theme: str) -> list[dict]:
    return [f for f in FEEDS if theme in f["themes"]]
