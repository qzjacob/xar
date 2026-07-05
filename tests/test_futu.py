"""富途接入:代码转换、指标映射、新闻时间解析、板块→本体映射、资金流回退、绑定。
全离线(不需 OpenD);ctx 相关逻辑用纯函数覆盖。"""
from __future__ import annotations

import pytest


# ── ticker → Futu code ─────────────────────────────────────────────────────────
def test_code_from_tickers_hk_zero_pad():
    from xar.providers.futu import code_from_tickers

    assert code_from_tickers(["0981.HK"]) == "HK.00981"   # 4-digit → 5-digit pad
    assert code_from_tickers(["00700.HK"]) == "HK.00700"
    assert code_from_tickers(["9660.HK"]) == "HK.09660"


def test_code_from_tickers_cn_and_us():
    from xar.providers.futu import code_from_tickers

    assert code_from_tickers(["600519.SS"]) == "SH.600519"
    assert code_from_tickers(["600519.SH"]) == "SH.600519"
    assert code_from_tickers(["300750.SZ"]) == "SZ.300750"
    assert code_from_tickers(["NVDA"]) == "US.NVDA"        # US: plain upper symbol
    assert code_from_tickers([]) is None
    assert code_from_tickers(["ignored-lowercase"]) is None


def test_code_prefers_hk_cn_over_us():
    from xar.providers.futu import code_from_tickers

    # a dual-listed name with both → HK/CN wins (Futu's edge)
    assert code_from_tickers(["TSM", "2330.TW"]) == "US.TSM"      # TW not addressed here → US
    assert code_from_tickers(["BABA", "9988.HK"]) == "HK.09988"   # HK preferred


# ── metric map ─────────────────────────────────────────────────────────────────
def test_futu_snapshot_metric_map():
    from xar.ontology.standards import FinMetric, canonical_metric

    assert canonical_metric("futu", "pe_ttm_ratio") == FinMetric.PE.value
    assert canonical_metric("futu", "pb_ratio") == FinMetric.PB.value
    assert canonical_metric("futu", "total_market_val") == FinMetric.MARKET_CAP.value
    assert canonical_metric("futu", "nonsense_field") is None


def test_num_handles_na():
    from xar.providers.futu import _num

    assert _num("N/A") is None
    assert _num("") is None
    assert _num(None) is None
    assert _num(1.5) == 1.5
    assert _num("2.5") == 2.5


# ── news time parsing (M/D no year, future → last year, ISO) ────────────────────
def test_parse_news_time():
    from xar.providers import futu

    assert futu._parse_news_time("2026-06-30 14:22").startswith("2026-06-30 14:22")
    assert futu._parse_news_time("2026-06-30") == "2026-06-30 00:00:00"
    assert futu._parse_news_time("") is None
    # 'M/D' with no year → this year; a future month rolls back a year
    from datetime import datetime
    y = datetime.now().year
    jan = futu._parse_news_time("1/2")
    assert jan and jan.startswith(str(y)) or jan.startswith(str(y - 1))


# ── plate → ontology (reuse cn_routing; keys must be real themes) ───────────────
def test_plate_themes_via_cn_routing():
    from xar.ingestion.registry import THEMES
    from xar.ontology import futu_plates

    assert "ai_chip" in futu_plates.plate_themes(["半导体", "芯片股"])
    assert futu_plates.plate_themes(["白酒Ⅱ", "换电概念"]) == []   # 本体外 → 空,合理
    for th in futu_plates.plate_themes(["半导体", "光模块", "机器人概念股"]):
        assert th in THEMES                                       # 代码即真相守卫


# ── capital-flow 主力回退(港股 main_in_flow 常为 N/A → 超大单+大单)────────────
def test_main_inflow_fallback():
    from xar.providers.alt.futu_flow import _ccy, _main_inflow

    assert _main_inflow({"main_in_flow": 123.0}) == 123.0
    assert _main_inflow({"main_in_flow": "N/A", "super_in_flow": 100.0,
                         "big_in_flow": 50.0}) == 150.0
    assert _main_inflow({"main_in_flow": "N/A", "super_in_flow": "N/A",
                         "big_in_flow": "N/A"}) is None
    assert _ccy("HK.00700") == "HKD"
    assert _ccy("SH.600519") == "CNY"
    assert _ccy("US.NVDA") == "USD"


# ── alt binding: any HK/CN/US ticker → futu capital-flow signal ─────────────────
def test_altbinding_futu_signal():
    from xar.ontology.altdata import AltBinding

    b = AltBinding(company_id="x", futu_code="HK.00981")
    assert "alt.futu_main_capital_flow" in b.signals()
    assert AltBinding(company_id="y").signals() == ()   # no code → no signal


def test_futu_flow_spec_registered():
    from xar.ontology.altdata import SIGNALS_BY_KEY
    from xar.ontology.thesis import PILLAR_KINDS

    spec = SIGNALS_BY_KEY["alt.futu_main_capital_flow"]
    assert spec.scope == "company" and spec.good_when == "rising"
    assert all(k in PILLAR_KINDS for k in spec.pillar_kinds)   # demand/valuation valid


# ── ontology-gap query (needs a seeded futu_plates row) ─────────────────────────
@pytest.fixture
def seeded(seeded_db):
    return seeded_db


def test_plate_theme_gaps(seeded):
    from xar.providers import futu
    from xar.storage import db

    cid = db.query("SELECT id FROM companies LIMIT 1")[0]["id"]
    db.execute("DELETE FROM futu_plates WHERE company_id=%s", (cid,))
    # a plate implying a theme the company is (very likely) not curated for
    db.execute("INSERT INTO futu_plates(company_id,plate_id,plate_name,plate_type,themes) "
               "VALUES(%s,'PL.TEST','测试板块','CONCEPT',%s)",
               (cid, ["restaurants"]))
    gaps = futu.plate_theme_gaps(limit=50)
    hit = [g for g in gaps if g["company_id"] == cid]
    # only a gap if 'restaurants' isn't already curated for that company
    curated = db.query("SELECT themes FROM companies WHERE id=%s", (cid,))[0]["themes"] or []
    if "restaurants" not in curated:
        assert hit and "restaurants" in hit[0]["futu_implied"]
    db.execute("DELETE FROM futu_plates WHERE company_id=%s", (cid,))
