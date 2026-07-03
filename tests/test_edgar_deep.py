"""EDGAR-deep unit tests (xbrl + holdings13f) — no DB, no network, no API key.
Facts and 13F infotables are in-memory fixtures shaped like edgartools output."""
from datetime import date
from types import SimpleNamespace

from xar.ingestion import holdings13f as h13f
from xar.ingestion import xbrl


def _fact(start, end, value, *, ptype="duration", fp="Q1", fy=2025,
          filed="2025-11-01", concept="us-gaap:Revenues", unit="USD"):
    return SimpleNamespace(
        concept=concept, numeric_value=value, unit=unit,
        period_start=date.fromisoformat(start) if start else None,
        period_end=date.fromisoformat(end), period_type=ptype,
        fiscal_period=fp, fiscal_year=fy, filing_date=date.fromisoformat(filed))


def _fy2025_quarters():
    return [
        _fact("2024-10-01", "2024-12-28", 20.0, fp="Q1"),
        _fact("2024-12-29", "2025-03-29", 25.0, fp="Q2"),
        _fact("2025-03-30", "2025-06-28", 30.0, fp="Q3"),
        _fact("2024-09-29", "2025-09-27", 100.0, fp="FY"),  # 10-K full year
        _fact("2024-09-29", "2025-03-29", 45.0, fp="Q2"),   # 6-month YTD: excluded
    ]


def test_pick_quarters_derives_q4_from_fy():
    rows = xbrl.pick_quarters([_fy2025_quarters()], instant=False, quarters=8)
    assert len(rows) == 4
    by_period = {r["period"]: r for r in rows}
    assert by_period["Q4-2025"]["value"] == 25.0  # 100 - (20+25+30)
    assert by_period["Q4-2025"]["period_end"] == date(2025, 9, 27)
    assert by_period["Q4-2025"]["meta"]["derived"] == "fy_minus_3q"
    # newest first, real period_ends throughout
    assert [r["period_end"] for r in rows] == sorted(
        (r["period_end"] for r in rows), reverse=True)


def test_pick_quarters_restated_value_wins_original_label_kept():
    facts = [
        _fact("2024-10-01", "2024-12-28", 20.0, fp="Q1", fy=2025, filed="2025-01-30"),
        # comparative re-report in next year's 10-Q: newer value, but its fiscal
        # context (fy=2026) describes the *filing*, not this period
        _fact("2024-10-01", "2024-12-28", 21.0, fp="Q1", fy=2026, filed="2026-01-30"),
    ]
    rows = xbrl.pick_quarters([facts], instant=False, quarters=8)
    assert len(rows) == 1
    assert rows[0]["value"] == 21.0        # latest filing's (restated) value
    assert rows[0]["period"] == "Q1-2025"  # original filing's fiscal label


def test_pick_quarters_derives_from_ytd_cash_flow_chain():
    # 10-Q cash-flow statements are YTD-only: Q1 discrete, then 6M/9M/FY
    facts = [
        _fact("2025-01-27", "2025-04-27", 10.0, fp="Q1", fy=2026),
        _fact("2025-01-27", "2025-07-27", 25.0, fp="Q2", fy=2026),
        _fact("2025-01-27", "2025-10-26", 45.0, fp="Q3", fy=2026),
        _fact("2025-01-27", "2026-01-25", 70.0, fp="FY", fy=2026),
    ]
    rows = xbrl.pick_quarters([facts], instant=False, quarters=8)
    got = {r["period"]: r for r in rows}
    assert len(rows) == 4
    assert got["Q1-2026"]["value"] == 10.0
    assert got["Q2-2026"]["value"] == 15.0  # 25 - 10
    assert got["Q3-2026"]["value"] == 20.0  # 45 - 25
    assert got["Q4-2026"]["value"] == 25.0  # 70 - 45
    assert got["Q4-2026"]["meta"]["derived"] == "ytd_diff"
    assert got["Q4-2026"]["period_end"] == date(2026, 1, 25)


def test_pick_quarters_concept_priority_first_wins_later_fills():
    primary = [_fact("2024-10-01", "2024-12-28", 20.0, concept="us-gaap:A")]
    fallback = [
        _fact("2024-10-01", "2024-12-28", 999.0, concept="us-gaap:B"),  # overlap: loses
        _fact("2024-12-29", "2025-03-29", 25.0, fp="Q2", concept="us-gaap:B"),  # fills gap
    ]
    rows = xbrl.pick_quarters([primary, fallback], instant=False, quarters=8)
    got = {r["period_end"]: (r["value"], r["meta"]["concept"]) for r in rows}
    assert got[date(2024, 12, 28)] == (20.0, "us-gaap:A")
    assert got[date(2025, 3, 29)] == (25.0, "us-gaap:B")


def test_pick_quarters_instant_balances_fy_labeled_q4():
    facts = [
        _fact(None, "2025-06-28", 5.0, ptype="instant", fp="Q3"),
        _fact(None, "2025-09-27", 6.0, ptype="instant", fp="FY"),
    ]
    rows = xbrl.pick_quarters([facts], instant=True, quarters=8)
    assert [r["period"] for r in rows] == ["Q4-2025", "Q3-2025"]
    assert rows[0]["value"] == 6.0


def test_xbrl_concepts_are_canonical_metrics():
    from xar.ontology.standards import FinMetric

    assert set(xbrl.CONCEPTS) <= {m.value for m in FinMetric}
    assert xbrl.INSTANT_METRICS <= set(xbrl.CONCEPTS)


def test_pull_company_skips_non_us_fast():
    # CN-only filer: returns before edgartools is even imported (no network)
    assert xbrl.pull_company("innolight") == 0
    assert xbrl.pull_company("nonexistent-company") == 0


def test_universe_ticker_map_us_only_normalized():
    m = h13f.universe_ticker_map()
    assert m.get("NVDA") == "nvidia"
    assert m.get("MOG-A")  # share-class ticker kept, normalized
    assert not any("." in t for t in m)  # 300308.SZ / 7011.T etc. excluded


def test_aggregate_infotable_sums_equity_rows_only():
    rows = [
        {"Ticker": "ALLY", "PutCall": "", "Type": "Shares",
         "SharesPrnAmount": 100, "Value": 1000},
        {"Ticker": "ALLY", "PutCall": "", "Type": "Shares",
         "SharesPrnAmount": "2,300", "Value": "23,000"},   # split reporting line
        {"Ticker": "ALLY", "PutCall": "Put", "Type": "Shares",
         "SharesPrnAmount": 999, "Value": 9990},           # option: excluded
        {"Ticker": "BRK.B", "PutCall": "", "Type": "SH",
         "SharesPrnAmount": 10, "Value": 4000},            # class ticker normalized
        {"Ticker": "T", "PutCall": "", "Type": "PRN",
         "SharesPrnAmount": 5, "Value": 500},              # debt principal: excluded
        {"Ticker": None, "PutCall": "", "Type": "Shares",
         "SharesPrnAmount": 5, "Value": 500},              # unresolved CUSIP: excluded
    ]
    agg = h13f.aggregate_infotable(rows)
    assert agg == {"ALLY": (2400.0, 24000.0), "BRK-B": (10.0, 4000.0)}


def test_upsert_holding_targets_unique_key(monkeypatch):
    captured = {}
    monkeypatch.setattr(h13f.db, "execute",
                        lambda sql, params=None: captured.update(sql=sql, params=params))
    h13f.upsert_holding("nvidia", holder="Berkshire Hathaway", holder_cik="1067983",
                        shares=1000.0, value_usd=175000.0,
                        as_of=date(2026, 3, 31), filed_at=date(2026, 5, 15))
    assert "ON CONFLICT (company_id,holder,as_of) DO UPDATE" in captured["sql"]
    assert captured["params"][0] == "nvidia"
    assert captured["params"][5] == date(2026, 3, 31)
    assert captured["params"][7] == h13f.SOURCE


def test_manager_list_is_well_formed():
    names = [n for n, _ in h13f.MANAGERS]
    ciks = [c for _, c in h13f.MANAGERS]
    assert len(h13f.MANAGERS) >= 25
    assert len(set(ciks)) == len(ciks)  # no duplicate CIKs
    assert all(c.isdigit() for c in ciks)
    assert "Berkshire Hathaway" in names
