"""M3 实测修复:**billing 偏好必须压过 env 默认**。

实测事故(2026-07-29):`xar thesis build` 的大 dossier 触发动态升层 cheap_bulk→strong,
而 `_env_spec` 把 config 默认的 `model_strong=deepseek/deepseek-v4-pro`(TOKEN,max_output=8192)
无条件插在链首 —— 于是 thesis 的 16000 max_tokens 被钳到 8192,JSON 截断成
「no JSON object found」,整批落 llm_failed。生产 llm_usage 实证:output_tokens 恒为 8191/8192/8194。

这同时违反两条既定纪律:
  ① route_plan 自己写明的「bulk 升级仍走 SUBSCRIPTION 优先,绝不越到无界 token 池」;
  ② 用户对 Phanny 的裁定「只保留订阅项下 minimax/kimi/glm,移除按 token 计费的 deepseek」——
     未钉扎路径此前仍会漂回 deepseek。
"""
from __future__ import annotations

import pytest

from xar.models.registry import Billing
from xar.models.router import POLICIES, TaskClass, resolve, route

_SUB_PREF = [tc for tc, p in POLICIES.items() if p.prefer_billing == Billing.SUBSCRIPTION.value]
_TOKEN_PREF = [tc for tc, p in POLICIES.items() if p.prefer_billing == Billing.TOKEN.value]
_ANY_PREF = [tc for tc, p in POLICIES.items() if p.prefer_billing == "any"]


@pytest.mark.parametrize("tc", _SUB_PREF, ids=lambda t: t.value)
def test_subscription_tasks_never_head_a_token_model(tc):
    """偏好订阅的任务:静态与动态升层两种解析,链首都必须是订阅模型。"""
    for chain in (resolve(tc), route(tc, complexity="high"), route(tc, relevance="high")):
        assert chain, tc.value
        assert chain[0].billing == Billing.SUBSCRIPTION, \
            f"{tc.value} 链首漂到计量模型 {chain[0].id}"


@pytest.mark.parametrize("tc", _SUB_PREF, ids=lambda t: t.value)
def test_token_model_still_available_as_fallback(tc):
    """降级不是丢弃 —— env 默认仍在链里可作回退,只是不许抢在订阅候选前。"""
    ids = [s.id for s in route(tc, complexity="high")]
    assert "deepseek-v4-pro" in ids, f"{tc.value} 把 env 默认整个丢了,应保留为回退"
    assert ids.index("deepseek-v4-pro") > 0


def test_thesis_head_can_hold_full_token_budget():
    """thesis_max_tokens=16000:链首 max_output 必须 ≥ 它,否则结构化输出必被截断。
    这是「JSON 截断→llm_failed」的直接护栏。"""
    from xar.config import get_settings
    want = get_settings().thesis_max_tokens
    for chain in (resolve(TaskClass.THESIS), route(TaskClass.THESIS, complexity="high")):
        head = chain[0]
        assert head.max_output >= want, \
            f"thesis 链首 {head.id} max_output={head.max_output} < thesis_max_tokens={want}"


@pytest.mark.parametrize("tc", _TOKEN_PREF, ids=lambda t: t.value)
def test_token_preferring_tasks_unchanged(tc):
    """偏好 TOKEN 的任务(synth/editor/audit/earnings_judge)行为不得被这次修复改动。
    注:chat 已于 2026-07-29 改为订阅精确链(见 test_chat_routing.py),不再属于本组。"""
    assert resolve(tc)[0].billing == Billing.TOKEN, tc.value


@pytest.mark.parametrize("tc", _ANY_PREF, ids=lambda t: t.value)
def test_any_billing_tasks_keep_env_default_first(tc):
    """billing='any':env 默认继续享有优先权(该修复只针对有明确偏好的任务)。"""
    assert resolve(tc), tc.value


def test_ops_override_still_wins(monkeypatch):
    """ops 的 route_overrides 是人为显式指令,必须仍排最前(哪怕计费方式不符偏好)。"""
    from xar.models import registry as reg
    from xar.models import router

    forced = reg.get("deepseek-v4-pro")
    monkeypatch.setattr(router.registry, "override_for", lambda t, c: forced)
    assert resolve(TaskClass.THESIS)[0].id == "deepseek-v4-pro"
