"""X (Twitter) connector + expert-source layer — via TwitterAPI.io.

TwitterAPI.io is a third-party X data API: GET /twitter/tweet/advanced_search with
an `X-API-Key` header and Twitter search syntax (cursor-paginated). (Official X API
v2 bearer is also supported as a fallback.) Beyond raw cashtag search this curates
DOMAIN-EXPERT accounts per chain theme and pulls their + domain-keyword posts into
`social_posts` (platform='x') AND mirrors substantive ones into `documents`
(source='x', grey) so the expert-agent processor (kg/expert.py) refines them into
ontology-integrated insights. Gated by TWITTERAPI_KEY (or X_BEARER_TOKEN)."""
from __future__ import annotations

from datetime import datetime

import httpx

from ..config import get_settings
from ..ingestion.base import polite
from ..ingestion.registry import COMPANIES, company_by_id
from ..storage import structured
from .base import log
from .sentiment import score as sentiment_score

_TAPI = "https://api.twitterapi.io/twitter/tweet/advanced_search"
_X_V2 = "https://api.twitter.com/2/tweets/search/recent"

EXPERT_HANDLES: dict[str, list[str]] = {
    "ai_optical": ["dnystedt", "SemiAnalysis_", "Fiber_Optics", "LightCounting", "optics_news"],
    "ai_chip": ["dylan522p", "Jukanlosreve", "SKundojjala", "IanCutress", "morethanmoore", "Frank_Hong_"],
    "ai_software": ["jaminball", "swyx", "GergelyOrosz", "saranormous", "alexandr_wang", "bgurley"],
    "space_exploration": ["SpaceX", "Rocket_Lab", "elonmusk", "torybruno", "planet", "SciGuySpace"],
    "humanoid_robotics": ["Tesla_Optimus", "Figure_robot", "UnitreeRobotics", "adcock_brett", "DrJimFan", "Scott_eFoster"],
}
DOMAIN_TERMS: dict[str, list[str]] = {
    "ai_optical": ['"optical module"', "1.6T", "CPO", '"silicon photonics"', "EML", '"linear drive"'],
    "ai_chip": ["HBM", "CoWoS", '"advanced packaging"', "EUV", '"AI accelerator"', '"wafer fab"'],
    "ai_software": ['"AI agent"', "Agentforce", "copilot", "RAG", "LLMOps",
                    '"agentic"', '"consumption revenue"', '"seat-based"'],
    "space_exploration": ["Starship", '"reusable rocket"', '"satellite constellation"', "Starlink",
                          '"orbital data center"', '"launch cadence"', '"space-based compute"'],
    "humanoid_robotics": ["humanoid", "Optimus", '"harmonic reducer"', '"roller screw"',
                          '"dexterous hand"', '"frameless motor"', "VLA", '"embodied AI"'],
}


def _key() -> str:
    s = get_settings()
    return s.twitterapi_key or s.x_bearer_token


def _use_tapi() -> bool:
    return bool(get_settings().twitterapi_key)


def available() -> bool:
    return bool(_key())


def _handles(theme: str) -> list[str]:
    override = [h.strip().lstrip("@") for h in get_settings().x_expert_handles.split(",") if h.strip()]
    return override or EXPERT_HANDLES.get(theme, [])


def _alias_index() -> list[tuple[str, str]]:
    idx: list[tuple[str, str]] = []
    for c in COMPANIES:
        for a in [c["name"], *c.get("aliases", [])]:
            if a:
                idx.append((a.lower(), c["id"]))
    return sorted(idx, key=lambda t: -len(t[0]))


def _link_company(text: str, aliases) -> str | None:
    t = (text or "").lower()
    return next((cid for alias, cid in aliases if alias and alias in t), None)


def _parse_created(s: str | None) -> datetime | None:
    if not s:
        return None
    try:  # TwitterAPI.io legacy format: "Wed Jun 17 13:12:32 +0000 2026"
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
    except Exception:
        try:  # official X v2 ISO
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None


def _norm(tw: dict) -> dict:
    """Normalize a TwitterAPI.io or X-v2 tweet into a common shape."""
    if "author" in tw or "createdAt" in tw:  # TwitterAPI.io
        author = (tw.get("author") or {})
        return {"id": str(tw.get("id") or tw.get("tweetId") or ""), "text": tw.get("text", ""),
                "author": author.get("userName") or author.get("screen_name") or author.get("id"),
                "created": tw.get("createdAt"),
                "metrics": {"like": tw.get("likeCount"), "retweet": tw.get("retweetCount"),
                            "reply": tw.get("replyCount"), "view": tw.get("viewCount")}}
    pm = tw.get("public_metrics", {})  # X v2
    return {"id": str(tw.get("id", "")), "text": tw.get("text", ""), "author": tw.get("author_id"),
            "created": tw.get("created_at"), "metrics": pm}


def _search(query: str, max_results: int = 30) -> list[dict]:
    """Cursor-paginated search; returns normalized tweets. Never raises."""
    out: list[dict] = []
    try:
        if _use_tapi():
            cursor = ""
            while len(out) < max_results:
                polite("api.twitterapi.io")
                r = httpx.get(_TAPI, headers={"X-API-Key": _key()},
                              params={"query": query, "queryType": "Latest", "cursor": cursor},
                              timeout=30)
                r.raise_for_status()
                js = r.json()
                tweets = js.get("tweets") or js.get("data") or []
                out += [_norm(t) for t in tweets]
                cursor = js.get("next_cursor") or ""
                if not js.get("has_next_page") or not cursor or not tweets:
                    break
        else:  # official X API v2
            polite("api.twitter.com")
            r = httpx.get(_X_V2, headers={"Authorization": f"Bearer {_key()}"},
                          params={"query": query, "max_results": min(max_results, 100),
                                  "tweet.fields": "created_at,public_metrics,author_id"}, timeout=30)
            r.raise_for_status()
            out += [_norm(t) for t in (r.json().get("data") or [])]
    except Exception as e:  # noqa: BLE001
        log.warning("x search failed (%s): %s", query[:40], e)
    return out[:max_results]


def _store(tw: dict, aliases, expert: bool) -> None:
    text = tw["text"]
    if not tw["id"] or not text:
        return
    cid = _link_company(text, aliases)
    structured.upsert_social(
        tw["id"], "x", company_id=cid, author=tw["author"],
        url=f"https://x.com/i/web/status/{tw['id']}", posted_at=_parse_created(tw["created"]),
        text=text, metrics=tw["metrics"], sentiment=sentiment_score(text),
        permission="grey", meta={"expert": expert})
    if len(text) >= 80:
        from ..ingestion.base import Doc, save

        save(Doc(company_id=cid, source="x", doc_type="x_post",
                 title=f"X @{tw['author'] or '?'}", text=text,
                 url=f"https://x.com/i/web/status/{tw['id']}", permission="grey",
                 license_tag="x-extracted-facts-self-use",
                 meta={"expert": expert, "social_id": f"x:{tw['id']}"}))


def pull_company(company_id: str, max_results: int = 25) -> int:
    if not available():
        return 0
    c = company_by_id(company_id)
    if not c:
        return 0
    terms = [f"${t}" for t in c.get("tickers", []) if "." not in t]
    terms.append(f'"{c["name"].split()[0]}"')
    q = f"({' OR '.join(terms)}) -is:retweet lang:en"
    aliases = _alias_index()
    posts = _search(q, max_results)
    for tw in posts:
        _store(tw, aliases, expert=False)
    log.info("x %s: %d posts", company_id, len(posts))
    return len(posts)


def pull_experts(theme: str, max_results: int = 30) -> int:
    if not available():
        return 0
    aliases = _alias_index()
    n = 0
    handles = _handles(theme)
    if handles:
        from_q = " OR ".join(f"from:{h}" for h in handles[:15])
        for tw in _search(f"({from_q}) -is:retweet lang:en", max_results):
            _store(tw, aliases, expert=True)
            n += 1
    terms = DOMAIN_TERMS.get(theme, [])
    if terms:
        for tw in _search(f"({' OR '.join(terms[:8])}) -is:retweet lang:en", max_results):
            _store(tw, aliases, expert=False)
            n += 1
    log.info("x experts %s: %d posts", theme, n)
    return n


def pull_frontier(handles: list[str], terms: list[str], max_results: int = 40) -> list[dict]:
    """Frontier-voice search for the Exploration module: recent posts from domain
    expert handles + domain terms. Returns normalized posts (caller stores them
    with a domain tag). Never raises; [] when unconfigured."""
    if not available():
        return []
    out: list[dict] = []
    if handles:
        from_q = " OR ".join(f"from:{h}" for h in handles[:12])
        out += _search(f"({from_q}) -is:retweet lang:en", max_results)
    if terms:
        out += _search(f"({' OR '.join(terms[:8])}) -is:retweet lang:en", max_results)
    return out


def pull(theme: str | None = None) -> dict:
    if not available():
        return {}
    themes = [theme] if theme else list(EXPERT_HANDLES.keys())
    return {t: pull_experts(t) for t in themes}
