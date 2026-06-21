"""Connector base: politeness/rate-limiting, the permission posture, and the
document upsert path (raw -> object store, facts/text -> documents table)."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock

from ..config import get_settings
from ..logging import get_logger
from ..storage import db, objects

log = get_logger("xar.ingest")

# Per-host last-request timestamps for crawl-delay politeness.
_LAST: dict[str, float] = {}
_LOCK = Lock()


def polite(host: str) -> None:
    s = get_settings()
    with _LOCK:
        last = _LAST.get(host, 0.0)
        wait = s.crawl_delay_seconds - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        _LAST[host] = time.time()


@dataclass
class Doc:
    company_id: str | None
    source: str               # edgar | cninfo | news | product | jobs | research_meta
    doc_type: str
    title: str
    text: str                 # extracted facts/text (NOT redistribution of third-party originals)
    url: str | None = None
    published_at: datetime | None = None
    permission: str = "green"  # green | grey | red (self-use risk tag)
    license_tag: str | None = None
    raw: bytes | None = None   # original artifact -> object store
    meta: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        basis = (self.url or "") + (self.title or "") + (self.text[:200] if self.text else "")
        return f"{self.source}:{hashlib.sha256(basis.encode()).hexdigest()[:20]}"


def save(doc: Doc) -> str:
    """Idempotently persist a document. Returns its id."""
    object_key = objects.put(doc.raw, suffix=".bin") if doc.raw else None
    db.execute(
        """INSERT INTO documents
             (id, company_id, source, doc_type, title, url, published_at,
              permission, license_tag, object_key, text, meta)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (id) DO UPDATE SET
              text = EXCLUDED.text, title = EXCLUDED.title, meta = EXCLUDED.meta""",
        (doc.id, doc.company_id, doc.source, doc.doc_type, doc.title, doc.url,
         doc.published_at, doc.permission, doc.license_tag, object_key, doc.text,
         _json(doc.meta)),
    )
    return doc.id


def _json(d: dict) -> str:
    import json

    return json.dumps(d, ensure_ascii=False, default=str)


def seed_companies() -> int:
    """Load the registry companies into the DB (idempotent)."""
    from .registry import COMPANIES

    n = 0
    for c in COMPANIES:
        meta = _json({"segments": c.get("seg", {})})
        db.execute(
            """INSERT INTO companies (id,name,aliases,tickers,region,chain_role,cn_code,themes,meta)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (id) DO UPDATE SET
                 name=EXCLUDED.name, aliases=EXCLUDED.aliases, tickers=EXCLUDED.tickers,
                 region=EXCLUDED.region, chain_role=EXCLUDED.chain_role,
                 themes=EXCLUDED.themes, meta=EXCLUDED.meta""",
            (c["id"], c["name"], c.get("aliases", []), c.get("tickers", []),
             c.get("region"), c.get("chain_role"), c.get("cn_code"),
             c.get("themes", ["ai_optical"]), meta),
        )
        n += 1
    log.info("seeded %d companies", n)
    return n
