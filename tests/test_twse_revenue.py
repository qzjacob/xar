"""Offline tests for the 台股月营收 provider (no network, no DB).

Pure ROC-date / number / field-alias parse layer is exercised with dict
fixtures shaped like the real TWSE ``t187ap05_L`` records (plus a synthetic
TPEx-style alias record); pull() orchestration runs with the HTTP + bindings +
upsert seams monkeypatched.
"""
from __future__ import annotations

from datetime import date

from xar.ontology.altdata import SIGNALS_BY_KEY
from xar.providers.alt import twse_revenue as tr

# Real-shaped TWSE 上市 record (fields verified live 2026-07): TSMC May-2026.
REC_TSMC = {
    "出表日期": "1150617", "資料年月": "11505",
    "公司代號": "2330", "公司名稱": "台積電", "產業別": "半導體業",
    "營業收入-當月營收": "416975163", "營業收入-上月營收": "410725118",
    "營業收入-去年當月營收": "320515951",
    "營業收入-上月比較增減(%)": "1.5217099529812539",
    "營業收入-去年同月增減(%)": "30.09498020271696",
    "累計營業收入-當月累計營收": "1961803721",
    "累計營業收入-去年累計營收": "1509336555",
    "累計營業收入-前期比較增減(%)": "29.977884289697073",
    "備註": "-",
}
# A company NOT in our bindings -> parsed but not matched/written.
REC_UNBOUND = {**REC_TSMC, "公司代號": "1101", "公司名稱": "台泥"}
# Missing revenue -> parse_record returns None (skipped).
REC_NO_REV = {"資料年月": "11505", "公司代號": "9999", "營業收入-當月營收": "-"}
# Synthetic TPEx-style record using romanized alias keys (alias tolerance).
REC_TPEX_ALIAS = {
    "SecuritiesCompanyCode": "3081", "CompanyName": "聯亞",
    "DataYearMonth": "11505", "Operatingincome": "1,234,567",
    "YoY": "12.5", "ReportDate": "1150617",
}


def test_signal_spec_is_company_monthly_twd():
    spec = SIGNALS_BY_KEY[tr._KEY]
    assert spec.scope == "company"
    assert spec.cadence == "monthly"
    assert spec.unit == "TWD"
    assert spec.source == "twse_revenue" == tr.pull.__module__.split(".")[-1]


def test_available_is_keyless():
    assert tr.available() is True


# --- pure helpers ------------------------------------------------------------
def test_num_strips_commas_and_dashes():
    assert tr._num("1,234,567") == 1234567.0
    assert tr._num("30.0949") == 30.0949
    assert tr._num("-") is None
    assert tr._num("") is None
    assert tr._num(None) is None
    assert tr._num("n/a") is None


def test_roc_period_end_is_month_end():
    # ROC 115 -> 2026; 05 -> May; month-end = 2026-05-31.
    assert tr._roc_period_end("11505") == date(2026, 5, 31)
    # Feb of a leap year (ROC 113 = 2024) -> 29th.
    assert tr._roc_period_end("11302") == date(2024, 2, 29)
    # ROC 99 -> 2010, month 12.
    assert tr._roc_period_end("9912") == date(2010, 12, 31)


def test_roc_period_end_rejects_garbage():
    assert tr._roc_period_end(None) is None
    assert tr._roc_period_end("abc") is None
    assert tr._roc_period_end("11513") is None  # month 13
    assert tr._roc_period_end("115") is None     # too short


def test_field_alias_first_nonempty():
    assert tr._field({"公司代號": "2330"}, tr._F_CODE) == "2330"
    assert tr._field({"Code": "3081"}, tr._F_CODE) == "3081"
    assert tr._field({"公司代號": "-"}, tr._F_CODE) is None
    assert tr._field({}, tr._F_CODE) is None


# --- parse_record ------------------------------------------------------------
def test_parse_record_tsmc_thousands_to_raw_twd():
    row = tr.parse_record(REC_TSMC)
    assert row["code"] == "2330"
    assert row["name"] == "台積電"
    assert row["period_end"] == date(2026, 5, 31)
    # 416,975,163 仟元 -> raw TWD.
    assert row["value_twd"] == 416975163 * 1000
    assert row["yoy_pct"] == 30.09498020271696
    assert round(row["mom_pct"], 4) == 1.5217
    assert row["ytd_twd"] == 1961803721 * 1000
    assert round(row["ytd_yoy_pct"], 4) == 29.9779
    assert row["data_ym"] == "11505"
    assert row["report_date"] == "1150617"


def test_parse_record_alias_keys():
    row = tr.parse_record(REC_TPEX_ALIAS)
    assert row["code"] == "3081"
    assert row["value_twd"] == 1234567 * 1000
    assert row["yoy_pct"] == 12.5
    assert row["ytd_twd"] is None  # no cumulative field present


def test_parse_record_none_on_missing_revenue():
    assert tr.parse_record(REC_NO_REV) is None
    assert tr.parse_record({"公司代號": "2330"}) is None  # no ym / rev
    assert tr.parse_record("not-a-dict") is None


# --- pull end-to-end (HTTP + bindings + upsert seams stubbed) ----------------
class _Binding:
    def __init__(self, code):
        self.tw_code = code


def _stub_bindings():
    return {"tsmc": _Binding("2330"), "lianya": _Binding("3081"),
            "nocode": _Binding(None)}


def test_pull_matches_bound_and_writes_raw_twd(monkeypatch):
    monkeypatch.setattr(tr, "bindings", _stub_bindings)
    # TWSE returns TSMC + an unbound name; TPEx returns the alias record.
    feeds = {"openapi.twse.com.tw": [REC_TSMC, REC_UNBOUND],
             "www.tpex.org.tw": [REC_TPEX_ALIAS]}
    monkeypatch.setattr(tr, "_fetch", lambda url, host: feeds.get(host))
    writes = []
    monkeypatch.setattr(tr, "upsert_signal", lambda key, **kw: writes.append((key, kw)))

    stats = tr.pull()
    assert stats["bound_companies"] == 2          # nocode dropped
    assert stats["sources_ok"] == ["TWSE", "TPEx"]
    assert stats["sources_failed"] == []
    assert stats["matched"] == 2                  # 2330 + 3081 (1101 parsed, unmatched)
    assert stats["written"] == 2
    assert stats["companies_matched"] == 2
    assert sorted(stats["companies"]) == ["lianya", "tsmc"]

    by_cid = {kw["company_id"]: (key, kw) for key, kw in writes}
    key, kw = by_cid["tsmc"]
    assert key == "alt.tw_monthly_revenue"
    assert kw["value"] == 416975163 * 1000
    assert kw["unit"] == "TWD" and kw["source"] == "twse_revenue"
    assert kw["period_end"] == date(2026, 5, 31)
    assert kw["meta"]["yoy_pct"] == 30.09498020271696
    assert kw["meta"]["market"] == "TWSE"
    assert kw["meta"]["tw_code"] == "2330"
    assert "raw TWD" in kw["meta"]["unit_note"]
    assert by_cid["lianya"][1]["meta"]["market"] == "TPEx"


def test_pull_skips_failed_source_without_raising(monkeypatch):
    monkeypatch.setattr(tr, "bindings", _stub_bindings)
    # TWSE ok, TPEx down (None) -> logged + skipped, not raised.
    feeds = {"openapi.twse.com.tw": [REC_TSMC], "www.tpex.org.tw": None}
    monkeypatch.setattr(tr, "_fetch", lambda url, host: feeds.get(host))
    monkeypatch.setattr(tr, "upsert_signal", lambda key, **kw: None)

    stats = tr.pull()
    assert stats["sources_ok"] == ["TWSE"]
    assert stats["sources_failed"] == ["TPEx"]
    assert stats["written"] == 1


def test_pull_limit_caps_total_writes(monkeypatch):
    monkeypatch.setattr(tr, "bindings", _stub_bindings)
    feeds = {"openapi.twse.com.tw": [REC_TSMC, REC_UNBOUND],
             "www.tpex.org.tw": [REC_TPEX_ALIAS]}
    monkeypatch.setattr(tr, "_fetch", lambda url, host: feeds.get(host))
    calls = []
    monkeypatch.setattr(tr, "_fetch",
                        lambda url, host: calls.append(host) or feeds.get(host))
    monkeypatch.setattr(tr, "upsert_signal", lambda key, **kw: None)

    stats = tr.pull(limit=1)
    assert stats["written"] == 1
    # limit already met after TWSE -> TPEx source is never fetched.
    assert calls == ["openapi.twse.com.tw"]


def test_pull_swallows_upsert_errors(monkeypatch):
    monkeypatch.setattr(tr, "bindings", _stub_bindings)
    monkeypatch.setattr(tr, "_fetch",
                        lambda url, host: [REC_TSMC] if "twse" in host else None)

    def _raise(key, **kw):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(tr, "upsert_signal", _raise)
    stats = tr.pull()  # must not raise
    assert stats["written"] == 0
