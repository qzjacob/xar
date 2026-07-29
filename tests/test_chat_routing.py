"""Chathy 路由与推理力度(2026-07-29 用户裁定)。

裁定三条:
  ① chat 默认模型 = kimi-k3,回退顺序 glm-5.2 → minimax-m3 → deepseek;
  ② 推理力度默认最大化,但「视任务需求而实际调用」—— 强/推理层拉满、bulk 层不动;
  ③ 链上四席全是思考模型 → 输出预算必须够,否则 reasoning 吃光 max_tokens 就是空回复。
"""
from __future__ import annotations

import json

import pytest

from xar.config import get_settings
from xar.models import llm, registry
from xar.models.registry import Billing
from xar.models.router import POLICIES, TaskClass, resolve, route

_WANT = ["kimi-k3-sub", "glm-5.2-sub", "minimax-m3-sub", "deepseek-v4-pro"]


def test_chat_chain_is_exactly_the_configured_order():
    assert [m.id for m in resolve(TaskClass.CHAT)] == _WANT


@pytest.mark.parametrize("kw", [{"complexity": "high"}, {"complexity": "low"},
                                {"relevance": "high"}, {"input_chars": 200_000}])
def test_chat_chain_survives_dynamic_routing(kw):
    """动态升/降层不得改写这条精确链 —— 否则「默认 kimi」在长/短会话里悄悄变成别的模型。"""
    assert [m.id for m in route(TaskClass.CHAT, **kw)] == _WANT


def test_chat_chain_admits_no_uninvited_candidates():
    """精确链意味着 registry 候选与 env 默认都不掺进来(计费漂移面 = 0)。"""
    ids = {m.id for m in resolve(TaskClass.CHAT)}
    assert ids == set(_WANT)
    assert get_settings().model_strong                        # env 默认确实配着…
    assert not [i for i in ids if i.startswith("env:")]       # …但没被插进链里


def test_chat_head_is_subscription_and_only_the_tail_is_metered():
    chain = resolve(TaskClass.CHAT)
    assert [m.billing for m in chain[:3]] == [Billing.SUBSCRIPTION] * 3
    assert chain[-1].billing == Billing.TOKEN        # deepseek 兜底,排最后


def test_ops_override_still_outranks_the_configured_chain(monkeypatch):
    """route_overrides 是人为显式指令,仍必须排最前(与其余 task 同纪律)。"""
    from xar.models import router
    forced = registry.get("claude-opus-4-8")
    monkeypatch.setattr(router.registry, "override_for", lambda t, c: forced)
    assert [m.id for m in resolve(TaskClass.CHAT)] == ["claude-opus-4-8"] + _WANT


def test_blank_chat_models_falls_back_to_subscription_policy(monkeypatch):
    """清空配置 = 退回常规策略解析;此时也不许链首漂到无界 token 池。"""
    monkeypatch.setattr(get_settings(), "chat_models", "")
    chain = resolve(TaskClass.CHAT)
    assert chain and chain[0].billing == Billing.SUBSCRIPTION


def test_unknown_model_id_is_skipped_not_fatal(monkeypatch):
    """打错一个 id 不该让 chat 整条链失效,也不该静默换成别的模型。"""
    monkeypatch.setattr(get_settings(), "chat_models", "no-such-model,glm-5.2-sub")
    assert [m.id for m in resolve(TaskClass.CHAT)] == ["glm-5.2-sub"]


# --- 推理力度:默认最大化,视任务需求实际调用 -------------------------------------------

def _effort(spec_id: str, want_strong: bool, explicit=None) -> str | None:
    s = get_settings()
    spec = registry.get(spec_id)
    kw = llm._build_kwargs(spec, [{"role": "user", "content": "x"}], 16_000, want_strong,
                           False, s, None, None, explicit)
    return kw.get("reasoning_effort")


@pytest.mark.parametrize("mid", _WANT)
def test_strong_tier_gets_max_effort(mid):
    assert _effort(mid, want_strong=True) == get_settings().model_effort == "high"


def test_bulk_tier_stays_low():
    """夜间 bulk/triage 不跟着拉满 —— 小 token 预算下思考会烧空 content(Phase 4 实测)。"""
    assert _effort("glm-5.2-sub", want_strong=False) == get_settings().model_effort_bulk == "low"


def test_explicit_effort_still_wins_over_the_max_default():
    """『视任务需求而实际调用』:thesis 量产的 low、phanny 的 high 仍压过默认。"""
    assert _effort("glm-5.2-sub", want_strong=True, explicit="low") == "low"
    assert _effort("glm-5.2-sub", want_strong=False, explicit="high") == "high"


def test_bulk_task_routing_and_effort_unchanged():
    """裁定只动强/推理层:bulk 策略与链首保持原样。"""
    for tc in (TaskClass.KG_EXTRACT, TaskClass.EXPERT, TaskClass.THESIS, TaskClass.WECHAT_TRIAGE):
        assert POLICIES[tc].chain_setting == ""
        assert resolve(tc)[0].billing == Billing.SUBSCRIPTION


# --- 输出预算:思考模型的空回复护栏 ---------------------------------------------------

def test_every_chat_candidate_can_emit_real_content_under_max_effort():
    """每一席的 max_output 都要留得下「思考 + 正文」。deepseek 尾席 8192 是链上最小值 ——
    低于 6000 就是已实证的空回复区(GLM/DeepSeek 实测),这条盯住它不再被调低。"""
    for spec in resolve(TaskClass.CHAT):
        assert spec.max_output >= 6000, f"{spec.id} max_output={spec.max_output} 落进空回复区"


def test_chat_budget_is_large_enough_for_the_head_model():
    s = get_settings()
    head = resolve(TaskClass.CHAT)[0]
    assert s.chat_max_tokens >= 8000
    assert min(s.chat_max_tokens, head.max_output) >= 8000, "链首实际拿到的预算被钳得太小"


def test_agent_passes_budget_and_effort_to_the_stream(monkeypatch, seeded_db):
    """护栏落到调用点:chathy 必须真的把 chat_max_tokens + 最大 effort 传下去。"""
    from xar.chathy import agent, sessions

    seen: dict = {}

    def fake_stream(messages, **kw):
        seen.update(kw)
        yield {"type": "final", "message": {"role": "assistant", "content": "ok"}, "usage": None}

    monkeypatch.setattr(llm, "complete_stream", fake_stream)
    sid = sessions.create("budget")["id"]
    list(agent.run_turn(sid, "hi"))

    s = get_settings()
    assert seen["max_tokens"] == s.chat_max_tokens
    assert seen["reasoning_effort"] == s.model_effort
    assert seen["task"] == TaskClass.CHAT


def test_tool_defs_and_results_fit_the_chat_context():
    """24 个工具的 schema + 8k 工具结果不能把 256k(链上最小 context)挤爆。"""
    from xar.capabilities import registry as caps
    defs_chars = len(json.dumps(caps.openai_tool_defs(), ensure_ascii=False))
    smallest_ctx = min(m.context_window for m in resolve(TaskClass.CHAT))
    assert defs_chars < smallest_ctx, "工具定义本身就超过了最小上下文"
