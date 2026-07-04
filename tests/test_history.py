"""History backfill planner: unit enumeration, cursor advance/wraparound,
failure tolerance, and the reset/status round-trip. NO network: every pull
executor is monkeypatched; only the real glm_worker_state table is exercised
(created defensively by the module itself), so no seeding is required."""
from __future__ import annotations

import pytest

from xar.ingestion import history


def _db_ok() -> bool:
    try:
        from xar.storage import db

        db.query("SELECT 1")
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_ok(), reason="no Postgres (docker compose up -d)")

US = {"id": "fake_us", "tickers": ["FAKE"], "region": "US"}
CN = {"id": "fake_cn", "tickers": ["300001.SZ"], "cn_code": "300001", "region": "CN"}
NEITHER = {"id": "fake_jp", "tickers": ["1234.T"], "region": "JP"}


# --- unit enumeration (pure; no DB) ------------------------------------------
def test_plan_units_us_company():
    units = history.plan_units(US)
    edgar = [u for u in units if u[0] == "edgar"]
    assert [y for _, _, y in edgar] == list(range(2026, 2015, -1))  # descending, inclusive
    assert units[-1] == ("finnhub_news", "fake_us", None)
    assert len(units) == 12  # 11 edgar years + 1 finnhub_news


def test_plan_units_cn_company():
    units = history.plan_units(CN)
    assert all(u == ("cninfo", "fake_cn", y) for u, y in zip(units, range(2026, 2015, -1)))
    assert len(units) == 11


def test_plan_units_skips_company_with_neither():
    assert history.plan_units(NEITHER) == []


def test_plan_units_dual_listed_gets_both_phases():
    dual = {"id": "dual", "tickers": ["DUAL", "300009.SZ"], "cn_code": "300009"}
    kinds = [u[0] for u in history.plan_units(dual)]
    assert kinds.count("edgar") == 11
    assert kinds.count("finnhub_news") == 1
    assert kinds.count("cninfo") == 11
    assert kinds.index("cninfo") == 12  # US units first, then the cn phase


# --- cursor mechanics against the real state table ----------------------------
@pytest.fixture
def fake_world(monkeypatch):
    """Tiny deterministic universe + no-network executors + no sleeping."""
    monkeypatch.setattr(history, "COMPANIES", [US, CN, NEITHER])
    calls: list[tuple] = []

    def fake_edgar(cid, year):
        calls.append(("edgar", cid, year))
        return 2

    def fake_news(cid):
        calls.append(("finnhub_news", cid, None))
        return 3

    def fake_cninfo(cid, year):
        calls.append(("cninfo", cid, year))
        return 1, False

    monkeypatch.setattr(history, "_pull_edgar_year", fake_edgar)
    monkeypatch.setattr(history, "_pull_finnhub_news_history", fake_news)
    monkeypatch.setattr(history, "_pull_cninfo_year", fake_cninfo)
    monkeypatch.setattr(history.time, "sleep", lambda _s: None)
    history.reset_cursor()
    yield calls
    history.reset_cursor()


@requires_db
def test_cursor_advance_and_wraparound(fake_world):
    calls = fake_world
    r1 = history.backfill_step(units=5)  # edgar 2026..2022
    assert r1["done_units"] == 5 and r1["docs_pulled"] == 10 and not r1["finished"]
    c = r1["cursor"]
    assert (c["phase_idx"], c["company_idx"], c["year"]) == (0, 0, 2021)

    # each step reloads the cursor from glm_worker_state -> resumability across calls
    r2 = history.backfill_step(units=8)  # edgar 2021..2016 + finnhub + wrap into cn 2026
    assert r2["done_units"] == 8 and not r2["finished"]
    assert ("finnhub_news", "fake_us", None) in calls
    c = r2["cursor"]
    assert (c["phase_idx"], c["company_idx"], c["year"]) == (1, 0, 2025)

    r3 = history.backfill_step(units=100)  # remaining 10 cninfo years, then done
    assert r3["done_units"] == 10 and r3["finished"]
    assert history.backfill_step(units=3)["done_units"] == 0  # finished stays finished
    assert calls == history.plan_units(US) + history.plan_units(CN)  # exact order, no skips


@requires_db
def test_cninfo_company_skip_marks_company_done(fake_world, monkeypatch):
    monkeypatch.setattr(history, "_pull_cninfo_year", lambda _cid, _year: (0, True))
    history.backfill_step(units=12)  # complete the whole US phase
    r = history.backfill_step(units=5)  # first cn unit skips the rest of the company
    assert r["done_units"] == 1 and r["finished"]


@requires_db
def test_unit_failure_never_raises_and_advances(fake_world, monkeypatch):
    def boom(_cid, _year):
        raise RuntimeError("edgar down")

    monkeypatch.setattr(history, "_pull_edgar_year", boom)
    r = history.backfill_step(units=3)
    assert r["done_units"] == 3 and r["docs_pulled"] == 0
    assert r["cursor"]["year"] == 2023  # advanced past the failing units


@requires_db
def test_reset_and_status_roundtrip(fake_world):
    s0 = history.backfill_status()
    assert not s0["started"] and not s0["finished"]
    assert s0["totals"] == {"docs": 0, "units": 0}
    assert s0["planned_units"] == 23  # 12 us units + 11 cn units

    history.backfill_step(units=3)
    s1 = history.backfill_status()
    assert s1["started"] and not s1["finished"]
    assert s1["totals"] == {"docs": 6, "units": 3}
    assert (s1["phase"], s1["company_id"], s1["year"]) == ("us", "fake_us", 2023)

    history.reset_cursor()
    s2 = history.backfill_status()
    assert not s2["started"] and s2["totals"] == {"docs": 0, "units": 0}
    assert s2["cursor"] is None
