"""10-year historical-document backfill planner (2026 -> 2016).

Walks the watched universe BACKWARDS through time, pulling historical filings /
announcements / news into `documents` so the GLM extraction worker can process
them into the ontology. Resumable: the cursor is persisted into the
glm_worker_state key/value table after EVERY unit, so a crash resumes at the
next unit. Every save path dedups on the content-hash Doc.id, so re-pulling an
already-done unit is idempotent.

Work order (theme relevance + data richness):
  phase "us": US-tickered names first (EDGAR is the deep free source). Per
      company: one EDGAR unit per year descending 2026..2016 (10-K/10-Q/8-K/
      20-F, <=14 filings/yr; pre-IPO years just come back empty), then ONE
      finnhub_news unit — a backwards ~monthly walk from today (the free tier
      only reaches ~1yr back; 2 consecutive empty months stop the walk).
  phase "cn": cn_code names via cninfo. One unit per year descending when the
      installed akshare supports start/end_date windows; otherwise a single
      max-depth pull on the first year unit and the company is marked done.
      akshare missing entirely -> the company is skipped gracefully.
Companies with neither a US ticker nor a cn_code are not enumerated at all.

No LLM calls. Per-unit failures never raise (logged + cursor advances).
"""
from __future__ import annotations

import inspect
import time
from datetime import date, datetime, timedelta, timezone


from ..config import get_settings
from ..logging import get_logger
from .base import Doc, polite, save
from .edgar import _filing_text, _parse_date, _ticker
from .registry import COMPANIES, company_by_id

log = get_logger("xar.ingest.history")

CURSOR_KEY = "history_cursor"
def _start_year() -> int:
    return datetime.now(timezone.utc).year   # newest year walked first(随时间推移自动前滚)


def _years() -> tuple[int, ...]:
    y = _start_year()
    return tuple(range(y, y - 11, -1))       # 10-year window inclusive
PHASES: tuple[str, ...] = ("us", "cn")

EDGAR_FORMS = ["10-K", "10-Q", "8-K", "20-F"]
EDGAR_YEAR_CAP = 14  # max filings pulled per (company, year)
FINNHUB_MAX_MONTHS = 14  # hard bound on the backwards monthly news walk
FINNHUB_EMPTY_STOP = 2  # consecutive empty months that end a company's walk
CNINFO_YEAR_CAP = 120  # max announcements per (company, year) window
CNINFO_MAX_DEPTH = 200  # one-shot depth when akshare has no date windows

# --- cursor persistence (shared storage.kvstate, key='history_cursor') -------
def _load_cursor() -> dict | None:
    from ..storage.kvstate import get_state

    v = get_state(CURSOR_KEY, default={})
    return v or None


def _save_cursor(cursor: dict) -> None:
    from ..storage.kvstate import save_state

    save_state(CURSOR_KEY, cursor)


def _new_cursor() -> dict:
    return {"phase_idx": 0, "company_idx": 0, "year": _start_year(),
            "totals": {"docs": 0, "units": 0},
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished": False}


# --- plan enumeration ---------------------------------------------------------
def _phase_companies(phase: str) -> list[dict]:
    """Companies enumerated by a phase, in registry order (US names first overall)."""
    if phase == "us":
        return [c for c in COMPANIES if _ticker(c)]
    return [c for c in COMPANIES if c.get("cn_code")]


def _year_seq(phase: str) -> tuple[int | None, ...]:
    """Per-company unit sequence for a phase. `None` = the one finnhub_news unit."""
    return _years() + (None,) if phase == "us" else _years()


def _unit_for(phase: str, company: dict, year: int | None) -> tuple[str, str, int | None]:
    if phase == "cn":
        return ("cninfo", company["id"], year)
    if year is None:
        return ("finnhub_news", company["id"], None)
    return ("edgar", company["id"], year)


def plan_units(company: dict) -> list[tuple[str, str, int | None]]:
    """Ordered work units for ONE company across all phases (the enumeration
    contract): US-tickered -> ('edgar', cid, 2026..2016) + one ('finnhub_news',
    cid, None); cn_code -> ('cninfo', cid, 2026..2016); neither -> []."""
    units: list[tuple[str, str, int | None]] = []
    for phase in PHASES:
        qualifies = _ticker(company) if phase == "us" else company.get("cn_code")
        if qualifies:
            units += [_unit_for(phase, company, y) for y in _year_seq(phase)]
    return units


def _current_unit(cursor: dict) -> tuple[str, str, int | None] | None:
    """Resolve the unit the cursor points at, rolling forward over exhausted
    companies/phases (and empty phases). Mutates the cursor in place; returns
    None (and sets finished) when the whole plan is done."""
    while True:
        pi = cursor["phase_idx"]
        if pi >= len(PHASES):
            cursor["finished"] = True
            return None
        phase = PHASES[pi]
        companies = _phase_companies(phase)
        ci = cursor["company_idx"]
        want = cursor.get("company_id")
        if want and ci < len(companies) and companies[ci]["id"] != want:
            # 注册表顺序变化:按 id 重新对位;找不到(公司被移除)则保持位置继续
            for j, c in enumerate(companies):
                if c["id"] == want:
                    ci = cursor["company_idx"] = j
                    break
        if ci >= len(companies):
            cursor["phase_idx"] += 1
            cursor["company_idx"] = 0
            cursor["company_id"] = None
            cursor["year"] = _start_year()
            continue
        seq = _year_seq(phase)
        if cursor["year"] not in seq:  # stale/corrupt cursor field: restart the company
            cursor["year"] = seq[0]
        cursor["company_id"] = companies[ci]["id"]
        return _unit_for(phase, companies[ci], cursor["year"])


def _advance(cursor: dict, *, skip_company: bool = False) -> None:
    """Move the cursor to the next unit (caller persists it right after)."""
    seq = _year_seq(PHASES[cursor["phase_idx"]])
    i = seq.index(cursor["year"]) if cursor["year"] in seq else len(seq) - 1
    if skip_company or i + 1 >= len(seq):
        cursor["company_idx"] += 1
        cursor["company_id"] = None      # 下一 _current_unit 重新钉住新公司 id
        cursor["year"] = _start_year()
    else:
        cursor["year"] = seq[i + 1]
    _current_unit(cursor)  # eagerly normalize over phase boundaries / set finished


# --- per-unit pull executors ---------------------------------------------------
_EDGAR_MEMO: dict = {}   # {ticker: edgar.Company} — size 1:连续年单元同司复用一次索引拉取


def _edgar_company(ticker: str, edgar_mod):
    if ticker not in _EDGAR_MEMO:
        _EDGAR_MEMO.clear()
        _EDGAR_MEMO[ticker] = edgar_mod.Company(ticker)
    return _EDGAR_MEMO[ticker]


def _pull_edgar_year(company_id: str, year: int) -> int:
    """One YEAR of EDGAR filings for a US-tickered company (<= EDGAR_YEAR_CAP).
    edgartools >=5.x filters server-side via get_filings(year=...); pre-IPO
    years simply return nothing. Mirrors edgar.ingest_company's Doc/save path."""
    ticker = _ticker(company_by_id(company_id) or {})
    if not ticker:
        return 0
    import edgar

    edgar.set_identity(get_settings().edgar_identity)
    company = _edgar_company(ticker, edgar)
    filings = company.get_filings(form=EDGAR_FORMS, year=year)
    filings = filings.head(EDGAR_YEAR_CAP) if filings is not None else []
    n = 0
    for f in filings:
        try:
            text = _filing_text(f)
            if not text:
                continue
            save(Doc(
                company_id=company_id, source="edgar",
                doc_type=str(getattr(f, "form", "filing")),
                title=f"{ticker} {getattr(f, 'form', '')} {getattr(f, 'filing_date', '')}",
                text=str(text)[:400_000],
                url=getattr(f, "filing_url", None) or getattr(f, "homepage_url", None),
                published_at=_parse_date(getattr(f, "filing_date", None)),
                permission="green", license_tag="us-gov-public-domain",
                meta={"accession": str(getattr(f, "accession_no", "")),
                      "backfill_year": year},
            ))
            n += 1
        except Exception as e:  # noqa: BLE001 — one bad filing must not sink the year
            log.warning("edgar history filing failed (%s %s): %s", ticker, year, e)
    return n


def _pull_finnhub_news_history(company_id: str) -> int:
    """Backwards ~monthly walk of Finnhub company news from today. The free tier
    only reaches ~1 year back, so stop after FINNHUB_EMPTY_STOP consecutive empty
    windows (bounded by FINNHUB_MAX_MONTHS calls at finnhub's own 60/min pacer).
    Reuses finnhub.pull_news, i.e. its save path, dedup, and rate limiting."""
    from ..providers import finnhub

    if not finnhub.available():
        return 0
    total, empty_streak = 0, 0
    until = date.today()
    for _ in range(FINNHUB_MAX_MONTHS):
        since = until - timedelta(days=30)
        n = finnhub.pull_news(company_id, since=since, until=until)
        total += n
        empty_streak = 0 if n else empty_streak + 1
        if empty_streak >= FINNHUB_EMPTY_STOP:
            break
        until = since - timedelta(days=1)
    return total


def _pull_cninfo_year(company_id: str, year: int) -> tuple[int, bool]:
    """One YEAR of cninfo statutory disclosures. Returns (docs, company_done):
    - akshare missing -> (0, True): skip the whole company gracefully;
    - akshare without start/end_date windows -> ONE max-depth pull via
      cninfo.ingest_company on the first year unit, then the company is done;
    - windowed akshare -> that year's announcements, same Doc shape as cninfo."""
    from . import cninfo

    code = (company_by_id(company_id) or {}).get("cn_code")
    if not code:
        return 0, True
    ak = cninfo._ak()
    if ak is None:
        return 0, True  # optional [cn] extra not installed: skip company, keep walking
    fn = ak.stock_zh_a_disclosure_report_cninfo
    params = inspect.signature(fn).parameters
    if "start_date" not in params or "end_date" not in params:
        return len(cninfo.ingest_company(company_id, limit=CNINFO_MAX_DEPTH)), True
    polite("cninfo")
    df = fn(symbol=code, market="沪深京", start_date=f"{year}0101", end_date=f"{year}1231")
    n = 0
    for _, row in df.head(CNINFO_YEAR_CAP).iterrows():
        r = {k: row[k] for k in df.columns}
        title = str(r.get("公告标题") or r.get("title") or "")
        save(Doc(
            company_id=company_id, source="cninfo", doc_type="announcement",
            title=title, text=title,
            url=str(r.get("公告链接") or r.get("url") or ""),
            published_at=cninfo._date(r.get("公告时间") or r.get("date")),
            permission="green", license_tag="cn-mandatory-disclosure",
            meta={k: str(v) for k, v in r.items()},
        ))
        n += 1
    return n, False


def _execute(unit: tuple[str, str, int | None]) -> tuple[int, bool]:
    """Run one work unit. Returns (docs_pulled, skip_rest_of_company)."""
    source, company_id, year = unit
    if source == "edgar":
        return _pull_edgar_year(company_id, year), False
    if source == "finnhub_news":
        return _pull_finnhub_news_history(company_id), False
    return _pull_cninfo_year(company_id, year)


# --- public API (the resident worker's contract) -------------------------------
def backfill_step(units: int = 4) -> dict:
    """Advance the historical backfill by `units` work units. One unit = one
    (company, source, year) pull. Returns {done_units, docs_pulled, cursor,
    finished: bool}. Never raises for per-unit failures (log + advance); the
    cursor is persisted after EVERY unit so a crash resumes exactly where it
    stopped. No LLM calls."""
    cursor = _load_cursor() or _new_cursor()
    _current_unit(cursor)  # normalize a stale/legacy cursor position up front
    done = docs = consec_fail = 0
    for i in range(max(0, units)):
        unit = _current_unit(cursor)
        if unit is None:
            break
        if i:  # politeness floor between consecutive units within one step
            time.sleep(get_settings().crawl_delay_seconds)
        skip_company = False
        failed = False
        try:
            pulled, skip_company = _execute(unit)
        except Exception as e:  # noqa: BLE001
            pulled = 0
            failed = True
            log.warning("backfill unit %s failed: %s", unit, str(e)[:200])
        if failed:
            consec_fail += 1
            # 失败不消耗单元:重试一次;第二次仍失败 → 毒单元,记档后跳过
            retries = cursor.setdefault("unit_retries", {})
            ukey = f"{unit[0]}:{unit[1]}:{unit[2]}"
            retries[ukey] = int(retries.get(ukey, 0)) + 1
            if retries[ukey] >= 2:
                cursor.setdefault("failed_units", []).append(ukey)
                retries.pop(ukey, None)
                _advance(cursor, skip_company=skip_company)
            _save_cursor(cursor)
            if consec_fail >= 3:
                # 连续 3 失败 = 基建/网络级故障:提前收兵,单元留待下轮
                log.warning("backfill aborting step after %d consecutive failures", consec_fail)
                break
            continue
        consec_fail = 0
        docs += pulled
        done += 1
        cursor["totals"]["docs"] += pulled
        cursor["totals"]["units"] += 1
        cursor.get("unit_retries", {}).pop(f"{unit[0]}:{unit[1]}:{unit[2]}", None)
        _advance(cursor, skip_company=skip_company)
        _save_cursor(cursor)  # crash-safe: persist after EVERY unit
        log.info("backfill %s -> %d docs (cursor p%s c%s y=%s)", unit, pulled,
                 cursor["phase_idx"], cursor["company_idx"], cursor["year"])
    _save_cursor(cursor)
    return {"done_units": done, "docs_pulled": docs, "cursor": cursor,
            "finished": bool(cursor.get("finished"))}


def backfill_status() -> dict:
    """Cursor position + running totals from glm_worker_state. Safe on a fresh
    DB / before the first step: reports a not-yet-started plan."""
    cursor = _load_cursor()
    us, cn = _phase_companies("us"), _phase_companies("cn")
    out = {
        "started": cursor is not None,
        "finished": bool(cursor and cursor.get("finished")),
        "cursor": cursor,
        "totals": (cursor or {}).get("totals") or {"docs": 0, "units": 0},
        "planned_units": len(us) * len(_year_seq("us")) + len(cn) * len(_year_seq("cn")),
        "us_companies": len(us),
        "cn_companies": len(cn),
        "phase": None, "company_id": None, "year": None,
    }
    if cursor and not out["finished"]:
        probe = dict(cursor)  # shallow copy: _current_unit only touches scalar keys
        unit = _current_unit(probe)
        if unit is None:
            out["finished"] = True
        else:
            out["phase"] = PHASES[probe["phase_idx"]]
            out["company_id"], out["year"] = unit[1], unit[2]
    return out


def reset_cursor() -> None:
    """Forget all backfill progress; the next backfill_step restarts from the top.
    Already-pulled documents stay put (every save path dedups by content hash)."""
    from ..storage.kvstate import delete_state

    delete_state(CURSOR_KEY)
