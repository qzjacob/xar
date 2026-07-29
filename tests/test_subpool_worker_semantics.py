"""M2:subpool 的返回值语义 = **provider 健康信号**,不是「有没有产出」。

误诊闭环(2026-07-29 修):把 `rejected`(模型答了但违反论点纪律)当成 provider 故障,
连续三次就冷却整家订阅 —— 于是「论点校验严」被翻译成「三家订阅全挂」,thesis 停摆期间
看到的正是这个假象。只有 `llm_failed`(没吐出可用 JSON)才是真的 provider 有问题。
"""
from __future__ import annotations

import pytest

from xar.orchestration import subpool_worker as sw


class _S:
    subpool_enabled = True
    subpool_batch = 3
    subpool_pins = "glm-5.2-sub|minimax-m3-sub|kimi-k3-sub"
    subpool_thesis_stale_hours = 24
    subpool_idle_seconds = 30


@pytest.fixture
def wired(monkeypatch):
    from xar.models import llm
    monkeypatch.setattr(llm, "new_batch_run_id", lambda p="batch": f"{p}-test")
    monkeypatch.setattr(sw, "get_settings", lambda: _S())
    monkeypatch.setattr(sw.subpool, "available_pins",
                        lambda: [("zhipu", ("glm-5.2-sub",))])
    monkeypatch.setattr(sw.subpool, "status", lambda: {})
    # _pick_companies 现返回 [(cid, because)] —— because 会写进 changed_because 讲因果
    monkeypatch.setattr(sw, "_pick_companies",
                        lambda n: [("a", "信号/争论挑战"), ("b", "季报兑现 X"), ("c", None)])
    # run_parallel 直接串行跑,避免线程干扰断言
    monkeypatch.setattr(sw.subpool, "run_parallel",
                        lambda items, fn: [(it, fn(it)) for it in items])
    return monkeypatch


@pytest.mark.parametrize("status,healthy", [
    ("built", True),        # 正常产出
    ("skipped", True),      # 幂等跳过:provider 没被调用,谈不上故障
    ("rejected", True),     # ★ 模型答了但违反纪律 —— provider 健康,内容不合格
    ("no_data", True),      # ★ 证据不足,压根没调模型 —— provider 健康
    ("llm_failed", False),  # ★ 没吐出可用 JSON —— 真 provider 故障,该冷却
])
def test_health_signal_semantics(wired, monkeypatch, status, healthy):
    from xar.research import thesis
    monkeypatch.setattr(thesis, "build", lambda cid, **k: {"status": status})
    out = sw.run_once()
    assert out["statuses"].get(status if healthy else "llm_failed") == 3
    assert (out["statuses"].get("llm_failed", 0) == 0) is healthy


def test_built_counted(wired, monkeypatch):
    from xar.research import thesis
    monkeypatch.setattr(thesis, "build", lambda cid, **k: {"status": "built"})
    assert sw.run_once()["built"] == 3


def test_because_threaded_into_thesis_build(wired, monkeypatch):
    """重建触发原因必须一路传到 thesis.build —— 否则新版本的 changed_because 讲不出因果
    (一份因财报重建的论点看上去与例行刷新毫无区别)。"""
    seen: dict = {}
    from xar.research import thesis
    monkeypatch.setattr(thesis, "build",
                        lambda cid, **k: seen.update({cid: k.get("because")}) or {"status": "built"})
    sw.run_once()
    assert seen["a"] == "信号/争论挑战" and seen["b"] == "季报兑现 X" and seen["c"] is None
