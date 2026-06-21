"""Job postings as demand/capacity signals — via OFFICIAL ATS APIs only
(Greenhouse / Lever / Ashby). GREEN: sanctioned public APIs. Never LinkedIn or
job-board scraping (ToS + CFAA). (design §4)"""
from __future__ import annotations

import httpx

from ..config import get_settings
from ..logging import get_logger
from .base import Doc, polite, save

log = get_logger("xar.ingest.jobs")


def ingest_greenhouse(company_id: str, board_token: str, limit: int = 50) -> list[str]:
    polite("greenhouse")
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"
    return _ingest_json(company_id, url, _parse_greenhouse, "greenhouse", limit)


def ingest_lever(company_id: str, company_token: str, limit: int = 50) -> list[str]:
    polite("lever")
    url = f"https://api.lever.co/v0/postings/{company_token}?mode=json"
    return _ingest_json(company_id, url, _parse_lever, "lever", limit)


def _ingest_json(company_id, url, parser, ats, limit) -> list[str]:
    s = get_settings()
    try:
        r = httpx.get(url, headers={"User-Agent": s.http_user_agent}, timeout=30)
        r.raise_for_status()
        items = parser(r.json())
    except Exception as e:
        log.warning("%s jobs failed: %s", ats, e)
        return []
    ids = []
    for title, text, jurl in items[:limit]:
        doc = Doc(
            company_id=company_id, source="jobs", doc_type="job_posting",
            title=title, text=text[:20_000], url=jurl,
            permission="green", license_tag=f"ats-{ats}-official-api",
        )
        ids.append(save(doc))
    log.info("%s jobs: %s -> %d", ats, company_id, len(ids))
    return ids


def _parse_greenhouse(j) -> list[tuple[str, str, str]]:
    out = []
    for job in j.get("jobs", []):
        out.append((job.get("title", ""), job.get("content", "")[:20000], job.get("absolute_url", "")))
    return out


def _parse_lever(j) -> list[tuple[str, str, str]]:
    out = []
    for job in j:
        out.append((job.get("text", ""), job.get("descriptionPlain", "")[:20000], job.get("hostedUrl", "")))
    return out
