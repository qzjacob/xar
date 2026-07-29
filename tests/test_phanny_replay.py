"""M7:回放 + 多 horizon 回验 + A/B 评测 —— 「可迭代」的落点。

守的不变量:
  ① 回放**不读活表**:输入全部来自快照。否则 prices/estimates 一直在变,
     「同样代码同样日期重跑」得到的 dossier 与当初并不相同,那是重做不是回放;
  ② 提示词漂移必须被**显式标记**(bit_exact=False),不许悄悄吸收;
  ③ 回放版本绝不进生产读路径(最新裁决/组合),但**要**被同一套回验打分 ——
     回放也是可证伪的预测,这正是 A/B 的意义;
  ④ 多 horizon 回验是 append-only:旧的单 horizon JSONB 每次 UPDATE 覆盖,改判无痕;
  ⑤ 评测全程停留在 Phanny 1-10 刻度,thesis(1-5)/ET(0-10)不进统计。
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from xar.phanny import engine, evaluate, replay, snapshots
from xar.storage import db

_CID = "nvidia"
_ED = dt.date(2099, 6, 10)


def _insert_verdict(*, variant="prod", build_id=None, direction="long", conviction=7.0,
                    model="glm-5.2-sub", outcome=None, replay_of=None) -> int:
    rows = db.query(
        "INSERT INTO phanny_verdicts(company_id,event_date,version,direction,conviction,"
        "content,quality,as_of,model,build_id,variant,replay_of,outcome) "
        "VALUES(%s,%s,(SELECT COALESCE(max(version),0)+1 FROM phanny_verdicts "
        "              WHERE company_id=%s AND event_date=%s),"
        "%s,%s,'{}'::jsonb,'{}'::jsonb,%s,%s,%s,%s,%s,%s::jsonb) RETURNING id",
        (_CID, _ED, _CID, _ED, direction, conviction, _ED, model, build_id, variant, replay_of,
         json.dumps(outcome) if outcome else None))
    return rows[0]["id"]


def test_replay_needs_a_snapshot(isolated_db):
    """审计层之前的裁决没有 build_id —— 必须明说不可回放,而不是假装回放(那会去读活表)。"""
    vid = _insert_verdict(build_id=None)
    out = replay.replay_verdict(vid)
    assert out["status"] == "no_snapshot" and "无 build_id" in out["reason"]


def test_replay_rebuilds_dossier_without_touching_live_tables(isolated_db, monkeypatch):
    bid = snapshots.new_build_id()
    snapshots.snap_dossier(bid, _CID, {"text": "FROZEN EVIDENCE", "known_ids": {"event:7"},
                                       "panel": {"technical": {"last": 100}}, "n_facts": 11,
                                       "as_of": "2026-07-29", "implied_move": 0.07},
                           event_date=_ED)
    snapshots.snap_call(bid, _CID, stage="propose", attempt=1, model="glm-5.2-sub",
                        capture={"raw": "{}", "prompt_sha": "z" * 64, "schema_sha": "s" * 64},
                        template="phanny.proposer.user", template_ver=1,
                        params={"as_of": "2026-07-29"})
    vid = _insert_verdict(build_id=bid)

    seen: dict = {}

    def _fake_propose(cid, event, dossier, **k):
        seen["dossier"] = dossier
        return None, ["stub"], "glm-5.2-sub"

    monkeypatch.setattr(engine, "propose", _fake_propose)
    # 任何对 dossier_phanny(活表装配)的调用都是违规
    monkeypatch.setattr(engine, "dossier_phanny",
                        lambda *a, **k: pytest.fail("回放不得重新装配 dossier —— 必须吃快照"))
    out = replay.replay_verdict(vid, store=False)
    assert out["status"] == "rejected"                    # stub 返回 problems
    assert seen["dossier"]["text"] == "FROZEN EVIDENCE"   # 输入来自快照
    assert seen["dossier"]["known_ids"] == {"event:7"}
    assert seen["dossier"]["panel"]["technical"]["last"] == 100


def test_prompt_drift_is_flagged_not_swallowed(isolated_db, monkeypatch):
    """模板版本变了 → bit_exact=False + 明确原因。悄悄吸收漂移比报错更危险。"""
    bid = snapshots.new_build_id()
    snapshots.snap_dossier(bid, _CID, {"text": "E", "known_ids": set(), "panel": {},
                                       "n_facts": 9, "as_of": "2026-07-29"}, event_date=_ED)
    snapshots.snap_call(bid, _CID, stage="propose", attempt=1, model="m",
                        capture={"raw": "{}", "prompt_sha": "old" + "0" * 61},
                        template="phanny.proposer.user", template_ver=99)   # 库里是 v1
    vid = _insert_verdict(build_id=bid)
    monkeypatch.setattr(engine, "propose", lambda *a, **k: (None, ["x"], "m"))
    out = replay.replay_verdict(vid, store=False)
    assert out["bit_exact"] is False and "v99" in out["drift_reason"]


def test_replay_isolated_from_production_reads(isolated_db):
    """★ 回放绝不能污染生产:最新裁决只认 variant='prod'。"""
    prod = _insert_verdict(variant="prod", direction="long", conviction=7.0)
    _insert_verdict(variant="replay", direction="short", conviction=2.0, replay_of=prod)
    latest = engine.latest_verdict(_CID, _ED)
    assert latest["direction"] == "long" and latest["conviction"] == 7.0


def test_multi_horizon_outcomes_are_append_only(isolated_db):
    """旧口径每次 UPDATE 覆盖 outcome,改判无痕;新表按 horizon 各存一行且幂等。"""
    vid = _insert_verdict()
    engine._stamp(vid, {"status": "scored", "reaction_pct": 5.0, "direction_hit": True})
    engine._stamp_horizon(vid, "T+5", {"status": "scored", "reaction_pct": 9.0,
                                       "direction_hit": True})
    rows = db.query("SELECT horizon, reaction_pct FROM phanny_outcomes WHERE verdict_id=%s "
                    "ORDER BY horizon", (vid,))
    assert {r["horizon"]: r["reaction_pct"] for r in rows} == {"T+5": 9.0, "reaction": 5.0}
    engine._stamp_horizon(vid, "T+5", {"status": "scored", "reaction_pct": 9.5})   # 幂等重跑
    rows2 = db.query("SELECT count(*) c FROM phanny_outcomes WHERE verdict_id=%s", (vid,))
    assert rows2[0]["c"] == 2


def test_legacy_outcome_column_still_written(isolated_db):
    """读侧不破:既有 dashboards / calibration 仍读 outcome JSONB。"""
    vid = _insert_verdict()
    engine._stamp(vid, {"status": "scored", "reaction_pct": 3.3, "direction_hit": False})
    row = db.query("SELECT outcome FROM phanny_verdicts WHERE id=%s", (vid,))[0]
    assert row["outcome"]["reaction_pct"] == 3.3


def test_evaluate_groups_by_model_within_phanny_scale(isolated_db):
    _insert_verdict(model="glm-5.2-sub", conviction=8.0,
                    outcome={"status": "scored", "direction_hit": True, "reaction_pct": 6.0})
    _insert_verdict(model="kimi-k3-sub", conviction=3.0,
                    outcome={"status": "scored", "direction_hit": False, "reaction_pct": -2.0})
    out = evaluate.compare("model")
    assert out["scale"] == "phanny_1_10"
    names = {g["group"] for g in out["groups"]}
    assert {"glm-5.2-sub", "kimi-k3-sub"} <= names


def test_evaluate_scores_replays_too(isolated_db):
    """回放也被回验打分 —— 否则 A/B 无从比较。"""
    prod = _insert_verdict(variant="prod",
                           outcome={"status": "scored", "direction_hit": True})
    _insert_verdict(variant="replay", replay_of=prod,
                    outcome={"status": "scored", "direction_hit": False})
    out = evaluate.compare("variant")
    groups = {g["group"]: g for g in out["groups"]}
    assert "prod" in groups and "replay" in groups


def test_replay_pairs_join(isolated_db):
    prod = _insert_verdict(variant="prod", direction="long", conviction=7.0)
    _insert_verdict(variant="replay", replay_of=prod, direction="short", conviction=4.0,
                    model="kimi-k3-sub")
    pairs = evaluate.replay_pairs()
    assert pairs and pairs[0]["orig_direction"] == "long"
    assert pairs[0]["replay_direction"] == "short" and pairs[0]["replay_model"] == "kimi-k3-sub"


def test_evaluate_never_reads_other_conviction_scales():
    """刻度隔离的机械保证:查询里不得出现 thesis / earnings_verdicts 表。"""
    import inspect
    src = inspect.getsource(evaluate)
    assert "earnings_verdicts" not in src and "company_thesis" not in src
