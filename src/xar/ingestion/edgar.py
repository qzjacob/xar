"""US SEC EDGAR ingestion via edgartools. GREEN: US-government public domain;
the safe core of the pipeline. Honors SEC fair-access (identity + rate limit)."""
from __future__ import annotations

from datetime import datetime

from ..config import get_settings
from ..logging import get_logger
from .base import Doc, save
from .registry import company_by_id

log = get_logger("xar.ingest.edgar")
_US_FORMS = ["10-K", "10-Q", "8-K", "20-F", "6-K"]


def _ticker(company: dict) -> str | None:
    for t in company.get("tickers", []):
        if "." not in t:  # US tickers have no exchange suffix
            return t
    return None


def ingest_company(company_id: str, forms: list[str] | None = None, limit: int = 8) -> list[str]:
    company = company_by_id(company_id)
    if not company:
        return []
    ticker = _ticker(company)
    if not ticker:
        return []  # CN-only filer; handled by cninfo connector

    import edgar

    edgar.set_identity(get_settings().edgar_identity)
    forms = forms or _US_FORMS
    ids: list[str] = []
    try:
        co = edgar.Company(ticker)
        filings = co.get_filings(form=forms).head(limit)
    except Exception as e:
        log.warning("edgar lookup failed for %s: %s", ticker, e)
        return []

    for f in filings:
        try:
            text = _filing_text(f)
            if not text:
                continue
            pub = _parse_date(getattr(f, "filing_date", None))
            doc = Doc(
                company_id=company_id,
                source="edgar",
                doc_type=str(getattr(f, "form", "filing")),
                title=f"{ticker} {getattr(f, 'form', '')} {getattr(f, 'filing_date', '')}",
                text=text[:400_000],
                url=getattr(f, "filing_url", None) or getattr(f, "homepage_url", None),
                published_at=pub,
                permission="green",
                license_tag="us-gov-public-domain",
                meta={"accession": str(getattr(f, "accession_no", ""))},
            )
            ids.append(save(doc))
        except Exception as e:
            log.warning("edgar filing parse failed: %s", e)
    log.info("edgar: %s -> %d filings", ticker, len(ids))
    return ids


def _filing_text(f) -> str:
    for attr in ("markdown", "text"):
        fn = getattr(f, attr, None)
        if callable(fn):
            try:
                out = fn()
                if out:
                    return str(out)
            except Exception:
                continue
    return str(f)


def _parse_date(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v))
    except Exception:
        return None
