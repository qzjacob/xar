"""Phanny worker 节拍接线:_phanny_step 在 due 时调 judge_due/score_outcomes,失败吞掉不沉轮。
全打桩,不发真实 LLM / 不写 DB。"""
from __future__ import annotations

from xar.orchestration import glm_worker as gw
from xar.phanny import engine


def test_phanny_step_runs_when_due(monkeypatch):
    monkeypatch.setattr(gw, "_due", lambda k, s: True)
    monkeypatch.setattr(gw, "_stamp", lambda *a, **k: None)
    monkeypatch.setattr(engine, "judge_due", lambda: {"status": "normal", "n": 12})
    monkeypatch.setattr(engine, "score_outcomes", lambda: {"scored": 3})
    out = gw._phanny_step()
    assert out["verdicts"]["n"] == 12 and out["outcomes"]["scored"] == 3


def test_phanny_step_skips_when_not_due(monkeypatch):
    monkeypatch.setattr(gw, "_due", lambda k, s: False)
    called: list[str] = []
    monkeypatch.setattr(engine, "judge_due", lambda: called.append("j") or {})
    monkeypatch.setattr(engine, "score_outcomes", lambda: called.append("o") or {})
    out = gw._phanny_step()
    assert out == {} and called == []


def test_phanny_step_swallows_errors(monkeypatch):
    monkeypatch.setattr(gw, "_due", lambda k, s: True)
    monkeypatch.setattr(gw, "_stamp", lambda *a, **k: None)

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(engine, "judge_due", boom)
    monkeypatch.setattr(engine, "score_outcomes", lambda: {"scored": 0})
    out = gw._phanny_step()
    assert "error" in out["verdicts"] and out["outcomes"]["scored"] == 0


def test_phanny_wired_into_run_once():
    import inspect
    src = inspect.getsource(gw.run_once)
    assert "_phanny_step()" in src and 'stages_on.get("phanny"' in src
