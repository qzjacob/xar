"""Phanny worker 节拍接线:_phanny_step 在 due 时调 judge_due/score_outcomes,失败吞掉不沉轮。
外加 run_id 贯穿护栏 —— 生产道曾整条 run_id=NULL,导致花费无法归因**且预算帽形同虚设**。
全打桩,不发真实 LLM / 不写 DB。"""
from __future__ import annotations

from xar.orchestration import glm_worker as gw
from xar.phanny import engine


def test_phanny_step_runs_when_due(monkeypatch):
    monkeypatch.setattr(gw, "_due", lambda k, s: True)
    monkeypatch.setattr(gw, "_stamp", lambda *a, **k: None)
    monkeypatch.setattr(engine, "judge_due", lambda **k: {"status": "normal", "n": 12})
    monkeypatch.setattr(engine, "score_outcomes", lambda **k: {"scored": 3})
    out = gw._phanny_step()
    assert out["verdicts"]["n"] == 12 and out["outcomes"]["scored"] == 3


def test_phanny_step_threads_run_id(monkeypatch):
    """worker 必须给整本 book 铸一个 phanny- 前缀 run_id(该前缀才吃批量预算帽)。"""
    seen: dict = {}
    monkeypatch.setattr(gw, "_due", lambda k, s: k == "phanny_verdicts")
    monkeypatch.setattr(gw, "_stamp", lambda *a, **k: None)
    monkeypatch.setattr(engine, "judge_due", lambda **k: seen.update(k) or {})
    gw._phanny_step()
    assert seen.get("run_id", "").startswith("phanny-")

    from xar.models import llm
    assert llm._budget_cap(seen["run_id"], _S()) == 20.0     # 批量帽,不是 per-run 小帽


def test_earnings_step_threads_run_id(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(gw, "_due", lambda k, s: k == "earnings_verdicts")
    monkeypatch.setattr(gw, "_stamp", lambda *a, **k: None)
    from xar.research import earnings
    monkeypatch.setattr(earnings, "judge_due", lambda **k: seen.update(k) or {})
    gw._earnings_step()
    assert seen.get("run_id", "").startswith("earn-")


class _S:                                    # llm._budget_cap 只读这两个字段
    llm_max_usd_per_batch = 20.0
    llm_max_usd_per_run = 5.0


def test_batch_prefixes_cover_all_batch_lanes():
    """每条批量道的前缀都必须在册,否则它悄悄掉回 per-run 小帽(subpool/flow 曾如此)。"""
    from xar.models import llm
    s = _S()
    for prefix in ("kg", "expert", "synth", "batch", "thesis", "flow", "phanny", "earn"):
        assert llm._budget_cap(f"{prefix}-abc123", s) == s.llm_max_usd_per_batch, prefix
    assert llm._budget_cap("adhoc-abc123", s) == s.llm_max_usd_per_run
    assert llm._budget_cap(None, s) == s.llm_max_usd_per_run


def test_phanny_step_skips_when_not_due(monkeypatch):
    monkeypatch.setattr(gw, "_due", lambda k, s: False)
    called: list[str] = []
    monkeypatch.setattr(engine, "judge_due", lambda **k: called.append("j") or {})
    monkeypatch.setattr(engine, "score_outcomes", lambda **k: called.append("o") or {})
    out = gw._phanny_step()
    assert out == {} and called == []


def test_phanny_step_swallows_errors(monkeypatch):
    monkeypatch.setattr(gw, "_due", lambda k, s: True)
    monkeypatch.setattr(gw, "_stamp", lambda *a, **k: None)

    def boom(**k):
        raise RuntimeError("boom")

    monkeypatch.setattr(engine, "judge_due", boom)
    monkeypatch.setattr(engine, "score_outcomes", lambda **k: {"scored": 0})
    out = gw._phanny_step()
    assert "error" in out["verdicts"] and out["outcomes"]["scored"] == 0


def test_phanny_wired_into_run_once():
    import inspect
    src = inspect.getsource(gw.run_once)
    assert "_phanny_step()" in src and 'stages_on.get("phanny"' in src
