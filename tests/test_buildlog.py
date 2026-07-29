"""M2 构建拒因台账 —— 「这家公司为何没有产出」必须能用一句 SQL 回答。

核心不变量:
  ① writer 绝不 raise(台账坏掉不许拖垮真实构建),但要留声(静默的观测面等于没有);
  ② validate 违规**全量**入库(不截断)——截断过的清单无法定位系统性纪律模式;
  ③ `llm_failed`(模型没吐出可用 JSON)与 `rejected`(答了但违反纪律)必须分开 ——
     混同会让 subpool 把内容拒绝误判成 provider 故障、冷却整家供应商(误诊闭环)。
"""
from __future__ import annotations

import pytest

from xar.storage import buildlog, db


def test_record_never_raises(monkeypatch):
    """DB 抖动时 record 静默降级 —— 但必须留一条 warning。"""
    def boom(*a, **k):
        raise RuntimeError("db down")

    warned: list = []
    monkeypatch.setattr(buildlog, "execute", boom)
    monkeypatch.setattr(buildlog.log, "warning", lambda *a, **k: warned.append(a))
    buildlog.record("thesis", "nvidia", stage="llm", status="llm_failed", reason="x")
    assert warned, "台账写失败必须留声,不许裸 pass"


def test_record_and_read_back(isolated_db):
    buildlog.record("thesis", "zz_test_co", stage="validate", status="rejected",
                    reason="2 violations", problems=["a", "b"], run_id="thesis-abc",
                    attempt=2, model="glm-5.2-sub")
    rows = buildlog.recent("thesis", "zz_test_co", limit=5)
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "rejected" and r["stage"] == "validate"
    assert r["problems"] == ["a", "b"] and r["attempt"] == 2 and r["run_id"] == "thesis-abc"


def test_problems_stored_in_full(isolated_db):
    """曾经只留前 6 条字符串 —— 那样根本看不出系统性模式。全量入库是台账的意义所在。"""
    many = [f"violation {i}" for i in range(23)]
    buildlog.record("thesis", "zz_many_co", stage="validate", status="rejected",
                    problems=many, reason="many")
    rows = buildlog.recent("thesis", "zz_many_co", limit=1)
    assert rows[0]["problems"] == many and len(rows[0]["problems"]) == 23


def test_summary_groups_by_status(isolated_db):
    for st, stage in (("rejected", "validate"), ("rejected", "validate"), ("llm_failed", "llm")):
        buildlog.record("phanny", "zz_sum_co", stage=stage, status=st)
    got = {(r["stage"], r["status"]): r["n"] for r in buildlog.summary("phanny", hours=1)}
    assert got.get(("validate", "rejected")) == 2 and got.get(("llm", "llm_failed")) == 1


# ── thesis 状态分裂:llm_failed ≠ rejected ────────────────────────────────────────
class _FakeDossier(dict):
    pass


def _stub_dossier(monkeypatch, mod):
    d = {"text": "evidence", "n_facts": 9, "as_of": "2026-07-29", "known_ids": {"event:1"},
         "kpis": set(), "indicators": set(), "debate_seeds": ()}
    monkeypatch.setattr(mod, "dossier", lambda cid: d)
    monkeypatch.setattr(mod, "latest", lambda cid: None)
    return d


def test_thesis_llm_exception_is_llm_failed(isolated_db, monkeypatch):
    """模型调用炸了 = provider 问题,必须报 llm_failed(而非 rejected)。"""
    from xar.research import thesis

    _stub_dossier(monkeypatch, thesis)
    monkeypatch.setattr(thesis.llm, "complete_json",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("upstream 500")))
    r = thesis.build("zz_llmfail_co", force=True, run_id="thesis-t1")
    assert r["status"] == "llm_failed" and "llm_error" in r["reason"]
    rows = buildlog.recent("thesis", "zz_llmfail_co", limit=1)
    assert rows[0]["status"] == "llm_failed" and rows[0]["stage"] == "llm"


def test_thesis_invalid_json_is_llm_failed_not_rejected(isolated_db, monkeypatch):
    """最隐蔽的一条:complete_json 兜底返回 schema(),CompanyThesis 必填字段缺失抛
    ValidationError —— 「模型压根没产出 JSON」曾伪装成「纪律不合格」。"""
    from pydantic import ValidationError

    from xar.ontology.thesis import CompanyThesis
    from xar.research import thesis

    _stub_dossier(monkeypatch, thesis)

    def _fallback(*a, **k):
        CompanyThesis()          # 必填字段缺失 → ValidationError(复刻 llm.py 的兜底路径)

    monkeypatch.setattr(thesis.llm, "complete_json", _fallback)
    with pytest.raises(ValidationError):
        CompanyThesis()          # 前提确认:这个类确实会抛
    r = thesis.build("zz_badjson_co", force=True, run_id="thesis-t2")
    assert r["status"] == "llm_failed" and "llm_invalid_json" in r["reason"]


def test_thesis_discipline_violation_is_rejected(isolated_db, monkeypatch):
    """模型答了但违反纪律 = 内容问题,provider 健康 → rejected + 全量 problems 入库。"""
    from xar.research import thesis

    _stub_dossier(monkeypatch, thesis)
    monkeypatch.setattr(thesis.llm, "complete_json", lambda *a, **k: object())
    monkeypatch.setattr(thesis, "validate_thesis",
                        lambda *a, **k: ["pillar 1 无证据", "conviction 过高", "debate 未回应"])
    r = thesis.build("zz_reject_co", force=True, run_id="thesis-t3")
    assert r["status"] == "rejected" and len(r["problems"]) == 3
    rows = buildlog.recent("thesis", "zz_reject_co", limit=5)
    assert all(x["status"] == "rejected" and x["stage"] == "validate" for x in rows)
    assert rows[0]["problems"] == ["pillar 1 无证据", "conviction 过高", "debate 未回应"]
