"""相对主张证据链接 + VP 数值检查器测试(seeded_db + mocked complete_json)。

验证:LLM 道只入合法链接行、无效 ref/target 静默丢弃、重跑幂等(表即游标);
规则道三态(超 bull 阈/破 bear 阈/灰区);router THESIS_LINK 订阅池优先。
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from xar.models import llm
from xar.models.router import TaskClass, resolve
from xar.research import evidence_link
from xar.research.evidence_link import FactLink, FactLinkBatch
from xar.storage import db, structured

_CID = "now"
_TVER = 9991


def _content():
    return {
        "one_liner_zh": "x", "narrative_zh": "y", "stance": "bull", "conviction": 3,
        "pillars": [{"key": "p1", "kind": "demand", "title_zh": "t", "claim_zh": "c",
                     "weight": 1.0, "score": 0.5, "evidence": [], "falsifier_zh": "cRPO 破位"}],
        "risks": [],
        "debates": [{
            "key": "ai_disrupt_vs_empower", "question_zh": "q", "bull_zh": "b", "bear_zh": "be",
            "weight": 0.5, "lean": 0.0, "pillar_keys": ["p1"],
            "verification_points": [{
                "key": "crpo_floor", "question_zh": "q", "metric": "crpo_yoy",
                "bull_reading_zh": "b", "bear_reading_zh": "be", "direction": "higher_is_bull",
                "bull_threshold": 0.20, "bear_threshold": 0.125, "cadence": "quarterly"}]}]}


# 远未来 VP 期(2099):测试自己的 derived crpo_yoy 唯一占据该期,清理只删这行,
# 绝不动开发库真实 derived 数据(评审 #7)。thesis_fact_links 由 company_thesis 版本删除 CASCADE 清理。
_VP_PE = dt.date(2099, 6, 30)


def _teardown():
    db.execute("DELETE FROM company_thesis WHERE company_id=%s AND version=%s", (_CID, _TVER))
    # 事务隔离(_thesis 依赖 isolated_db)下安全清全 'now' 的 semantic_facts 两臂 + 链接:
    # _pending_facts 走 semantic_facts(kg_events + expert_insights),生产 ServiceNow('now')的真实
    # 事件(162+8 行)会被当 pending fact 反复处理致幂等断言红(out2≠0)—— 事务内清干净、只留本测试
    # 自建事件,teardown 整体 rollback、生产行复原、绝不落库(K.3.2 测试隔离)。
    db.execute("DELETE FROM thesis_fact_links WHERE company_id=%s", (_CID,))
    db.execute("DELETE FROM kg_events WHERE company_id=%s", (_CID,))
    db.execute("DELETE FROM expert_insights WHERE company_id=%s", (_CID,))
    db.execute("DELETE FROM fundamentals WHERE company_id=%s AND metric='crpo_yoy' "
               "AND source='derived' AND period_end=%s", (_CID, _VP_PE))


@pytest.fixture()
def _thesis(isolated_db):
    _teardown()
    db.execute(
        "INSERT INTO company_thesis(company_id, version, as_of, stance, conviction, one_liner, content) "
        "VALUES(%s,%s,%s,'bull',3,'x',%s::jsonb)",
        (_CID, _TVER, dt.date(2026, 1, 1), json.dumps(_content())))
    row = db.query("SELECT id FROM company_thesis WHERE company_id=%s AND version=%s", (_CID, _TVER))[0]
    yield {"id": row["id"], "company_id": _CID, "as_of": dt.date(2026, 1, 1), "content": _content()}
    _teardown()


def _event(dedup, summary, polarity="negative"):
    db.execute(
        "INSERT INTO kg_events(company_id, event_type, event_date, polarity, summary, dedup_key) "
        "VALUES(%s,'contract_win',%s,%s,%s,%s) ON CONFLICT (dedup_key) DO NOTHING",
        (_CID, dt.date(2026, 6, 1), polarity, summary, dedup))
    return db.query("SELECT id FROM kg_events WHERE dedup_key=%s", (dedup,))[0]["id"]


def test_link_company_inserts_valid_drops_bogus(_thesis, monkeypatch):
    eid = _event("pytestlink1", "大客户弃用转自研 Agent")
    _event("pytestlink2", "另一条噪音")

    def fake(*a, **k):
        return FactLinkBatch(links=[
            FactLink(ref_id=f"event:{eid}", target_kind="debate",
                     target_key="ai_disrupt_vs_empower", verdict="confirms_bear",
                     strength=0.8, rationale_zh="取消订阅证实颠覆"),
            # 伪 ref → 应被丢弃
            FactLink(ref_id="event:999999", target_kind="debate",
                     target_key="ai_disrupt_vs_empower", verdict="confirms_bull", strength=0.5),
            # 伪 target → 应被丢弃
            FactLink(ref_id=f"event:{eid}", target_kind="pillar",
                     target_key="nonexistent", verdict="confirms", strength=0.5),
        ])
    monkeypatch.setattr(llm, "complete_json", fake)
    out = evidence_link.link_company(_CID, _thesis)
    assert out["links"] == 1
    rows = db.query("SELECT target_key, verdict FROM thesis_fact_links WHERE company_id=%s "
                    "AND origin='llm' AND target_kind='debate' AND target_key<>'none'", (_CID,))
    assert rows == [{"target_key": "ai_disrupt_vs_empower", "verdict": "confirms_bear"}]


def test_link_idempotent_cursor(_thesis, monkeypatch):
    eid = _event("pytestlink1", "事件")
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: FactLinkBatch(links=[
        FactLink(ref_id=f"event:{eid}", target_kind="debate",
                 target_key="ai_disrupt_vs_empower", verdict="confirms_bear", strength=0.7)]))
    evidence_link.link_company(_CID, _thesis)
    n1 = db.query("SELECT count(*) c FROM thesis_fact_links WHERE company_id=%s", (_CID,))[0]["c"]
    # 二次:该事实已链接 → _pending_facts 应为空 → 0 新增
    out2 = evidence_link.link_company(_CID, _thesis)
    n2 = db.query("SELECT count(*) c FROM thesis_fact_links WHERE company_id=%s", (_CID,))[0]["c"]
    assert out2["facts"] == 0 and n1 == n2


def _vp(value):
    structured.upsert_fundamental(_CID, "crpo_yoy", value, period=f"Q-{_VP_PE}",
                                  period_end=_VP_PE, freq="quarter",
                                  unit="ratio", source="derived")


def test_vp_checker_bull(_thesis):
    _vp(0.25)                                  # ≥ 0.20 → confirms_bull
    res = evidence_link.check_verification_points(_CID, _thesis)
    assert res[0]["verdict"] == "confirms_bull"


def test_vp_checker_bear(_thesis):
    _vp(0.10)                                  # ≤ 0.125 → confirms_bear
    res = evidence_link.check_verification_points(_CID, _thesis)
    assert res[0]["verdict"] == "confirms_bear"


def test_vp_checker_gray(_thesis):
    _vp(0.16)                                  # 灰区 → neutral
    res = evidence_link.check_verification_points(_CID, _thesis)
    assert res[0]["verdict"] == "neutral"


def test_vp_rule_refreshes_on_restatement(_thesis):
    # 评审 #3:同期数值重述跨阈 → 规则裁决必须刷新(不能 DO NOTHING 冻结旧值)
    _vp(0.19)                                  # 预披 0.19 → neutral
    evidence_link.check_verification_points(_CID, _thesis)
    _vp(0.21)                                  # 正式重述 0.21(同 period_end)→ 应变 confirms_bull
    evidence_link.check_verification_points(_CID, _thesis)
    rows = db.query("SELECT verdict FROM thesis_fact_links WHERE company_id=%s AND origin='rule' "
                    "AND fact_ref=%s", (_CID, f"crpo_floor:crpo_yoy:{_VP_PE}"))
    assert len(rows) == 1 and rows[0]["verdict"] == "confirms_bull"


def test_two_vps_same_metric_no_collision(_thesis, monkeypatch):
    # 评审 #9:同一争论下两个 VP 引用同一 metric 不应撞唯一键、互相覆盖
    content = _content()
    d = content["debates"][0]
    d["verification_points"].append({
        "key": "crpo_ceiling", "question_zh": "q", "metric": "crpo_yoy",
        "bull_reading_zh": "b", "bear_reading_zh": "be", "direction": "higher_is_bull",
        "bull_threshold": 0.30, "bear_threshold": 0.25, "cadence": "quarterly"})
    row = dict(_thesis, content=content)
    _vp(0.25)   # crpo_floor(0.20/0.125): confirms_bull;crpo_ceiling(0.30/0.25): 0.25→confirms_bear
    evidence_link.check_verification_points(_CID, row)
    rows = db.query("SELECT fact_ref, verdict FROM thesis_fact_links WHERE company_id=%s "
                    "AND origin='rule' ORDER BY fact_ref", (_CID,))
    assert len(rows) == 2
    verdicts = {r["fact_ref"].split(":")[0]: r["verdict"] for r in rows}
    assert verdicts["crpo_floor"] == "confirms_bull"
    assert verdicts["crpo_ceiling"] == "confirms_bear"


def test_router_thesis_link_subscription_first():
    specs = resolve(TaskClass.THESIS_LINK)
    assert specs, "no models resolved for THESIS_LINK"
    # 与 THESIS/EXPERT 同策略:首选订阅池模型(billing == subscription)
    assert specs[0].billing == "subscription"
