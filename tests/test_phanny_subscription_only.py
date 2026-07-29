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


# ── 额度冷却(2026-07-29:phanny 拖死 glm_worker 单线程 run_once 的两半修复之一)──────────
def test_critic_pins_skip_cooling_provider(monkeypatch):
    """某家订阅触限冷却 → 不再把它排进 critic 面板(此前每轮每名都白烧一发已知无额度的调用)。"""
    from xar.models import subpool
    monkeypatch.setattr(subpool, "cooling", lambda prov: prov == "moonshot")
    pins = debate._critic_pins()
    flat = [mid for pin in pins for mid in pin]
    assert "kimi-k3-sub" not in flat, f"冷却中的 moonshot 仍被排进面板: {pins}"
    provs = {reg.get(pin[0]).provider for pin in pins if reg.get(pin[0])}
    assert provs >= {"zhipu", "minimax"}, f"未冷却的厂商被误伤: {provs}"


def test_critic_pins_dedupe_by_provider_when_head_cools(monkeypatch):
    """头冷却 → 塌缩到 glm 兜底;但多个头塌缩到同一家只保留一个 ——
    4 个 critic 全变成同一个 glm 就是单模型自博,失去「异厂商」的意义。"""
    from xar.models import subpool
    monkeypatch.setattr(subpool, "cooling", lambda prov: prov in ("moonshot", "minimax"))
    pins = debate._critic_pins()
    provs = [reg.get(pin[0]).provider for pin in pins if reg.get(pin[0])]
    assert len(provs) == len(set(provs)), f"同一 provider 重复占位: {pins}"
    assert provs == ["zhipu"], f"应只剩未冷却的 zhipu: {pins}"


def test_critic_pins_empty_when_all_providers_cool(monkeypatch):
    """全部订阅厂商冷却 → 面板为空。run_debate 据此跳过辩论,而不是拿空票空跑 max_rounds 轮
    (`agree_ok = bool(votes)` 决定空票必定不收敛 → 5 轮 × 8000-token rebut 纯浪费)。"""
    from xar.models import subpool
    monkeypatch.setattr(subpool, "cooling", lambda prov: True)
    assert debate._critic_pins() == []


def test_phanny_route_policies_prefer_subscription():
    for tc in (TaskClass.PHANNY_VERDICT, TaskClass.PHANNY_CHALLENGE):
        assert POLICIES[tc].prefer_billing == reg.Billing.SUBSCRIPTION.value, (
            f"{tc.value} 的 prefer_billing 应为 subscription(防未钉扎路径漂回计量)")
