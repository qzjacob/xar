"""Institutional ownership from SEC 13F-HR filings via edgartools. GREEN:
US-government public domain. A curated code-as-truth list of top managers
(CIKs verified against EDGAR 2026-07) is swept for the latest 13F-HR; equity
holdings are matched to the watched universe by the filing's own CUSIP-resolved
ticker and upserted into the `holdings` table (UNIQUE company_id, holder,
as_of). Per-manager failures are non-fatal. Import-light: edgartools loads
lazily inside functions."""
from __future__ import annotations

from datetime import date

from ..config import get_settings
from ..logging import get_logger
from ..storage import db
from .registry import COMPANIES

log = get_logger("xar.ingest.13f")

SOURCE = "edgar_13f"

# (display name, SEC CIK) — code-as-truth. Every CIK verified to have a current
# 13F-HR on EDGAR. Notes: BlackRock's 13F filer moved to the new "BlackRock,
# Inc." entity (2012383) in 2024; Fidelity files as FMR LLC; the Capital Group
# complex files per management arm; Sequoia Fund files as Ruane Cunniff.
MANAGERS: tuple[tuple[str, str], ...] = (
    ("Berkshire Hathaway", "1067983"),
    ("Vanguard Group", "102909"),
    ("BlackRock", "2012383"),
    ("State Street", "93751"),
    ("FMR (Fidelity)", "315066"),
    ("T. Rowe Price Associates", "80255"),
    ("Capital World Investors", "1422849"),
    ("Capital Research Global Investors", "1422848"),
    ("Baillie Gifford", "1088875"),
    ("Coatue Management", "1135730"),
    ("Tiger Global Management", "1167483"),
    ("Altimeter Capital Management", "1541617"),
    ("Duquesne Family Office", "1536411"),
    ("Appaloosa LP", "1656456"),
    ("Pershing Square Capital Management", "1336528"),
    ("Third Point", "1040273"),
    ("Lone Pine Capital", "1061165"),
    ("Viking Global Investors", "1103804"),
    ("D1 Capital Partners", "1747057"),
    ("Whale Rock Capital Management", "1387322"),
    ("Light Street Capital Management", "1569049"),
    ("ARK Investment Management", "1697748"),
    ("Ruane Cunniff (Sequoia Fund)", "1720792"),
    ("Soros Fund Management", "1029160"),
    ("Bridgewater Associates", "1350694"),
    ("Renaissance Technologies", "1037389"),
    ("Citadel Advisors", "1423053"),
    ("Millennium Management", "1273087"),
    ("Point72 Asset Management", "1603466"),
)


def _norm(ticker: str) -> str:
    """Normalize share-class punctuation so BRK.B / BRK-B / MOG.A line up."""
    return str(ticker).strip().upper().replace(".", "-")


def universe_ticker_map() -> dict[str, str]:
    """Normalized US ticker -> company_id for the whole watched universe.
    Exchange-suffixed tickers (300308.SZ, 7011.T ...) are non-US: excluded."""
    out: dict[str, str] = {}
    for c in COMPANIES:
        for t in c.get("tickers", []):
            if "." in t:
                continue
            out.setdefault(_norm(t), c["id"])
    return out


def _num(v) -> float | None:
    try:
        f = float(str(v).replace(",", ""))
        return f if f == f else None  # NaN guard
    except (TypeError, ValueError):
        return None


def aggregate_infotable(table) -> dict[str, tuple[float, float]]:
    """Reduce a 13F infotable to {normalized ticker: (shares, value_usd)}.

    Keeps straight equity rows only (Type SH/Shares, no Put/Call) and sums
    across the manager's split reporting lines (discretion/other-manager rows).
    Accepts a pandas DataFrame or a plain list of row dicts (tests)."""
    rows = table.to_dict("records") if hasattr(table, "to_dict") else list(table)
    agg: dict[str, tuple[float, float]] = {}
    for rec in rows:
        tick = rec.get("Ticker")
        if not isinstance(tick, str) or not tick.strip():
            continue  # unresolved CUSIP (NaN/None) — can't match to universe
        put_call = rec.get("PutCall")
        if isinstance(put_call, str) and put_call.strip():
            continue  # option position, not share ownership
        typ = str(rec.get("Type") or "").strip().lower()
        if typ not in ("sh", "shares"):
            continue  # principal-amount (debt) rows
        key = _norm(tick)
        s0, v0 = agg.get(key, (0.0, 0.0))
        agg[key] = (s0 + (_num(rec.get("SharesPrnAmount")) or 0.0),
                    v0 + (_num(rec.get("Value")) or 0.0))
    return agg


def upsert_holding(company_id: str, *, holder: str, as_of, holder_cik=None,
                   shares=None, value_usd=None, filed_at=None, source: str = SOURCE) -> None:
    """Idempotent write onto holdings' (company_id, holder, as_of) unique key."""
    db.execute(
        """INSERT INTO holdings
             (company_id,holder,holder_cik,shares,value_usd,as_of,filed_at,source)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (company_id,holder,as_of) DO UPDATE SET
             shares=EXCLUDED.shares, value_usd=EXCLUDED.value_usd,
             holder_cik=EXCLUDED.holder_cik, filed_at=EXCLUDED.filed_at,
             source=EXCLUDED.source""",
        (company_id, holder, holder_cik, shares, value_usd, as_of, filed_at, source),
    )


def _dt(v) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def pull_manager(name: str, cik: str, ticker_map: dict[str, str] | None = None) -> int:
    """Latest 13F-HR for one manager -> holdings rows for universe matches.
    Returns rows written; 0 (logged) on any failure."""
    import edgar

    edgar.set_identity(get_settings().edgar_identity)
    ticker_map = ticker_map or universe_ticker_map()
    try:
        filing = edgar.Company(cik).get_filings(form="13F-HR").latest()
        if filing is None:
            return 0
        report = filing.obj()
        table = getattr(report, "infotable", None)
    except Exception as e:  # noqa: BLE001 — per-manager failures are non-fatal
        log.warning("13f fetch failed for %s (CIK %s): %s", name, cik, e)
        return 0
    if table is None or getattr(table, "empty", False):
        return 0
    as_of = _dt(getattr(report, "report_period", None)) or _dt(filing.filing_date)
    filed_at = _dt(filing.filing_date)
    if as_of is None:
        return 0
    n = 0
    for tick, (shares, value) in aggregate_infotable(table).items():
        company_id = ticker_map.get(tick)
        if not company_id:
            continue
        try:
            upsert_holding(company_id, holder=name, holder_cik=str(cik), shares=shares,
                           value_usd=value, as_of=as_of, filed_at=filed_at)
            n += 1
        except Exception as e:  # noqa: BLE001 — e.g. company not yet seeded in DB
            log.warning("13f upsert failed for %s/%s: %s", name, company_id, e)
    log.info("13f: %s -> %d universe holdings (as of %s)", name, n, as_of)
    return n


def sweep(managers: tuple[tuple[str, str], ...] | None = None) -> dict[str, int]:
    """Pull the latest 13F-HR for every curated manager. Returns rows per manager."""
    ticker_map = universe_ticker_map()
    stats: dict[str, int] = {}
    for name, cik in managers or MANAGERS:
        try:
            stats[name] = pull_manager(name, cik, ticker_map)
        except Exception as e:  # noqa: BLE001
            log.warning("13f sweep failed for %s: %s", name, e)
            stats[name] = 0
    log.info("13f sweep: %d managers, %d holdings rows", len(stats), sum(stats.values()))
    return stats
