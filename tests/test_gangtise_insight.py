"""Gangtise open-insight 抓取器离线测试(monkeypatch client;seeded_db for save/ratings)。

验证:securityList 反解 registry(数字段+交易所双匹配防 HK 撞车)、13 位 ms 解析、
研报/纪要落库 doc_id 稳定、expert vs meeting 分流、零 LLM 评级第二遍聚合、clue 目标去重。
远未来日期(2099)隔离,清理只删测试行,绝不动开发库真实数据。
"""
from __future__ import annotations

import datetime as dt

import pytest

from xar.providers.gangtise import client, insight
from xar.storage import db

_CID = "innolight"            # registry: tickers ['300308.SZ']
_MS = int(dt.datetime(2099, 6, 30, tzinfo=dt.timezone.utc).timestamp() * 1000)


def test_company_for_security_reverse_resolve():
    assert insight._company_for_security({"securityCode": "300308.SZ"}) == _CID
    # 数字撞车防护:9999.SS(A股)不得错配到 09999.HK
    assert insight._key("9999.SS") != insight._key("09999.HK")


def test_pub_ms_parse():
    d = insight._pub(_MS)
    assert d is not None and d.year == 2099
    assert insight._pub("2099-06-30").year == 2099
    assert insight._pub(None) is None
    # 评审 #10:8 位 yyyyMMdd 不能当 Unix 秒(否则 1970)
    p = insight._pub(20990331)
    assert p is not None and p.year == 2099 and p.month == 3 and p.day == 31
    assert insight._pub("2099/06/30").year == 2099


def test_company_for_security_tolerates_non_dict():
    # 评审 #11:securityList 元素可能是代码字符串或非法值——不炸
    assert insight._company_for_security("300308.SZ") == _CID
    assert insight._company_for_security(123) is None


def _page(rows):
    def fake_pages(url, payload, **kw):
        yield rows
    return fake_pages


@pytest.fixture()
def _clean(seeded_db):
    def wipe():
        db.execute("DELETE FROM documents WHERE source='gangtise' AND id LIKE 'gangtise:%%test%%'")
        db.execute("DELETE FROM documents WHERE id IN ('gangtise:report:RT1:innolight',"
                   "'gangtise:report:RTF1:innolight',"
                   "'gangtise:summary:ST1:innolight','gangtise:summary:SE1:innolight')")
        db.execute("DELETE FROM analyst_ratings WHERE company_id=%s AND source='gangtise' "
                   "AND as_of >= '2099-01-01'", (_CID,))
    wipe()
    yield
    wipe()


def test_pull_broker_reports_saves_doc(_clean, monkeypatch):
    row = {"reportId": "RT1", "title": "中际旭创深度", "brief": "1.6T 放量,份额稳固",
           "publishTime": _MS, "securityList": [{"securityCode": "300308.SZ"}],
           "category": "company", "llmTagList": ["inDepth"], "rating": "买入", "targetPrice": 180}
    monkeypatch.setattr(client, "pages", _page([row]))
    out = insight.pull_broker_reports(start_ms=_MS - 10**8, end_ms=_MS)
    assert out["saved"] == 1
    d = db.query("SELECT company_id, doc_type, text, meta FROM documents "
                 "WHERE id='gangtise:report:RT1:innolight'")
    assert d and d[0]["company_id"] == _CID and d[0]["doc_type"] == "broker_report"
    assert "1.6T" in d[0]["text"]


def test_pull_broker_reports_for_attributes_to_queried_company(_clean, monkeypatch):
    # 真机:broker feed 的 securityList 为空 → 按公司查时直接归属被查询 cid(不靠反解)
    row = {"reportId": "RTF1", "title": "中际旭创点评", "brief": "1.6T", "publishTime": _MS,
           "securityList": [], "rating": "增持", "targetPrice": 200}
    monkeypatch.setattr(client, "pages", _page([row]))
    monkeypatch.setattr("xar.providers.gangtise.gts_code",
                        lambda cid: "300308.SZ" if cid == _CID else None)
    out = insight.pull_broker_reports_for([_CID], start_ms=_MS - 10**8, end_ms=_MS)
    assert out["saved"] == 1
    d = db.query("SELECT company_id, doc_type FROM documents WHERE id='gangtise:report:RTF1:innolight'")
    assert d and d[0]["company_id"] == _CID and d[0]["doc_type"] == "broker_report"


def test_non_covered_report_skipped(_clean, monkeypatch):
    # 评审 #3:securityList 无 registry 命中的报告不落库
    row = {"reportId": "RT9", "title": "某未覆盖小盘股", "brief": "x", "publishTime": _MS,
           "securityList": [{"securityCode": "000001.SZ"}]}   # 平安银行,不在 registry
    monkeypatch.setattr(client, "pages", _page([row]))
    out = insight.pull_broker_reports(start_ms=_MS - 10**8, end_ms=_MS)
    assert out["seen"] == 1 and out["saved"] == 0


def test_pull_minutes_expert_vs_meeting(_clean, monkeypatch):
    rows = [
        {"summaryId": "ST1", "title": "业绩说明会纪要", "brief": "毛利率企稳", "publishTime": _MS,
         "securityList": [{"securityCode": "300308.SZ"}], "participantRoleList": ["management"],
         "essence": [{"content": "Q2 出货环比+20%"}]},
        {"summaryId": "SE1", "title": "专家交流:光模块景气", "brief": "北美需求强", "publishTime": _MS,
         "securityList": [{"securityCode": "300308.SZ"}], "participantRoleList": ["expert"],
         "guest": "某产业专家", "essence": [{"content": "1.6T 良率爬坡顺利"}]},
    ]
    monkeypatch.setattr(client, "pages", _page(rows))
    out = insight.pull_minutes(start_ms=_MS - 10**8, end_ms=_MS)
    assert out["saved"] == 2
    types = {r["id"]: r["doc_type"] for r in db.query(
        "SELECT id, doc_type FROM documents WHERE id IN "
        "('gangtise:summary:ST1:innolight','gangtise:summary:SE1:innolight')")}
    assert types["gangtise:summary:ST1:innolight"] == "meeting_minutes"
    assert types["gangtise:summary:SE1:innolight"] == "expert_minutes"


def test_parse_broker_ratings(_clean, monkeypatch):
    monkeypatch.setattr(client, "pages", _page([
        {"reportId": "RT1", "title": "t", "brief": "b", "publishTime": _MS,
         "securityList": [{"securityCode": "300308.SZ"}], "rating": "买入", "targetPrice": 180}]))
    insight.pull_broker_reports(start_ms=_MS - 10**8, end_ms=_MS)
    out = insight.parse_broker_ratings(_CID)
    assert out["companies_days"] >= 1
    r = db.query("SELECT buy, pt_mean FROM analyst_ratings WHERE company_id=%s AND source='gangtise' "
                 "AND as_of='2099-06-30'", (_CID,))
    assert r and r[0]["buy"] == 1 and abs(float(r[0]["pt_mean"]) - 180.0) < 1e-6


def test_pull_clues_targets(monkeypatch, seeded_db):
    rows = [{"securityCode": "300308.SZ", "source": "研报", "title": "x"},
            {"securityCode": "300308.SZ", "source": "研报", "title": "y"},   # 同 (cid,src) 去重
            {"securityCode": "300502.SZ", "source": "电话会议纪要", "title": "z"}]
    monkeypatch.setattr(client, "post", lambda url, payload, **kw: {"list": rows})
    out = insight.pull_clues(start_ms=_MS - 10**8, end_ms=_MS)
    assert out["rows"] == 3
    assert (_CID, "研报") in out["targets"]
    assert len(out["targets"]) == 2               # (innolight,研报) 去重 + (eoptolink,纪要)
