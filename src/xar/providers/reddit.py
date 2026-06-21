"""Reddit connector. Uses an OAuth script-app token when REDDIT_CLIENT_ID/SECRET
are set (higher rate limits); otherwise falls back to the public .json endpoints.
Searches finance/tech subreddits for watched names and stores posts as social
signal. Always degrades gracefully."""
from __future__ import annotations

import base64

import httpx

from ..config import get_settings
from ..ingestion.base import polite
from ..ingestion.registry import company_by_id
from ..storage import structured
from .base import get_json, log
from .sentiment import score as sentiment_score

_SUBS = "wallstreetbets+stocks+investing+hardware+semiconductors+nvidia+optics"
_UA = "xar-research/0.1 (research; self-use)"


def available() -> bool:
    return True  # public fallback always available


def _oauth_token() -> str | None:
    s = get_settings()
    if not (s.reddit_client_id and s.reddit_client_secret):
        return None
    try:
        auth = base64.b64encode(f"{s.reddit_client_id}:{s.reddit_client_secret}".encode()).decode()
        polite("www.reddit.com")
        r = httpx.post("https://www.reddit.com/api/v1/access_token",
                       data={"grant_type": "client_credentials"},
                       headers={"Authorization": f"Basic {auth}", "User-Agent": _UA}, timeout=20)
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception as e:  # noqa: BLE001
        log.warning("reddit oauth failed: %s", e)
        return None


def _search(term: str, token: str | None, limit: int):
    params = {"q": term, "restrict_sr": "1", "sort": "new", "limit": limit, "t": "month"}
    if token:
        return get_json(f"https://oauth.reddit.com/r/{_SUBS}/search",
                        params=params, headers={"Authorization": f"Bearer {token}",
                        "User-Agent": _UA}, host="oauth.reddit.com")
    return get_json(f"https://www.reddit.com/r/{_SUBS}/search.json", params=params,
                    headers={"User-Agent": _UA}, host="www.reddit.com")


def pull_company(company_id: str, token: str | None = None, limit: int = 25) -> int:
    c = company_by_id(company_id)
    if not c:
        return 0
    term = c["name"].split()[0]
    js = _search(term, token, limit)
    children = (js or {}).get("data", {}).get("children", [])
    n = 0
    for ch in children:
        d = ch.get("data", {})
        text = f"{d.get('title','')}\n{d.get('selftext','')}".strip()
        if term.lower() not in text.lower():
            continue
        structured.upsert_social(
            d.get("id"), "reddit", company_id=company_id, author=d.get("author"),
            url="https://reddit.com" + d.get("permalink", ""),
            posted_at=_ts(d.get("created_utc")), text=text[:8000],
            metrics={"score": d.get("score"), "comments": d.get("num_comments")},
            sentiment=sentiment_score(text), permission="grey",
            meta={"subreddit": d.get("subreddit")})
        n += 1
    log.info("reddit %s: %d posts", company_id, n)
    return n


def pull_basket(company_ids: list[str]) -> int:
    token = _oauth_token()
    return sum(pull_company(cid, token=token) for cid in company_ids)


def _ts(epoch):
    if not epoch:
        return None
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc)
    except Exception:
        return None
