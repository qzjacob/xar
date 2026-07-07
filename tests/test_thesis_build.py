"""Thesis 生成管线的争论接线测试(seeded_db + mocked complete_json)。

不打真 LLM:mock dossier(受控事实包)+ mock complete_json(返回罐装 CompanyThesis),
验证 build 对争论种子的处理:落库 debate: slot 证据、缺种子被拒、假 VP metric 被拒;
并验证 dossier 注入种子块(monkeypatch 合成种子,不依赖策展内容)。
"""
from __future__ import annotations

import pytest

from xar.models import llm
from xar.ontology.debates import DebateSeed
from xar.ontology.thesis import CompanyThesis
from xar.research import thesis
from xar.storage import db

_CID = "now"
_SEED_KEY = "ai_disrupt_vs_empower"


def _mk_thesis(*, debates=None) -> CompanyThesis:
    def _pillar(key, w):
        return dict(key=key, kind="demand", title_zh="标题", claim_zh="主张 增速>10%",
                    weight=w, score=0.5,
                    evidence=[dict(kind="registry", ref_id="reg:x", quote="q")])
    return CompanyThesis.model_validate(dict(
        one_liner_zh="一句话", narrative_zh="叙事", stance="bull", conviction=3,
        pillars=[_pillar("p1", 0.34), _pillar("p2", 0.33), _pillar("p3", 0.33)],
        bull_case_zh="多", bear_case_zh="空",
        risks=[dict(type="demand", desc_zh="风险", severity=0.3)],
        debates=debates or []))


def _debate(**o) -> dict:
    d = dict(key=_SEED_KEY, question_zh="AI 颠覆还是赋能?", bull_zh="赋能", bear_zh="颠覆",
             weight=0.5, lean=0.1, pillar_keys=["p1"],
             verification_points=[dict(
                 key="crpo_floor", question_zh="cRPO 增速?", metric="crpo_yoy",
                 bull_reading_zh="≥20% 证多", bear_reading_zh="≤12.5% 证空",
                 direction="higher_is_bull", bull_threshold=0.20, bear_threshold=0.125)])
    d.update(o)
    return d


def _mock_dossier(seed_keys):
    return {"text": "dossier text", "known_ids": set(), "kpis": {"crpo", "revenue"},
            "indicators": {"crpo_yoy"}, "debate_seeds": list(seed_keys),
            "coverage_gaps": [], "n_facts": 5, "as_of": "2026-07-07"}


@pytest.fixture()
def _clean(seeded_db):
    before = {r["id"] for r in db.query("SELECT id FROM company_thesis WHERE company_id=%s", (_CID,))}
    yield
    db.execute("DELETE FROM company_thesis WHERE company_id=%s AND NOT (id = ANY(%s))",
               (_CID, list(before) or [-1]))


def test_dossier_injects_seed(seeded_db, monkeypatch):
    seed = DebateSeed(company_id=_CID, key=_SEED_KEY, question_zh="AI 颠覆还是赋能?",
                      bull_zh="赋能", bear_zh="颠覆", suggested_metrics=("crpo_yoy",),
                      suggested_event_types=("contract_win",))
    monkeypatch.setattr("xar.ontology.debates.seeds_for",
                        lambda cid, themes=None: [seed] if cid == _CID else [])
    d = thesis.dossier(_CID)
    assert d is not None
    assert "AI 颠覆还是赋能" in d["text"]
    assert _SEED_KEY in d["debate_seeds"]
    assert "crpo_yoy" in d["indicators"]


def test_build_persists_debate(_clean, monkeypatch):
    monkeypatch.setattr(thesis, "dossier", lambda cid, **k: _mock_dossier([_SEED_KEY]))
    canned = _mk_thesis(debates=[_debate()])
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: canned)
    out = thesis.build(_CID, force=True)
    assert out["status"] == "built", out
    row = thesis.latest(_CID)
    assert row["content"]["debates"][0]["key"] == _SEED_KEY
    assert out["quality"]["vps_machine_checkable"] == 1


def test_build_rejects_missing_required_seed(_clean, monkeypatch):
    monkeypatch.setattr(thesis, "dossier", lambda cid, **k: _mock_dossier([_SEED_KEY]))
    canned = _mk_thesis(debates=[])            # 缺了要求的种子争论
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: canned)
    out = thesis.build(_CID, force=True)
    assert out["status"] == "rejected"
    assert _SEED_KEY in out["reason"]


def test_build_rejects_bad_vp_metric(_clean, monkeypatch):
    monkeypatch.setattr(thesis, "dossier", lambda cid, **k: _mock_dossier([_SEED_KEY]))
    bad = _mk_thesis(debates=[_debate(verification_points=[dict(
        key="v", question_zh="q", metric="not_a_metric",
        bull_reading_zh="b", bear_reading_zh="be")])])
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: bad)
    out = thesis.build(_CID, force=True)
    assert out["status"] == "rejected"
    assert "not_a_metric" in out["reason"]
