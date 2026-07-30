"""`complete_stream` 的候选轮转语义 —— Chathy「服务暂时不可用」的真因。

实测故障(2026-07-30):Chathy 把对话链钉扎到 `config.chat_models`(kimi-k3-sub 在首位),
Kimi 本计费周期订阅额度用尽,抛 `litellm.APIError`。而旧代码只在 `_retryable(e)` 为真时才
轮转 —— `APIError` **不在**那份名单里(名单只含 RateLimit/Timeout/APIConnection/
ServiceUnavailable/InternalServer/BadGateway)—— 于是链上还有 3 个健康候选,整轮对话却直接
变成一条 error 事件,Telegram 侧显示「⚠️ 服务暂时不可用」。

正确语义(与非流式 `complete()` 对齐):
  · **未产出任何内容前:任何失败都轮转** —— 用户什么都没看到,换下一个候选是纯收益;
  · 已经吐过内容后:必须报错收场 —— 局部文本已在屏幕上,静默换模型会把两个不同的回答拼一起。
"""
from __future__ import annotations

import pytest

from xar.models import llm


class _Delta:
    def __init__(self, content=None):
        self.content = content
        self.tool_calls = None


class _Choice:
    def __init__(self, content=None):
        self.delta = _Delta(content)
        self.message = _Delta(content)


class _Chunk:
    def __init__(self, content=None):
        self.choices = [_Choice(content)]


class _Built:
    """stream_chunk_builder 的返回:带 choices[0].message 与 usage。"""
    def __init__(self, text):
        self.choices = [_Choice(text)]
        self.usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5})()


@pytest.fixture()
def wired(monkeypatch):
    monkeypatch.setattr(llm, "_ensure_keys", lambda: None)
    monkeypatch.setattr(llm, "_endpoint", lambda spec, s: (None, None, True))
    monkeypatch.setattr(llm, "_record", lambda *a, **k: None)
    monkeypatch.setattr(llm.litellm, "stream_chunk_builder",
                        lambda chunks, messages=None: _Built("ANSWER"))
    monkeypatch.setattr(llm, "_msg_to_dict",
                        lambda m: {"role": "assistant", "content": "ANSWER"})
    return monkeypatch


def _msgs():
    return [{"role": "user", "content": "hi"}]


class _QuotaAPIError(Exception):
    """复刻实测异常:litellm.APIError 携带订阅额度耗尽文案(不在 _retryable 名单里)。"""


def test_quota_exhausted_head_rotates_instead_of_failing_the_turn(wired, monkeypatch):
    """★ 真实故障:链首订阅额度用尽 → 必须轮转到下一个候选,而不是终止整轮对话。"""
    tried: list[str] = []

    def _completion(**kw):
        tried.append(kw["model"])
        if len(tried) == 1:
            raise _QuotaAPIError(
                "APIError: OpenAIException - You've reached your usage limit for this "
                "billing cycle.")
        return iter([_Chunk("ANSWER")])

    monkeypatch.setattr(llm.litellm, "completion", _completion)
    with llm.pinned(("kimi-k3-sub", "glm-5.2-sub")):
        evs = list(llm.complete_stream(_msgs(), node="chathy"))
    kinds = [e["type"] for e in evs]
    assert "error" not in kinds, f"额度耗尽把整轮打死了:{evs}"
    assert kinds[-1] == "final" and evs[-1]["message"]["content"] == "ANSWER"
    assert len(tried) == 2                      # 确实轮转到了第二个候选


def test_non_retryable_pre_delta_error_rotates(wired, monkeypatch):
    """任何未产出内容的失败都该轮转 —— 不再看 _retryable。"""
    tried: list[str] = []

    def _completion(**kw):
        tried.append(kw["model"])
        if len(tried) == 1:
            raise ValueError("deterministic bad request")     # 明确不可重试
        return iter([_Chunk("ANSWER")])

    monkeypatch.setattr(llm.litellm, "completion", _completion)
    with llm.pinned(("kimi-k3-sub", "glm-5.2-sub")):
        evs = list(llm.complete_stream(_msgs(), node="chathy"))
    assert evs[-1]["type"] == "final" and len(tried) == 2


def test_mid_stream_failure_still_surfaces_as_error(wired, monkeypatch):
    """已吐过内容后失败:必须报错,不许静默换模型 —— 否则两个不同回答会被拼在一起。"""
    tried: list[str] = []

    def _completion(**kw):
        tried.append(kw["model"])

        def _gen():
            yield _Chunk("部分回答…")          # 已产出内容
            raise _QuotaAPIError("died mid-stream")

        return _gen()

    monkeypatch.setattr(llm.litellm, "completion", _completion)
    with llm.pinned(("kimi-k3-sub", "glm-5.2-sub")):
        evs = list(llm.complete_stream(_msgs(), node="chathy"))
    assert [e["type"] for e in evs] == ["delta", "error"]
    assert len(tried) == 1                      # 不得轮转


def test_all_candidates_exhausted_yields_one_error(wired, monkeypatch):
    def _completion(**kw):
        raise _QuotaAPIError("quota gone")

    monkeypatch.setattr(llm.litellm, "completion", _completion)
    with llm.pinned(("kimi-k3-sub", "glm-5.2-sub")):
        evs = list(llm.complete_stream(_msgs(), node="chathy"))
    assert len(evs) == 1 and evs[0]["type"] == "error" and "quota gone" in evs[0]["message"]


def test_empty_completion_rotates(wired, monkeypatch):
    """返空(GLM 高推理力度的招牌故障)在未产出内容时同样轮转。"""
    tried: list[str] = []

    def _completion(**kw):
        tried.append(kw["model"])
        return iter([_Chunk(None)])            # 无 content

    monkeypatch.setattr(llm.litellm, "completion", _completion)
    built = {"n": 0}

    def _builder(chunks, messages=None):
        built["n"] += 1
        return _Built("" if built["n"] == 1 else "ANSWER")

    monkeypatch.setattr(llm.litellm, "stream_chunk_builder", _builder)
    monkeypatch.setattr(llm, "_msg_to_dict",
                        lambda m: {"role": "assistant",
                                   "content": m.content or ""})
    with llm.pinned(("kimi-k3-sub", "glm-5.2-sub")):
        evs = list(llm.complete_stream(_msgs(), node="chathy"))
    assert evs[-1]["type"] == "final" and len(tried) == 2


def test_stream_records_failures_for_diagnosis(wired, monkeypatch):
    """失败候选要留痕 —— 否则「Chathy 为什么报错」只能靠翻日志(这次就是这么查的)。"""
    recorded: list[dict] = []
    monkeypatch.setattr(llm, "_record",
                        lambda *a, **k: recorded.append({"model": a[2].id, **k}))

    def _completion(**kw):
        if not recorded:
            raise _QuotaAPIError("quota gone")
        return iter([_Chunk("ANSWER")])

    monkeypatch.setattr(llm.litellm, "completion", _completion)
    with llm.pinned(("kimi-k3-sub", "glm-5.2-sub")):
        list(llm.complete_stream(_msgs(), node="chathy"))
    assert recorded[0]["status"] == "error" and "quota gone" in recorded[0]["error"]
    assert recorded[0]["attempt"] == 1 and recorded[0]["latency_ms"] is not None
    assert recorded[-1].get("status", "ok") == "ok" and recorded[-1]["attempt"] == 2


# ── 签名契约:调用方传了、被调方没有 → 生产直接 TypeError ────────────────────────────
def test_complete_stream_accepts_what_chathy_passes():
    """实测故障(2026-07-30):agent.py 传 `reasoning_effort=`,而 complete_stream 没这个参数
    → 每一轮对话都在 TypeError 上炸,Telegram 侧只看到「服务暂时不可用」。
    CHAT 链清一色思考模型,reasoning 与 content 共吃 max_tokens —— 调用方必须能同时指定
    力度与预算,所以这个参数是契约的一部分,用签名断言钉住。"""
    import inspect

    from xar.chathy import agent
    params = inspect.signature(llm.complete_stream).parameters
    for name in ("tools", "task", "node", "run_id", "max_tokens", "reasoning_effort"):
        assert name in params, f"complete_stream 缺参数 {name}"

    # agent.run_turn 实际传的 kwarg 必须都被接受(防止再次出现签名漂移)
    src = inspect.getsource(agent.run_turn)
    assert "complete_stream(" in src
    for kw in ("reasoning_effort=", "max_tokens=", "task=", "node=", "run_id=", "tools="):
        if kw in src:
            assert kw.rstrip("=") in params, f"agent 传了 {kw} 但 complete_stream 不接受"


def test_reasoning_effort_reaches_the_provider(wired, monkeypatch):
    """力度要真的进请求体 —— 否则思考模型把预算烧在 reasoning 上、content 恒空。"""
    seen: dict = {}

    def _completion(**kw):
        seen.update(kw)
        return iter([_Chunk("ANSWER")])

    monkeypatch.setattr(llm.litellm, "completion", _completion)
    with llm.pinned(("glm-5.2-sub",)):       # supports_reasoning=True
        list(llm.complete_stream(_msgs(), node="chathy", reasoning_effort="high",
                                 max_tokens=16000))
    assert seen.get("reasoning_effort") == "high"
    assert seen.get("stream") is True and seen.get("max_tokens") == 16000
