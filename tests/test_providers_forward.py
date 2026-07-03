"""Workstream-A provider tests: Finnhub forward earnings calendar + basket-wide
news safety, Polymarket theme widening, and X expert-roster theme coverage.

Fixture-based only — every HTTP entry point and every storage upsert is
monkeypatched, so nothing here touches the network or the database."""
from __future__ import annotations

from datetime import date, timedelta


def _no_pace(monkeypatch, finnhub):
    """Disable the 60/min pacer so unit tests don't sleep."""
    monkeypatch.setattr(finnhub, "_RATE_MIN_INTERVAL", 0.0)


def test_finnhub_pull_calendar_upserts_forward_earnings(monkeypatch):
    """GET /calendar/earnings rows land in event_calendar via upsert_calendar with
    the schema's fields (event_type=earnings, source=finnhub, estimate meta); past
    and unparseable dates are dropped; non-US companies never issue an HTTP call."""
    from xar.providers import finnhub
    from xar.storage import structured

    _no_pace(monkeypatch, finnhub)
    monkeypatch.setattr(finnhub, "available", lambda: True)
    future = (date.today() + timedelta(days=30)).isoformat()
    past = (date.today() - timedelta(days=30)).isoformat()
    sample = {"earningsCalendar": [
        {"date": future, "epsEstimate": 1.25, "revenueEstimate": 5.0e9, "hour": "amc",
         "quarter": 3, "year": 2026, "symbol": "NVDA"},
        {"date": past, "epsEstimate": 1.0, "symbol": "NVDA"},   # past -> dropped
        {"date": "not-a-date", "epsEstimate": 0.5},              # bad date -> dropped
    ]}
    calls: list = []
    monkeypatch.setattr(finnhub, "get_json",
                        lambda url, params=None, host=None, **k: calls.append(url) or sample)
    upserts: list = []

    def _fake_upsert(company_id, event_type, scheduled_for, **kw):
        upserts.append((company_id, event_type, scheduled_for, kw))
        return True

    monkeypatch.setattr(structured, "upsert_calendar", _fake_upsert)

    n = finnhub.pull_calendar("nvidia")
    assert n == 1 and len(upserts) == 1
    cid, etype, dd, kw = upserts[0]
    assert cid == "nvidia" and etype == "earnings" and dd == date.fromisoformat(future)
    assert kw["source"] == "finnhub" and kw["importance"] == 3
    assert kw["title"] == "NVDA earnings"
    assert kw["meta"]["epsEstimate"] == 1.25 and kw["meta"]["hour"] == "amc"

    # non-US (CN-listed) name: no US ticker -> return 0 with NO HTTP call
    before = len(calls)
    assert finnhub.pull_calendar("innolight") == 0
    assert len(calls) == before


def test_finnhub_pull_wires_calendar_and_skips_non_us(monkeypatch):
    """The per-company pull() bundle (what the daily loop invokes) now includes the
    forward calendar; non-US companies return {} without touching any endpoint."""
    from xar.providers import finnhub

    monkeypatch.setattr(finnhub, "available", lambda: True)
    hit: list = []
    for fn in ("pull_fundamentals", "pull_estimates", "pull_ratings", "pull_insider",
               "pull_news", "pull_calendar"):
        monkeypatch.setattr(finnhub, fn, lambda cid, _f=fn: hit.append(_f) or 1)
    out = finnhub.pull("nvidia")
    assert out.get("calendar") == 1 and "pull_calendar" in hit
    hit.clear()
    assert finnhub.pull("innolight") == {} and hit == []  # CN name: fast skip


def test_finnhub_news_basket_covers_us_and_skips_non_us(monkeypatch):
    """Basket-wide news sweep hits every US-tickered company exactly once and skips
    non-US names without a call — safe (paced) for the whole registry universe."""
    from xar.providers import finnhub

    _no_pace(monkeypatch, finnhub)
    monkeypatch.setattr(finnhub, "available", lambda: True)
    # real registry ids: nvidia (US), innolight (CN), tokyoelectron (JP)
    monkeypatch.setattr(finnhub, "COMPANIES",
                        [{"id": "nvidia"}, {"id": "innolight"}, {"id": "tokyoelectron"}])
    pulled: list = []
    monkeypatch.setattr(finnhub, "pull_news",
                        lambda cid, since=None: pulled.append(cid) or 3)

    stats = finnhub.pull_news_basket()
    assert pulled == ["nvidia"]
    assert stats == {"companies": 1, "docs": 3, "skipped_non_us": 2}

    # unconfigured -> silent no-op (config gating)
    monkeypatch.setattr(finnhub, "available", lambda: False)
    assert finnhub.pull_news_basket() == {}


def test_finnhub_calendar_basket_scopes_to_us(monkeypatch):
    from xar.providers import finnhub

    _no_pace(monkeypatch, finnhub)
    monkeypatch.setattr(finnhub, "available", lambda: True)
    monkeypatch.setattr(finnhub, "COMPANIES", [{"id": "nvidia"}, {"id": "innolight"}])
    monkeypatch.setattr(finnhub, "pull_calendar",
                        lambda cid, days_ahead=180: 2)
    stats = finnhub.pull_calendar_basket()
    assert stats == {"companies": 1, "events": 2, "skipped_non_us": 1}


def test_polymarket_widened_themes_and_tags(monkeypatch):
    """Markets from the new themes (space / humanoid / consumer) are kept and tagged
    with the matching theme; off-theme markets are dropped; space route hints map."""
    from xar.providers import polymarket
    from xar.storage import structured

    markets = [
        {"question": "Will SpaceX Starship reach orbit again in 2026?", "id": "m1",
         "outcomes": '["Yes","No"]', "outcomePrices": '["0.82","0.18"]',
         "volume": "120000", "endDate": "2026-12-31T00:00:00Z", "category": "Science"},
        {"question": "Will McDonald's report positive US same-store sales this quarter?",
         "id": "m2", "outcomes": '["Yes","No"]', "outcomePrices": '["0.6","0.4"]',
         "volume": "5000", "endDate": "2026-10-01T00:00:00Z", "category": "Business"},
        {"question": "Will Tesla sell a humanoid robot to consumers by 2027?", "id": "m3",
         "outcomes": '["Yes","No"]', "outcomePrices": '["0.15","0.85"]',
         "volume": "9000", "endDate": "2027-12-31T00:00:00Z", "category": "Tech"},
        {"question": "Will Taylor Swift release an album in 2026?", "id": "m4",
         "outcomes": '["Yes","No"]', "outcomePrices": '["0.5","0.5"]',
         "volume": "999999", "endDate": "2026-12-31T00:00:00Z", "category": "Pop"},
    ]
    monkeypatch.setattr(polymarket, "get_json", lambda *a, **k: markets)
    rows: list = []
    monkeypatch.setattr(structured, "upsert_prediction_market",
                        lambda market_id, **kw: rows.append((market_id, kw)))

    n = polymarket.pull()
    stored = {mid for mid, _ in rows}
    assert stored == {"m1", "m2", "m3"} and n == 6  # 3 markets x 2 outcomes; m4 dropped

    by_id = {mid: kw for mid, kw in rows}
    assert "space_exploration" in by_id["m1"]["tags"] and "Science" in by_id["m1"]["tags"]
    assert by_id["m1"]["tech_route_tag"] == "tr_reusable"
    assert "consumer" in by_id["m2"]["tags"]
    assert "humanoid_robotics" in by_id["m3"]["tags"]
    assert by_id["m3"]["tech_route_tag"] == "tr_vla"


def test_twitter_expert_rosters_cover_all_registry_themes(monkeypatch):
    """Every registry theme has an expert roster (~4-8 real handles, deduped, no '@')
    and matching domain terms; the theme sweep pull() walks all of them."""
    from xar.ingestion.registry import THEMES
    from xar.providers import twitter

    assert set(THEMES) <= set(twitter.EXPERT_HANDLES)
    assert set(twitter.DOMAIN_TERMS) == set(twitter.EXPERT_HANDLES)
    for theme, handles in twitter.EXPERT_HANDLES.items():
        assert 4 <= len(handles) <= 8, theme
        assert len(set(handles)) == len(handles), f"dup handle in {theme}"
        assert all(h and not h.startswith("@") and " " not in h for h in handles), theme
        assert twitter.DOMAIN_TERMS[theme], f"no domain terms for {theme}"

    monkeypatch.setattr(twitter, "available", lambda: True)
    swept: list = []
    monkeypatch.setattr(twitter, "pull_experts",
                        lambda theme, max_results=30: swept.append(theme) or 0)
    twitter.pull()
    assert set(swept) == set(twitter.EXPERT_HANDLES)
