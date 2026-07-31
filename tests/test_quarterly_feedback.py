"""M8:季报 → 个股投资观点的反哺回路(Genny 持续更新的动力源)。

守的不变量:
  ① **一次财报只产生一条事实** —— Phanny 与 ET 两条 12h 回验节拍谁先跑都不重复写(双计第一道闸);
  ② **只有已兑现的结果成为事实,赛前观点永不入库** —— 这是斩断 phanny↔thesis 互引循环的关键:
     季报 dossier 读论点、论点读季报事实,若把「裁决怎么看」也写成事实,两边会互相强化猜测;
  ③ 极性来自**市场实际反应**(客观),不是任何模型的观点;
  ④ 刻度隔离:Phanny 1-10 绝不写进 company_thesis.conviction(1-5);
  ⑤ 财报参与重建队列,且 `changed_because` 要讲出因果。
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from xar.research import quarterly_feedback as qf
from xar.storage import db

# ⚠️ 公司也必须是哨兵,不能借用真实公司(2026-07-31 修)。
# 原来是 `_CID = "nvidia"` + 远期 `_ED` 哨兵,以为靠日期就能避开真实数据 —— 避不开:
# `company_thesis` 的唯一约束是 **(company_id, version)**,**不含 event_date/as_of**。
# 生产库里 nvidia 早有 version=1(建于 2026-07-03),于是测试插同一把键必然
# `UniqueViolation: Key (company_id, version)=(nvidia, 1) already exists`。
# 更深一层:即使换个 version 躲开约束,`recent_print_companies` / `sweep` 读的仍是
# **该公司的真实论点行**,断言等于在考生产数据的状态,而不是被测逻辑 —— 借真实公司做
# 夹具,从一开始就不成立。改用库里不存在的哨兵公司,并在事务内补一条 companies 行
# (company_thesis/phanny_verdicts/event_calendar 都有 FK 指向 companies);
# isolated_db 整体回滚,不留痕。
_CID = "zz_qf_test"
_ED = dt.date(2099, 4, 15)          # 远期哨兵,避开真实数据(与既有测试同惯例)


@pytest.fixture(autouse=True)
def _sentinel_company(isolated_db):
    """在事务内建出哨兵公司。autouse + 依赖 isolated_db,保证任何 DB 测试跑之前它就位。"""
    db.execute("INSERT INTO companies(id, name) VALUES(%s, %s) ON CONFLICT (id) DO NOTHING",
               (_CID, "ZZ Quarterly-Feedback Test Co"))
    yield


def _events(cid=_CID):
    return db.query("SELECT event_type, event_date, polarity, summary, attrs, dedup_key, "
                    "time_orientation, license_tag FROM kg_events WHERE dedup_key LIKE %s "
                    "AND company_id=%s", (f"{qf._DEDUP_PREFIX}:%", cid))


def _seed_scored_verdict(table: str, direction="long", conviction=7.0, reaction=8.2, hit=True):
    outcome = {"status": "scored", "reaction_pct": reaction, "direction_hit": hit,
               "size_weighted_pnl_pct": 0.41}
    cols = ("company_id, event_date, version, direction, conviction, content, quality, as_of, "
            "outcome, outcome_at")
    vals = "(%s,%s,1,%s,%s,'{}'::jsonb,'{}'::jsonb,%s,%s::jsonb, now())"
    db.execute(f"INSERT INTO {table}({cols}) VALUES {vals}",  # noqa: S608 — 表名为测试常量
               (_CID, _ED, direction, conviction, _ED,
                json.dumps(outcome, ensure_ascii=False)))


def test_one_print_one_fact_across_both_verdict_systems(isolated_db):
    """双计第一道闸:Phanny 与 ET 各回验一次,仍只留一条事实。"""
    _seed_scored_verdict("phanny_verdicts")
    _seed_scored_verdict("earnings_verdicts", conviction=6.0)
    r1 = qf.on_outcome(_CID, _ED)
    r2 = qf.on_outcome(_CID, _ED)               # 第二条节拍
    assert r1["event_inserted"] is True and r2.get("deduped") is True
    assert len(_events()) == 1


def test_fact_carries_realized_outcome_not_opinion(isolated_db):
    """事实内容必须是**已实现**的:市场反应 + 各系统命中与否。绝不写赛前观点。"""
    _seed_scored_verdict("phanny_verdicts", direction="long", reaction=8.2, hit=True)
    qf.on_outcome(_CID, _ED)
    e = _events()[0]
    assert e["event_type"] == "earnings" and e["time_orientation"] == "backward_looking"
    assert e["polarity"] == "positive"                    # 极性 = 市场裁决,不是模型观点
    assert "季报兑现" in e["summary"] and "命中" in e["summary"]
    assert e["attrs"]["reaction_pct"] == 8.2 and e["attrs"]["phanny_hit"] is True


def test_negative_reaction_flips_polarity(isolated_db):
    _seed_scored_verdict("phanny_verdicts", direction="long", reaction=-5.1, hit=False)
    qf.on_outcome(_CID, _ED)
    e = _events()[0]
    assert e["polarity"] == "negative" and "未中" in e["summary"]


def test_unscored_verdict_never_becomes_a_fact(isolated_db):
    """★ 循环终止条件:赛前(未回验)的裁决不得入库为事实,否则 phanny 与 thesis 会互相
    引用对方的猜测并自我强化。"""
    db.execute("INSERT INTO phanny_verdicts(company_id,event_date,version,direction,conviction,"
               "content,quality,as_of) VALUES(%s,%s,1,'long',9.0,'{}'::jsonb,'{}'::jsonb,%s)",
               (_CID, _ED, _ED))
    qf.on_outcome(_CID, _ED)
    e = _events()[0]
    assert e["attrs"]["phanny_verdict_id"] is None       # 未回验 → 不引用
    assert "Phanny 判" not in e["summary"]


def test_verdict_conviction_never_leaks_into_thesis(isolated_db):
    """刻度隔离:Phanny 的 1-10 不得以任何形式写进 company_thesis(1-5)。"""
    _seed_scored_verdict("phanny_verdicts", conviction=9.0)
    before = db.query("SELECT count(*) c FROM company_thesis WHERE company_id=%s", (_CID,))[0]["c"]
    qf.on_outcome(_CID, _ED)
    after = db.query("SELECT count(*) c FROM company_thesis WHERE company_id=%s", (_CID,))[0]["c"]
    assert before == after                # 反哺**不**直接写论点,只投喂事实总线


def test_on_outcome_is_fail_soft(isolated_db, monkeypatch):
    """反哺失败绝不拖垮回验批。"""
    monkeypatch.setattr(qf.db, "query", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
    out = qf.on_outcome(_CID, _ED)
    assert "error" in out and out["event_inserted"] is False


def test_recent_print_companies_needs_stale_thesis(isolated_db):
    """财报感知重建队列:论点比这次财报旧才入队;论点更新的不重复排。"""
    _seed_scored_verdict("phanny_verdicts")
    db.execute("INSERT INTO company_thesis(company_id,version,as_of,stance,conviction,one_liner,"
               "content,quality) VALUES(%s,1,%s,'bull',3,'x','{}'::jsonb,'{}'::jsonb)",
               (_CID, _ED - dt.timedelta(days=10)))        # 论点旧于财报 → 应入队
    picks = dict(qf.recent_print_companies(days=99999))
    assert _CID in picks and "季报兑现" in picks[_CID]

    db.execute("UPDATE company_thesis SET as_of=%s WHERE company_id=%s",
               (_ED + dt.timedelta(days=1), _CID))         # 论点已比财报新 → 出队
    assert _CID not in dict(qf.recent_print_companies(days=99999))


def test_sweep_covers_all_thesis_holders_not_just_universe(isolated_db):
    """全覆盖库反哺道:只要持有论点且近期出过财报,即便没有任何裁决也要合成事实 ——
    完整多空辩论很贵(每名 ~40 次订阅调用),但「财报兑现了什么」零成本,不该只有几十家享有。"""
    recent = dt.date.today() - dt.timedelta(days=2)
    db.execute("INSERT INTO company_thesis(company_id,version,as_of,stance,conviction,one_liner,"
               "content,quality) VALUES(%s,1,%s,'bull',3,'x','{}'::jsonb,'{}'::jsonb)",
               (_CID, recent - dt.timedelta(days=30)))
    db.execute("INSERT INTO event_calendar(company_id,event_type,scheduled_for,status,source,"
               "dedup_key) VALUES(%s,'earnings',%s,'occurred','test',%s)",
               (_CID, recent, f"zz_sweep_test:{_CID}:{recent}"))
    out = qf.sweep(days=5)
    assert out["events"] >= 1
    assert any(e["event_date"] == recent for e in _events())


def test_vp_checks_is_a_count_not_a_list(isolated_db, monkeypatch):
    """check_verification_points 返回的是**结果列表**;当作计数直接累加会在 sweep 里炸
    (int += list)。生产冒烟抓到过一次 —— 用类型断言钉死。"""
    from xar.research import evidence_link, thesis
    monkeypatch.setattr(thesis, "latest", lambda cid: {"content": {}})
    monkeypatch.setattr(evidence_link, "check_verification_points",
                        lambda cid, th, **k: [{"vp": 1}, {"vp": 2}, {"vp": 3}])
    out = qf.on_outcome(_CID, _ED)
    assert out["vp_checks"] == 3 and isinstance(out["vp_checks"], int)


def test_lineage_shape(isolated_db):
    _seed_scored_verdict("phanny_verdicts")
    qf.on_outcome(_CID, _ED)
    rows = qf.lineage(_CID)
    assert rows and rows[0]["event_date"] == _ED and "季报兑现" in rows[0]["summary"]


# ── changed_because 因果 ────────────────────────────────────────────────────────
def test_changed_because_cites_the_trigger():
    """一份因财报而重建的论点,若注记与例行刷新毫无区别,复盘时就讲不出因果。"""
    from xar.research.thesis import _changed_because

    class _T:
        stance, conviction, pillars, debates = "bull", 3.5, [], []

    assert _changed_because(None, _T(), "季报兑现 2026-07-30") == "首版(季报兑现 2026-07-30)"
    prev = {"stance": "bear", "conviction": 2.0, "content": {"pillars": [], "debates": []}}
    out = _changed_because(prev, _T(), "季报兑现 2026-07-30")
    assert out.startswith("触发:季报兑现 2026-07-30;") and "stance bear→bull" in out
    assert "触发" not in _changed_because(prev, _T())      # 不传则行为不变


def test_rebuild_queue_prefers_challenged_then_prints(monkeypatch):
    """重建优先级:challenged → 刚出财报 → stale,且去重。"""
    from xar.orchestration import subpool_worker as sw
    from xar.research import quarterly_feedback, thesis_health

    monkeypatch.setattr(thesis_health, "challenged_companies_v2", lambda limit: ["a", "b"])
    monkeypatch.setattr(quarterly_feedback, "recent_print_companies",
                        lambda limit=20: [("b", "季报兑现 X"), ("c", "季报兑现 Y")])
    monkeypatch.setattr(sw, "get_settings", lambda: type("S", (), {
        "subpool_thesis_stale_hours": 24})())
    monkeypatch.setattr(sw.db if hasattr(sw, "db") else sw, "_noop", None, raising=False)
    from xar.storage import db as _db
    monkeypatch.setattr(_db, "query", lambda *a, **k: [])
    picks = sw._pick_companies(5)
    assert [c for c, _ in picks] == ["a", "b", "c"]          # b 只出现一次
    assert dict(picks)["c"] == "季报兑现 Y" and dict(picks)["a"] == "信号/争论挑战"


# ── Genny 个股页 phanny 块 ──────────────────────────────────────────────────────
def test_phanny_block_gated_and_scale_labelled():
    from xar.api import dashboard
    assert dashboard._phanny_block("definitely_not_a_company") is None
    from xar.ontology.phanny_events import PHANNY_UNIVERSE
    cid = next(iter(PHANNY_UNIVERSE))
    blk = dashboard._phanny_block(cid)
    if blk is not None:                        # 有数据时必须标注刻度,防前端与 thesis 1-5 混用
        assert blk["convictionScale"] == "phanny_1_10"
        assert "thesisFeedback" in blk and "hitRate" in blk


def test_company_detail_exposes_phanny_block():
    import inspect

    from xar.api import dashboard
    src = inspect.getsource(dashboard.company_detail)
    assert '"phanny": _phanny_block(cid)' in src


def test_quarterly_review_capability_registered():
    from xar.capabilities import registry
    spec = registry.by_name("quarterly_review")
    assert spec is not None and spec.kind == "read" and spec.chathy is True
