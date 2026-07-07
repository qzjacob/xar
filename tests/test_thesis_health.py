"""health_v3(争论感知健康度)测试(seeded_db,纯插行零 LLM)。

验证:bear 链接 + 破 bear 阈 VP → 争论 flipped + overall challenged + 进 challenged_companies_v2;
无 debates 旧论点 → v3 形状退化(debates==[]);支柱 LLM 链接只做升降级(net 证伪 quiet→challenging,
confirms 不改分——防与事件桶双计)。
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from xar.research import thesis_health
from xar.storage import db

_CID = "now"
_VER = 9993


def _content(*, debates, pillars=None):
    return {
        "one_liner_zh": "x", "narrative_zh": "y", "stance": "bull", "conviction": 3,
        "pillars": pillars or [{"key": "p1", "kind": "demand", "title_zh": "t", "claim_zh": "c",
                                "weight": 0.6, "score": 0.5, "evidence": [], "watch_event_types": []}],
        "risks": [], "debates": debates}


def _debate():
    return {"key": "ai_disrupt_vs_empower", "question_zh": "q", "bull_zh": "b", "bear_zh": "be",
            "weight": 0.5, "lean": 0.5, "pillar_keys": ["p1"],  # 作者态偏多(+0.5)
            "verification_points": [{"key": "crpo_floor", "question_zh": "q", "metric": "crpo_yoy",
                                     "bull_reading_zh": "b", "bear_reading_zh": "be",
                                     "direction": "higher_is_bull", "bull_threshold": 0.20,
                                     "bear_threshold": 0.125, "cadence": "quarterly"}]}


def _mk_thesis(content) -> int:
    db.execute("DELETE FROM company_thesis WHERE company_id=%s AND version=%s", (_CID, _VER))
    db.execute(
        "INSERT INTO company_thesis(company_id, version, as_of, stance, conviction, one_liner, content) "
        "VALUES(%s,%s,%s,'bull',3,'x',%s::jsonb)",
        (_CID, _VER, dt.date(2026, 1, 1), json.dumps(content)))
    return db.query("SELECT id FROM company_thesis WHERE company_id=%s AND version=%s",
                    (_CID, _VER))[0]["id"]


def _link(tid, target_kind, target_key, verdict, strength, *, origin="llm", fact="event:1"):
    kind, ref = fact.split(":", 1)
    db.execute(
        "INSERT INTO thesis_fact_links(thesis_id, company_id, fact_kind, fact_ref, target_kind, "
        "target_key, verdict, strength, origin, as_of) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT DO NOTHING",
        (tid, _CID, kind, ref, target_kind, target_key, verdict, strength, origin,
         dt.date(2026, 6, 1)))


@pytest.fixture()
def _clean(seeded_db):
    yield
    db.execute("DELETE FROM company_thesis WHERE company_id=%s AND version=%s", (_CID, _VER))
    # thesis_fact_links CASCADE 随 company_thesis 删除


def test_debate_flip_and_overall_challenged(_clean):
    tid = _mk_thesis(_content(debates=[_debate()]))
    for i in range(4):                                  # 4 条 bear 链接 → llm_score≈-0.8
        _link(tid, "debate", "ai_disrupt_vs_empower", "confirms_bear", 0.8, fact=f"event:{i}")
    _link(tid, "debate", "ai_disrupt_vs_empower", "confirms_bear", 1.0,
          origin="rule", fact="fundamental:crpo_yoy:2025-06-30")   # VP 破 bear 阈
    h = thesis_health.health_v3(_CID)
    d = h["debates"][0]
    assert d["status"] == "flipped", d
    assert d["lean_now"] < -0.3 and d["lean_authored"] == 0.5
    assert h["overall"] == "challenged" and h["debate_challenged"] is True
    assert _CID in thesis_health.challenged_companies_v2(limit=200)


def test_legacy_thesis_shape(_clean):
    _mk_thesis(_content(debates=[]))
    h = thesis_health.health_v3(_CID)
    assert h["debates"] == []
    assert h["debate_challenged"] is False
    assert "pillars" in h and h["version"] == "v3"


def test_zero_weight_debate_does_not_challenge(_clean):
    # 评审 #9:显式 weight=0(作者判无关)不应被 or 吞成 0.5 而通过翻转 gate
    d = _debate()
    d["weight"] = 0.0
    tid = _mk_thesis(_content(debates=[d]))
    for i in range(4):
        _link(tid, "debate", "ai_disrupt_vs_empower", "confirms_bear", 0.8, fact=f"event:{i}")
    h = thesis_health.health_v3(_CID)
    dh = h["debates"][0]
    assert dh["weight"] == 0.0
    # 天平仍可 flipped(状态是描述性的),但不应把 overall 拉成 challenged(权重<0.3)
    assert h["debate_challenged"] is False
    assert _CID not in thesis_health.challenged_companies_v2(limit=200)


def test_pillar_link_escalates_only_on_falsify(_clean):
    pillars = [
        {"key": "p1", "kind": "demand", "title_zh": "t", "claim_zh": "c", "weight": 0.5,
         "score": 0.5, "evidence": [], "watch_event_types": []},
        {"key": "p2", "kind": "moat", "title_zh": "t", "claim_zh": "c", "weight": 0.5,
         "score": 0.5, "evidence": [], "watch_event_types": []},
    ]
    tid = _mk_thesis(_content(debates=[], pillars=pillars))
    _link(tid, "pillar", "p2", "falsifies", 0.8, fact="event:10")   # net 证伪 → 提级
    _link(tid, "pillar", "p1", "confirms", 0.9, fact="event:11")    # confirms → 不改分
    h = thesis_health.health_v3(_CID)
    st = {p["key"]: p["status"] for p in h["pillars"]}
    assert st["p2"] == "challenging"          # quiet → challenging(net 证伪提级)
    assert st["p1"] == "quiet"                # confirms 不把 quiet 变成别的
    assert h["overall"] == "challenged"
