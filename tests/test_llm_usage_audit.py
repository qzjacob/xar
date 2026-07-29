"""M4:llm_usage 可审计性增强。

此前**只有成功调用**落行 —— 轮转、重试、返空全部不可见,于是「这次为什么换了模型」
「哪家在抖」「实际给了多少 token 预算」在库里查不到。现在失败候选也落行(usd=0/tokens=0,
故任何既有花费聚合口径不变),并记下 latency / attempt / requested(含 **clamped**)/ context。
"""
from __future__ import annotations

import pytest

from xar.models import llm
from xar.storage import db


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Usage:
    prompt_tokens = 120
    completion_tokens = 60


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = _Usage()


@pytest.fixture()
def rows(isolated_db):
    def _get(run_id):
        return db.query(
            "SELECT node, model, status, error, attempt, latency_ms, requested, context, "
            "prompt_sha, tokens_estimated, usd, input_tokens FROM llm_usage "
            "WHERE run_id=%s ORDER BY id", (run_id,))
    return _get


def test_rotation_records_failure_then_success(rows, monkeypatch):
    """一次调用轮转两个候选:失败候选留下 status='error' 行,成功的留 'ok' —— 轮转史可查。"""
    calls = {"n": 0}

    def _completion(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("provider exploded")
        return _Resp('{"ok": true}')

    monkeypatch.setattr(llm.litellm, "completion", _completion)
    monkeypatch.setattr(llm, "_ensure_keys", lambda: None)
    monkeypatch.setattr(llm, "_endpoint", lambda spec, s: (None, None, True))
    out = llm.complete("hi", task="kg_extract", node="t_rot", run_id="kg-rot1")
    assert out == '{"ok": true}'
    got = rows("kg-rot1")
    assert [r["status"] for r in got] == ["error", "ok"]
    assert got[0]["attempt"] == 1 and got[1]["attempt"] == 2
    assert "provider exploded" in got[0]["error"]
    assert got[0]["usd"] == 0 and got[0]["input_tokens"] == 0     # 失败行不污染花费聚合
    assert got[1]["input_tokens"] == 120 and got[1]["latency_ms"] is not None


def test_empty_completion_recorded_as_empty(rows, monkeypatch):
    """返空是 GLM-5.2 高推理力度下的招牌故障 —— 必须与 error 区分开。"""
    calls = {"n": 0}

    def _completion(**kw):
        calls["n"] += 1
        return _Resp("   " if calls["n"] == 1 else "text")

    monkeypatch.setattr(llm.litellm, "completion", _completion)
    monkeypatch.setattr(llm, "_ensure_keys", lambda: None)
    monkeypatch.setattr(llm, "_endpoint", lambda spec, s: (None, None, True))
    llm.complete("hi", task="kg_extract", node="t_empty", run_id="kg-empty1")
    got = rows("kg-empty1")
    assert got[0]["status"] == "empty" and got[0]["error"] == "empty completion"
    assert got[-1]["status"] == "ok"


def test_requested_records_max_token_clamp(rows, monkeypatch):
    """`clamped=true` = 「我们要 16000、模型只给得起 8192」—— 结构化输出被截断的直接证据,
    这正是 thesis 停摆期间无法证实的那个假设。"""
    monkeypatch.setattr(llm.litellm, "completion", lambda **kw: _Resp("ok"))
    monkeypatch.setattr(llm, "_ensure_keys", lambda: None)
    monkeypatch.setattr(llm, "_endpoint", lambda spec, s: (None, None, True))
    with llm.pinned(("glm-4.6-sub",)):                     # max_output=8192
        llm.complete("hi", task="thesis", node="t_clamp", run_id="thesis-clamp", max_tokens=16000)
    req = rows("thesis-clamp")[-1]["requested"]
    assert req["max_tokens"] == 16000 and req["granted"] == 8192 and req["clamped"] is True
    assert req["pin"] == ["glm-4.6-sub"]

    with llm.pinned(("glm-5.2-sub",)):                     # max_output=32768 → 不钳制
        llm.complete("hi", task="thesis", node="t_noclamp", run_id="thesis-noclamp", max_tokens=16000)
    req2 = rows("thesis-noclamp")[-1]["requested"]
    assert req2["granted"] == 16000 and req2["clamped"] is False


def test_context_attributes_spend_to_a_company(rows, monkeypatch):
    """没有 context,一行花费无法归属到任何一家公司 —— 只能按时间猜。"""
    monkeypatch.setattr(llm.litellm, "completion", lambda **kw: _Resp("ok"))
    monkeypatch.setattr(llm, "_ensure_keys", lambda: None)
    monkeypatch.setattr(llm, "_endpoint", lambda spec, s: (None, None, True))
    llm.complete("hi", task="kg_extract", node="t_ctx", run_id="kg-ctx1",
                 context={"company_id": "micron", "role": "proposer", "round": 2})
    ctx = rows("kg-ctx1")[-1]["context"]
    assert ctx["company_id"] == "micron" and ctx["round"] == 2


def test_prompt_sha_is_stable_and_input_sensitive(rows, monkeypatch):
    monkeypatch.setattr(llm.litellm, "completion", lambda **kw: _Resp("ok"))
    monkeypatch.setattr(llm, "_ensure_keys", lambda: None)
    monkeypatch.setattr(llm, "_endpoint", lambda spec, s: (None, None, True))
    llm.complete("same", system="sys", task="kg_extract", node="a", run_id="kg-sha1")
    llm.complete("same", system="sys", task="kg_extract", node="b", run_id="kg-sha2")
    llm.complete("other", system="sys", task="kg_extract", node="c", run_id="kg-sha3")
    a, b, c = (rows(f"kg-sha{i}")[-1]["prompt_sha"] for i in (1, 2, 3))
    assert a == b and a != c and len(a) == 64


def test_record_never_raises_but_logs(monkeypatch):
    warned: list = []
    monkeypatch.setattr(llm.db, "execute", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
    monkeypatch.setattr(llm.log, "warning", lambda *a, **k: warned.append(a))
    from xar.models import registry as reg
    llm._record("kg-x", "n", reg.get("glm-5.2-sub"), llm._NO_USAGE, "kg_extract", True)
    assert warned, "usage 写失败必须留声(旧代码是裸 pass,静默的观测面等于没有)"


def test_capture_hook_populates_replay_trace(monkeypatch):
    """capture 是 M5/M7 回放的取数口:模型原文 + 完整提示词 + schema 指纹。"""
    from pydantic import BaseModel

    class _Schema(BaseModel):
        v: int = 0

    monkeypatch.setattr(llm, "complete", lambda *a, **k: '{"v": 7}')
    cap: dict = {}
    out = llm.complete_json("p", _Schema, node="t", capture=cap)
    assert out.v == 7
    assert cap["raw"] == '{"v": 7}' and cap["attempts"] == 1
    assert len(cap["prompt_sha"]) == 64 and len(cap["schema_sha"]) == 64
    assert "JSON Schema" in cap["instruction"] and "fallback" not in cap


def test_capture_marks_fallback_when_model_never_produced_json(monkeypatch):
    """兜底 schema() 是「模型压根没产出」——调用方必须能与真产出区分开。"""
    from pydantic import BaseModel

    class _Schema(BaseModel):
        v: int = 0

    monkeypatch.setattr(llm, "complete", lambda *a, **k: "not json at all")
    cap: dict = {}
    llm.complete_json("p", _Schema, node="t", capture=cap)
    assert cap.get("fallback") is True and cap["attempts"] == 2
