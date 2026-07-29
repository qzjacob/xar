"""M5+M6:Phanny 构建快照 + 辩论痕迹 v2。

这些测试守的是「事后能不能复盘」——每一条都对应一个此前会**静默丢失**的事实:
  · dossier / known_ids / panel 用完即弃 → 裁决的输入不可复原;
  · 被拒的构建完全无痕 → 最该复盘的反而没记录;
  · round-1 原稿不入库 → 反作弊守卫(禁止靠降 conviction 凑收敛)事后无法复核;
  · critic 解析失败伪装成 abstain → 全体崩掉被判「一致同意」,零对抗压力也能收敛;
  · 被拒的修正稿无声丢弃 → 痕迹里记的是旧状态,看不出这轮提过一稿;
  · REDEBATE 直接覆盖 → 被顶替的那一稿彻底消失;
  · book 级分布/组合/跳过原因只进被截断的日志 → 「那天为何标 calibration_incomplete」查不到。
"""
from __future__ import annotations

import pytest

from xar.phanny import snapshots
from xar.storage import db


@pytest.fixture()
def snaps(isolated_db):
    def _get(build_id):
        return db.query("SELECT stage, round, attempt, model, dossier_sha, prompt_sha, "
                        "response_sha, prompt_template, template_ver, schema_sha, known_ids, "
                        "panel, meta, verdict_id FROM phanny_build_snapshots "
                        "WHERE build_id=%s ORDER BY id", (build_id,))
    return _get


# ── 工件内容寻址 ────────────────────────────────────────────────────────────────
def test_artifact_dedup(isolated_db):
    """同一份 dossier 被一次 build 的 ~20 次调用共享 —— 必须只存一行。"""
    a = snapshots.save_artifact("dossier_text", "same body")
    b = snapshots.save_artifact("dossier_text", "same body")
    assert a == b and len(a) == 64
    n = db.query("SELECT count(*) c FROM artifacts WHERE sha=%s", (a,))[0]["c"]
    assert n == 1


def test_save_artifact_never_raises(monkeypatch):
    monkeypatch.setattr(snapshots.db, "execute",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
    warned: list = []
    monkeypatch.setattr(snapshots.log, "warning", lambda *a, **k: warned.append(a))
    assert snapshots.save_artifact("prompt", "x") is None and warned


def test_snap_dossier_freezes_the_evidence_surface(snaps, isolated_db):
    """定格三样东西:全文、known_ids(被允许引用的 id 全集)、panel(那些 id 实际所指的值)。
    缺任何一样,回放都得回读活表 —— 而活表一直在变。"""
    d = {"text": "EVIDENCE TEXT", "known_ids": {"event:9", "price:mu:recent"},
         "panel": {"tech": {"sma20": 101.5}}, "n_facts": 12, "as_of": "2026-07-29",
         "implied_move": 0.061}
    bid = snapshots.new_build_id()
    snapshots.snap_dossier(bid, "micron", d, run_id="phanny-x", event_date="2026-09-21")
    row = snaps(bid)[0]
    assert row["stage"] == "dossier"
    assert sorted(row["known_ids"]) == ["event:9", "price:mu:recent"]
    assert row["panel"]["tech"]["sma20"] == 101.5
    assert row["meta"]["n_facts"] == 12 and row["meta"]["implied_move"] == 0.061
    body = db.query("SELECT body FROM artifacts WHERE sha=%s", (row["dossier_sha"],))[0]["body"]
    assert body == "EVIDENCE TEXT"


def test_snap_call_records_template_identity_and_raw_response(snaps, isolated_db):
    bid = snapshots.new_build_id()
    cap = {"raw": '{"direction":"long"}', "prompt_sha": "a" * 64, "schema_sha": "b" * 64,
           "attempts": 1}
    snapshots.snap_call(bid, "micron", stage="propose", attempt=1, model="glm-5.2-sub",
                        capture=cap, template="phanny.proposer.user", template_ver=1)
    r = snaps(bid)[0]
    assert r["prompt_template"] == "phanny.proposer.user" and r["template_ver"] == 1
    assert r["prompt_sha"] == "a" * 64 and r["schema_sha"] == "b" * 64
    raw = db.query("SELECT body FROM artifacts WHERE sha=%s", (r["response_sha"],))[0]["body"]
    assert raw == '{"direction":"long"}'


def test_stamp_and_supersede(snaps, isolated_db):
    bid, newer = snapshots.new_build_id(), snapshots.new_build_id()
    snapshots.snap_call(bid, "micron", stage="propose", capture={"raw": "x"})
    snapshots.stamp_verdict(bid, 4242)
    assert snaps(bid)[0]["verdict_id"] == 4242
    snapshots.mark_superseded(bid, newer)
    assert snaps(bid)[0]["meta"]["superseded_by"] == newer


def test_load_build_roundtrip(isolated_db):
    bid = snapshots.new_build_id()
    snapshots.snap_dossier(bid, "micron", {"text": "D", "known_ids": set(), "panel": {},
                                           "n_facts": 5, "as_of": "2026-07-29"})
    snapshots.snap_call(bid, "micron", stage="propose", capture={"raw": "R"})
    out = snapshots.load_build(bid)
    assert out["dossier"]["dossier_text"] == "D" and len(out["calls"]) == 1
    assert snapshots.load_build("nope") is None


# ── 辩论痕迹 v2 ─────────────────────────────────────────────────────────────────
class _Dim:
    def __init__(self):
        self.key, self.score, self.note_zh, self.evidence = "fundamental", 1, "n", ["event:1"]


class _Prop:
    def __init__(self, direction="long", conviction=6.0):
        self.direction, self.conviction = direction, conviction
        self.asymmetry_zh, self.dimensions = "asym", [_Dim()]

    def model_dump(self):
        return {"direction": self.direction, "conviction": self.conviction}


class _Vote:
    def __init__(self, vote="agree"):
        self.direction_vote = vote

    def model_dump(self):
        return {"direction_vote": self.direction_vote, "conviction_delta": 0,
                "size_delta": 0, "attack_zh": "a", "rebuttal_zh": "r"}


@pytest.fixture()
def debate_env(monkeypatch):
    from xar.phanny import debate
    monkeypatch.setattr(debate, "_critic_pins", lambda: [("m1",), ("m2",), ("m3",)])
    monkeypatch.setattr(debate, "_anchors", lambda p: 6)
    monkeypatch.setattr(debate, "_critic_prompt", lambda *a, **k: "cp")
    monkeypatch.setattr(debate, "_rebut_prompt", lambda *a, **k: "rp")
    from xar.phanny import engine
    monkeypatch.setattr(engine, "_primary_pin", lambda: ("mp",))
    monkeypatch.setattr(engine, "_system_phanny", lambda: "sys")
    return debate


def _dossier():
    return {"text": "D", "known_ids": {"event:1"}, "as_of": "2026-07-29", "n_facts": 9}


def test_round0_original_proposal_is_traced(debate_env, monkeypatch):
    """round-1 原稿必须入痕:book 的反作弊守卫依赖 round1_*,此前只在内存里传递。"""
    d = debate_env
    monkeypatch.setattr(d.llm if hasattr(d, "llm") else d, "_x", None, raising=False)
    from xar.models import llm
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: _Vote())
    monkeypatch.setattr(llm, "pinned", lambda p: __import__("contextlib").nullcontext())
    out = d.run_debate("micron", {"scheduled_for": None}, _dossier(), _Prop())
    first = out["debate_trace"][0]
    assert first["round"] == 0 and first["role"] == "proposer"
    assert first["conviction"] == 6.0 and first["anchors"] == 6


def test_all_critics_failing_does_not_converge(debate_env, monkeypatch):
    """★ 最危险的一条:全体 critic 崩掉时 active 为空,旧式判据 `(not active)` 会判「一致同意」
    → 零对抗压力也能第 1 轮收敛。必须不收敛。"""
    from xar.models import llm
    monkeypatch.setattr(llm, "pinned", lambda p: __import__("contextlib").nullcontext())

    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm, "complete_json", boom)
    out = debate_env.run_debate("micron", {"scheduled_for": None}, _dossier(), _Prop())
    assert out["converged"] is False
    roles = [t.get("status") for t in out["debate_trace"] if t.get("role") == "critic"]
    assert roles and all(r == "provider_failed" for r in roles)


def test_parse_failure_is_not_an_abstain(debate_env, monkeypatch):
    """CriticVote 所有字段都有默认值 → 兜底 schema() 构造得干干净净,解析失败会伪装成
    一张真实的 abstain 票。必须标成 parse_failed 且不进投票池。"""
    from xar.models import llm
    monkeypatch.setattr(llm, "pinned", lambda p: __import__("contextlib").nullcontext())

    def bad(*a, **k):
        raise llm.StructuredOutputError("no valid JSON")

    monkeypatch.setattr(llm, "complete_json", bad)
    out = debate_env.run_debate("micron", {"scheduled_for": None}, _dossier(), _Prop())
    crit = [t for t in out["debate_trace"] if t.get("role") == "critic"]
    assert crit and all(t["status"] == "parse_failed" for t in crit)
    assert out["converged"] is False
    assert not any("vote" in t for t in crit)     # 绝不留下一张假票


def test_genuine_all_abstain_still_converges(debate_env, monkeypatch):
    """真正的全体弃权(有票、都是 abstain)是模型的真实表态 —— 仍应照旧收敛,
    不能被上一条修复误伤。"""
    from xar.models import llm
    monkeypatch.setattr(llm, "pinned", lambda p: __import__("contextlib").nullcontext())
    monkeypatch.setattr(llm, "complete_json",
                        lambda *a, **k: _Vote("abstain") if "CriticVote" in str(a[1]) else _Prop())
    out = debate_env.run_debate("micron", {"scheduled_for": None}, _dossier(), _Prop())
    assert out["converged"] is True


def test_rejected_rebuttal_is_traced(debate_env, monkeypatch):
    """被拒的修正稿此前无声丢弃,痕迹里记的却是旧状态 —— 看不出这轮其实提过一稿、因何被否。"""
    from xar.models import llm
    from xar.phanny import debate as dmod
    monkeypatch.setattr(llm, "pinned", lambda p: __import__("contextlib").nullcontext())

    def cj(prompt, schema, **k):
        return _Vote() if schema.__name__ == "CriticVote" else _Prop("short", 9.0)

    monkeypatch.setattr(llm, "complete_json", cj)
    monkeypatch.setattr(dmod, "validate_proposal", lambda p, **k: ["证据 id 编造", "维度缺项"],
                        raising=False)
    import xar.ontology.phanny_events as pe
    monkeypatch.setattr(pe, "validate_proposal", lambda p, **k: ["证据 id 编造", "维度缺项"])
    out = debate_env.run_debate("micron", {"scheduled_for": None}, _dossier(), _Prop())
    rej = [t for t in out["debate_trace"] if t.get("role") == "proposer_rejected"]
    assert rej and rej[0]["problems"] == ["证据 id 编造", "维度缺项"]
    assert out["proposal"].direction == "long"      # 被拒稿不得生效
