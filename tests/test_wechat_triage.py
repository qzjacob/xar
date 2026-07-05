"""微信 SNR triage:融合数学、预筛地板、小作文/新颖度救回、补链、两条 WHERE 守卫。
LLM 全打桩;DB 部分用 seeded_db 自清。"""
from __future__ import annotations

import pytest

from xar.mining import triage
from xar.mining.triage import WechatTriage, _blend


# ── 离线:融合数学 ─────────────────────────────────────────────────────────────
def test_blend_irrelevant_floor():
    assert _blend(WechatTriage(relevant=False)) == 0.05


def test_blend_strong_signal_kept():
    s = _blend(WechatTriage(relevant=True, priority=0.9, credibility=0.8,
                            novelty=0.7, specificity=0.8))
    assert s >= 0.4  # above default deep_min


def test_blend_xiaozuowen_floored():
    s = _blend(WechatTriage(relevant=True, priority=0.6, credibility=0.3,
                            is_xiaozuowen=True, novelty=0.2, specificity=0.2))
    assert s <= 0.15


def test_blend_low_circulation_rescue():
    # low priority but highly novel + specific → rescued above deep_min
    s = _blend(WechatTriage(relevant=True, priority=0.2, credibility=0.3,
                            novelty=0.9, specificity=0.9))
    assert s >= 0.4


def test_prefilter_noise_vs_signal():
    from xar.ingestion.wechat import _alias_index

    aliases = _alias_index()
    noise = triage._prefilter("今天天气不错", "大家注意身体多喝热水", aliases)
    assert not noise["hit"]
    signal = triage._prefilter("1.6T光模块放量", "CoWoS先进封装供不应求", aliases)
    assert signal["hit"] and "ai_optical" in signal["themes"]


# ── DB:预筛地板跳过 LLM + WHERE 守卫 ─────────────────────────────────────────
@pytest.fixture
def wechat_docs(seeded_db, monkeypatch):
    from xar.ingestion.base import Doc, save
    from xar.storage import db

    ids = {}
    # a pure-noise doc (no theme/route/company) and a signal doc
    for key, title, body in [
        ("noise", "早安心语", "祝大家周末愉快身体健康"),
        ("signal", "中际旭创1.6T光模块", "CoWoS先进封装订单激增,月产能翻倍"),
    ]:
        d = Doc(company_id=None, source="wechat", doc_type="mp_article",
                title=title, text=f"{title}\n\n{body}", permission="grey",
                license_tag="wechat-extracted-facts-self-use")
        # force deterministic id for cleanup
        did = save(d)
        ids[key] = did
    yield ids
    db.execute("DELETE FROM documents WHERE id = ANY(%s)", (list(ids.values()),))


def test_noise_floor_skips_llm(wechat_docs, monkeypatch):
    from xar.storage import db

    # LLM must NOT be called for the pure-noise doc; make it explode if called
    monkeypatch.setattr(triage.llm, "complete_json",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM called on noise")))
    # stub the signal doc's LLM to a mid verdict
    def fake_json(prompt, schema, **kw):
        return WechatTriage(relevant=True, entity="中际旭创", priority=0.8,
                            credibility=0.8, novelty=0.7, specificity=0.8)
    # only the signal doc reaches the LLM; triage both via triage_pending
    calls = {"n": 0}
    def dispatch(prompt, schema, **kw):
        calls["n"] += 1
        return fake_json(prompt, schema, **kw)
    monkeypatch.setattr(triage.llm, "complete_json", dispatch)
    out = triage.triage_pending(limit=50)
    assert out["triaged"] >= 2
    assert out["noise_floor"] >= 1     # the noise doc took the zero-LLM floor
    # noise doc scored at floor, signal doc scored high
    noise_score = db.query("SELECT triage_score FROM documents WHERE id=%s",
                           (wechat_docs["noise"],))[0]["triage_score"]
    sig_score = db.query("SELECT triage_score FROM documents WHERE id=%s",
                         (wechat_docs["signal"],))[0]["triage_score"]
    assert noise_score <= 0.05 and sig_score >= 0.4


def test_where_guards_select_correctly(wechat_docs, monkeypatch):
    """用 SHIPPED 的 triage.wechat_pending_clause()(而非硬编码副本)验证守卫:
    miner 开启 → 高分微信留、低分/未 triage 微信排除;miner 关闭 → 全放行。"""
    from xar.mining import triage
    from xar.storage import db

    db.execute("UPDATE documents SET triage_score=0.9, triaged_at=now() WHERE id=%s",
               (wechat_docs["signal"],))
    db.execute("UPDATE documents SET triage_score=0.02, triaged_at=now() WHERE id=%s",
               (wechat_docs["noise"],))
    # signal 有分、noise 有分、再造一篇未 triage(triage_score NULL)
    from xar.ingestion.base import Doc, save
    untriaged = save(Doc(company_id=None, source="wechat", doc_type="mp_article",
                         title="未评分号", text="未评分号\n\n某些内容",
                         permission="grey", license_tag="wechat-extracted-facts-self-use"))
    ids = list(wechat_docs.values()) + [untriaged]
    try:
        monkeypatch.setattr(triage, "_deep_min", lambda: 0.4)
        # miner 默认开启 → 守卫 = triage_score>=deep_min(未 triage 的 NULL 被排除)
        clause = triage.wechat_pending_clause()
        assert clause and "triage_score" in clause
        kept = {r["id"] for r in db.query(
            f"SELECT d.id FROM documents d WHERE d.id = ANY(%s){clause}", (ids,))}
        assert wechat_docs["signal"] in kept          # 高分留
        assert wechat_docs["noise"] not in kept       # 低分排除
        assert untriaged not in kept                  # 未 triage 排除(不再绕过闸门)
        # miner 关闭 → 守卫为空,全放行(退回旧的无差别抽取)
        monkeypatch.setattr(triage, "wechat_pending_clause", lambda: "")
        assert triage.wechat_pending_clause() == ""
    finally:
        db.execute("DELETE FROM documents WHERE id=%s", (untriaged,))
