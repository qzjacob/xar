"""Phanny 只用订阅模型(2026-07-25 用户裁定:移除 deepseek,仅 minimax/kimi/glm)。

回归护栏:proposer/rebut 的 _primary_pin 与 critic 的 _critic_pins 里**不得出现任何按 token
计费的模型**——deepseek-v4-pro 曾在 propose/rebut/critic 日烧 ~$7.6,与「订阅额度充分利用、
零计量支出」目标冲突。同时校验 PHANNY 路由策略 billing_pref=subscription(未钉扎路径也不漂回计量)。
"""
from __future__ import annotations

from xar.models import registry as reg
from xar.models.router import POLICIES, TaskClass
from xar.phanny import debate, engine


def _metered(model_ids) -> list[str]:
    """钉扎链里按 token 计费的模型(订阅/本地均为 usd=0,不算)。"""
    out = []
    for mid in model_ids:
        spec = reg.get(mid)
        if spec is not None and spec.billing == reg.Billing.TOKEN:
            out.append(mid)
    return out


def test_primary_pin_is_subscription_only(monkeypatch):
    # docker 路径(无 host 执行器)——正是曾经落 deepseek 的分支
    monkeypatch.setattr(engine, "_host_executor", lambda: None)
    pin = engine._primary_pin()
    assert pin, "primary pin 不得为空"
    assert _metered(pin) == [], f"proposer/rebut 钉扎链含计量模型: {_metered(pin)}"
    assert "deepseek-v4-pro" not in pin


def test_critic_pins_are_subscription_only():
    pins = debate._critic_pins()
    assert pins, "critic 钉扎链不得为空"
    flat = [mid for pin in pins for mid in pin]
    assert _metered(flat) == [], f"critic 钉扎链含计量模型: {_metered(flat)}"
    assert "deepseek-v4-pro" not in flat


def test_critic_pins_cover_three_vendors():
    """多 LLM 对抗仍在:三家不同 provider(zhipu/moonshot/minimax)都要有 critic 头。"""
    heads = [pin[0] for pin in debate._critic_pins()]
    provs = {reg.get(h).provider for h in heads if reg.get(h)}
    assert provs >= {"zhipu", "moonshot", "minimax"}, f"critic 厂商覆盖不足: {provs}"


def test_phanny_route_policies_prefer_subscription():
    for tc in (TaskClass.PHANNY_VERDICT, TaskClass.PHANNY_CHALLENGE):
        assert POLICIES[tc].prefer_billing == reg.Billing.SUBSCRIPTION.value, (
            f"{tc.value} 的 prefer_billing 应为 subscription(防未钉扎路径漂回计量)")
