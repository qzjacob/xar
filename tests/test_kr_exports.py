"""Offline tests for the semiconductor macro provider (no network, no DB).

Two theme-scope signals in one provider:
  * ``alt.semi_billings``   — SIA/WSTS global monthly sales (keyless). Deterministic
    regex over the canonical press-release sentence captured live from
    semiconductors.org (April 2026 release body).
  * ``alt.kr_chip_exports`` — Korea customs export (env-key + graceful skip). XML
    parse fixture mirrors data.go.kr Itemtrade; no-key path must no-op, never write.
All parsing is pure; fetch + DB write are monkeypatched.
"""
from __future__ import annotations

from datetime import date

from xar.providers.alt import kr_exports

# --- SIA press-release body (verbatim shape from the live April 2026 release) --
SIA_BODY = (
    "WASHINGTON—June 5, 2026—The Semiconductor Industry Association (SIA) "
    "today announced global semiconductor sales were $110.5 billion during the month "
    "of April 2026, an increase of 11% compared to the March 2026 total of $99.5 "
    "billion and 93.9% more than the April 2025 total of $56.9 billion. Monthly sales "
    "are compiled by the World Semiconductor Trade Statistics (WSTS) organization and "
    "represent a three-month moving average."
)
SIA_BODY_HTML = f"<html><body><p>{SIA_BODY}</p></body></html>"

# A decrease month (sign handling)
SIA_BODY_DOWN = (
    "global semiconductor sales were $88.8 billion during the month of February 2026, "
    "a decrease of 3.2% compared to the January 2026 total of $91.7 billion and 5.5% "
    "less than the February 2025 total of $94.0 billion."
)

LISTING_HTML = """
<a href="https://www.semiconductors.org/global-semiconductor-sales-increase-11-month-to-month-in-april/">April</a>
<a href="https://www.semiconductors.org/global-semiconductor-sales-increase-substantially-in-february/">Feb</a>
<a href="https://www.semiconductors.org/global-semiconductor-sales-increase-25-from-q4-2025-to-q1-2026/">Q1 quarterly</a>
<a href="https://www.semiconductors.org/global-semiconductor-sales-increase-11-month-to-month-in-april/">dup</a>
<a href="https://www.semiconductors.org/some-policy-post/">unrelated</a>
"""

# data.go.kr Itemtrade (관세청_품목별 수출입실적) response shape
KR_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
 <header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
 <body><items>
  <item><hsCd>8542</hsCd><statKor>전자집적회로</statKor>
   <year>2026</year><expDlr>11234567</expDlr><impDlr>4000000</impDlr>
   <balPayments>7234567</balPayments></item>
  <item><hsCd>8542</hsCd><statKor>총계</statKor>
   <year>2026</year><expDlr>13868765</expDlr><impDlr>5000000</impDlr>
   <balPayments>8868765</balPayments></item>
 </items></body>
</response>"""


# --- SIA parsing -------------------------------------------------------------
def test_parse_billing_from_html():
    r = kr_exports.parse_billing(SIA_BODY_HTML)
    assert r is not None
    assert r["billions"] == 110.5
    assert r["value_usd"] == 110.5e9
    assert r["period_end"] == date(2026, 4, 30)  # month-end
    assert r["month"] == 4 and r["year"] == 2026
    assert r["mom_pct"] == 11.0
    assert r["yoy_pct"] == 93.9


def test_parse_billing_decrease_sign():
    r = kr_exports.parse_billing(SIA_BODY_DOWN)
    assert r["period_end"] == date(2026, 2, 28)
    assert r["mom_pct"] == -3.2   # "a decrease of 3.2%"
    assert r["yoy_pct"] == -5.5   # "5.5% less than"


def test_parse_billing_rejects_non_monthly():
    # quarterly / unrelated bodies lack the "during the month of $XX billion" anchor
    assert kr_exports.parse_billing("Sales rose 2.5% from Q4 2025 to Q1 2026.") is None
    assert kr_exports.parse_billing("this is not a sales release") is None


def test_billing_urls_filters_monthly_only():
    urls = kr_exports.billing_urls(LISTING_HTML)
    assert len(urls) == 2  # april + february, dedup'd, quarterly + unrelated excluded
    assert all("global-semiconductor-sales" in u for u in urls)
    assert all("from-q" not in u for u in urls)
    assert urls[0].endswith("month-to-month-in-april/")


# --- Korea Itemtrade parsing -------------------------------------------------
def test_parse_itemtrade_rows():
    rows = kr_exports.parse_itemtrade(KR_XML)
    assert len(rows) == 2
    assert rows[0]["hs"] == "8542" and rows[0]["exp_usd"] == 11234567.0
    assert rows[1]["stat"].startswith("총")  # 총계


def test_select_semi_export_prefers_total_row():
    rows = kr_exports.parse_itemtrade(KR_XML)
    # 총계(total) row wins even though it isn't the first item
    assert kr_exports.select_semi_export(rows) == 13868765.0


def test_parse_itemtrade_garbage_is_empty():
    assert kr_exports.parse_itemtrade("not xml at all") == []


# --- pull_semi_billings (fetch + upsert stubbed) -----------------------------
def test_pull_semi_billings_writes_theme_rows(monkeypatch):
    writes = []

    def fake_fetch(url, host, params=None):
        return LISTING_HTML if url == kr_exports._SIA_LIST_URL else SIA_BODY_HTML

    monkeypatch.setattr(kr_exports, "_fetch", fake_fetch)
    monkeypatch.setattr(kr_exports, "upsert_signal",
                        lambda key, **kw: writes.append((key, kw)))

    stats = kr_exports.pull_semi_billings(limit=5)
    assert stats["listing_ok"] and stats["candidates"] == 2
    # both candidate pages return the SAME April body -> second is a period dup
    assert stats["written"] == 1 and stats["months"] == ["2026-04-30"]
    key, kw = writes[0]
    assert key == "alt.semi_billings"
    assert kw["company_id"] is None and kw["theme"] == "ai_chip"
    assert kw["value"] == 110.5e9 and kw["unit"] == "USD"
    assert kw["source"] == "kr_exports"
    assert kw["period_end"] == date(2026, 4, 30)
    assert kw["meta"]["billions"] == 110.5 and kw["meta"]["yoy_pct"] == 93.9


def test_pull_semi_billings_listing_down_is_empty(monkeypatch):
    monkeypatch.setattr(kr_exports, "_fetch", lambda *a, **k: None)
    monkeypatch.setattr(kr_exports, "upsert_signal",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no write")))
    stats = kr_exports.pull_semi_billings()
    assert stats["listing_ok"] is False and stats["written"] == 0


# --- pull_kr_exports (env-key graceful skip) ---------------------------------
def test_kr_exports_no_key_skips(monkeypatch):
    for env in kr_exports._KR_KEY_ENVS:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(kr_exports, "_fetch",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fetch")))
    monkeypatch.setattr(kr_exports, "upsert_signal",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no write")))
    stats = kr_exports.pull_kr_exports()
    assert stats["written"] == 0 and "skipped" in stats
    assert "KR_DATA_API_KEY" in stats["skipped"]


def test_kr_exports_with_key_writes_two_theme_rows(monkeypatch):
    monkeypatch.setenv("KR_DATA_API_KEY", "dummy-token")
    monkeypatch.setattr(kr_exports, "_fetch", lambda url, host, params=None: KR_XML)
    writes = []
    monkeypatch.setattr(kr_exports, "upsert_signal", lambda key, **kw: writes.append(kw))

    stats = kr_exports.pull_kr_exports(limit=1)
    assert stats["written"] == 1  # one month
    # spec themes = ai_chip + ai_optical -> two theme rows per month
    themes = {w["theme"] for w in writes}
    assert themes == {"ai_chip", "ai_optical"}
    assert all(w["value"] == 13868765.0 and w["company_id"] is None for w in writes)
    assert all(w["source"] == "kr_exports" and w["unit"] == "USD" for w in writes)


def test_recent_months_walks_backwards():
    ms = kr_exports._recent_months(3, today=date(2026, 2, 15))
    assert ms == [(2026, 1), (2025, 12), (2025, 11)]


# --- combined pull -----------------------------------------------------------
def test_pull_combines_both(monkeypatch):
    monkeypatch.setattr(kr_exports, "pull_semi_billings", lambda limit=None: {"written": 3})
    monkeypatch.setattr(kr_exports, "pull_kr_exports", lambda limit=None: {"written": 2})
    stats = kr_exports.pull()
    assert stats["written"] == 5
    assert stats["semi_billings"] == {"written": 3}
    assert stats["kr_chip_exports"] == {"written": 2}
