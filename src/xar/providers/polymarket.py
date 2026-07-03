"""Polymarket connector via the public Gamma API (no key). Pulls active markets,
filters to the watched THEMES (AI chain, space exploration, humanoid robotics, and
the consumer complex: retail / restaurants / internet platforms + the macro that
drives them), and stores forward probabilities as prediction-market signals.

These probabilities are a *forward* read on the demand drivers (hyperscaler capex,
accelerator launches, launch cadence, consumer macro) that no filing yet reflects —
the earliest catalyst signal in the stack. Matched theme names are appended to the
row's `tags` so downstream reads can scope by theme."""
from __future__ import annotations

import json

from ..ingestion.registry import COMPANIES
from ..storage import structured
from .base import get_json, log

_GAMMA = "https://gamma-api.polymarket.com/markets"
_HOST = "gamma-api.polymarket.com"

# Domain keyword filter (lowercased substring match on the question), per theme
# group. A market matching ANY group is kept; the matching groups become tags.
_THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ai_chain": ("nvidia", "ai ", "a.i", "data center", "datacenter", "gpu", "openai",
                 "chip", "tsmc", "hyperscaler", "compute", "agi", "rubin", "blackwell",
                 "broadcom", "semiconductor", "cloud capex", "h100", "b200", "gb200"),
    "space_exploration": ("spacex", "starship", "starlink", "rocket lab", "blue origin",
                          "new glenn", "nasa", "artemis", "satellite", "orbital",
                          "reach orbit", "moon landing", "mars landing", "humans on mars",
                          "space station", "reusable rocket", "launch cadence"),
    "humanoid_robotics": ("humanoid", "robot", "optimus", "figure ai", "figure 0",
                          "unitree", "boston dynamics", "robotaxi", "self-driving",
                          "autonomous vehicle", "embodied ai"),
    # consumer complex = retail / restaurants / internet platforms + the macro
    # (rates / inflation / spending) that gates all discretionary demand.
    "consumer": ("recession", "inflation", "cpi ", "rate cut", "fed funds", "fomc",
                 "consumer spending", "retail sales", "holiday sales", "black friday",
                 "tariff", "minimum wage", "gas price",
                 "amazon", "walmart", "costco", "shopify", "e-commerce",
                 "netflix", "tiktok", "youtube", "instagram", "facebook", "uber",
                 "doordash", "airbnb", "mcdonald", "starbucks", "chipotle", "wendy's"),
}
# Back-compat flat view (kept for callers/tests that only need "is it on-theme").
_KEYWORDS = tuple(k for kws in _THEME_KEYWORDS.values() for k in kws)

_TECH_ROUTE_HINTS = {"cpo": "tr_cpo", "co-packaged": "tr_cpo", "lpo": "tr_lpo",
                     "1.6t": "tr_1600g", "800g": "tr_800g", "silicon photonics": "tr_siph",
                     # space
                     "starship": "tr_reusable", "reusable rocket": "tr_reusable",
                     "starlink": "tr_megaconstellation", "constellation": "tr_megaconstellation",
                     "orbital data center": "tr_orbital_compute",
                     "space data center": "tr_orbital_compute",
                     # humanoid
                     "humanoid": "tr_vla", "optimus": "tr_vla", "embodied ai": "tr_vla"}


def available() -> bool:
    return True  # public API


def _alias_index() -> list[tuple[str, str]]:
    idx = []
    for c in COMPANIES:
        for a in [c["name"], *c.get("aliases", [])]:
            idx.append((a.lower(), c["id"]))
    return idx


def _link_company(question: str, aliases) -> str | None:
    q = question.lower()
    return next((cid for alias, cid in aliases if alias and alias in q), None)


def pull(limit: int = 300) -> int:
    js = get_json(_GAMMA, params={"closed": "false", "limit": limit, "order": "volume",
                  "ascending": "false"}, host=_HOST)
    if not isinstance(js, list):
        return 0
    aliases = _alias_index()
    n = 0
    for m in js:
        q = (m.get("question") or "").strip()
        ql = q.lower()
        themes = [th for th, kws in _THEME_KEYWORDS.items() if any(k in ql for k in kws)]
        if not themes:
            continue
        outcomes = _loads(m.get("outcomes"))
        prices = _loads(m.get("outcomePrices"))
        route = next((tr for k, tr in _TECH_ROUTE_HINTS.items() if k in ql), None)
        cid = _link_company(q, aliases)
        pairs = list(zip(outcomes, prices)) or [("Yes", m.get("lastTradePrice"))]
        for outcome, price in pairs:
            structured.upsert_prediction_market(
                m.get("id") or m.get("slug") or q[:40], question=q, outcome=str(outcome),
                probability=_num(price), volume=_num(m.get("volume")),
                close_date=(m.get("endDate") or "")[:10] or None,
                tags=[t for t in (m.get("category"), *themes) if t], company_id=cid,
                tech_route_tag=route, source="polymarket")
            n += 1
    log.info("polymarket: stored %d outcomes", n)
    return n


def _loads(v):
    if isinstance(v, list):
        return v
    try:
        return json.loads(v) if v else []
    except Exception:
        return []


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
