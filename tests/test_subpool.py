"""云端订阅并行池 subpool 离线测试(monkeypatch llm.pinned + registry + kvstate,零网络)。

验证:三 provider pin 解析、并行分发返回结果、某 provider 触限即冷却 + requeue、
available_pins 跳过冷却中(未到探测期)的 provider。
"""
from __future__ import annotations

import contextlib

import pytest

from xar.models import subpool

_PROV = {"glm-5.2-sub": "zhipu", "minimax-m3-sub": "minimax", "kimi-k3-sub": "moonshot",
         "glm-4.6-sub": "zhipu"}


class _S:
    subpool_pins = "glm-5.2-sub,glm-4.6-sub|minimax-m3-sub|kimi-k3-sub"
    subpool_probe_seconds = 900


class _Spec:
    def __init__(self, prov):
        self.provider = prov


@pytest.fixture
def mem(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(subpool, "get_state", lambda k, d=None: store.get(k, d if d is not None else {}))
    monkeypatch.setattr(subpool, "save_state", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(subpool, "get_settings", lambda: _S())
    monkeypatch.setattr(subpool.reg, "get", lambda mid: _Spec(_PROV.get(mid, mid)))
    monkeypatch.setattr(subpool.llm, "pinned", lambda pin: contextlib.nullcontext())
    return store


def test_provider_pins_parse(mem):
    pins = subpool.provider_pins()
    assert [p for p, _ in pins] == ["zhipu", "minimax", "moonshot"]
    assert pins[0][1] == ("glm-5.2-sub", "glm-4.6-sub")     # GLM 带同家回退链
    assert pins[1][1] == ("minimax-m3-sub",)


def test_run_parallel_distributes_all(mem):
    res = subpool.run_parallel([1, 2, 3, 4, 5], lambda x: x * 10)
    assert {it: r for it, r in res} == {1: 10, 2: 20, 3: 30, 4: 40, 5: 50}


def test_quota_error_cools_all_providers(mem):
    class RateLimitError(Exception):
        pass

    def boom(_x):
        raise RateLimitError("quota exceeded 额度")
    subpool.run_parallel([1, 2, 3, 4, 5, 6], boom)
    st = subpool.status()
    assert all(st.get(p, {}).get("status") == "exhausted"
               for p in ("zhipu", "minimax", "moonshot"))


def test_repeated_failure_cools_provider(mem):
    def boom(_x):
        raise ValueError("auth invalid / bad thesis")   # 持续非额度失败(如鉴权失效)
    subpool.run_parallel([1, 2, 3, 4, 5, 6, 7, 8, 9], boom)
    st = subpool.status()
    assert all(st.get(p, {}).get("status") == "exhausted"    # 连续失败达阈值 → 冷却退出
               for p in ("zhipu", "minimax", "moonshot"))


def test_returns_none_counts_as_failure_and_cools(mem):
    # fn 返回 None(provider 没产出:返空/被拒)也算失败,连续达阈值即冷却
    subpool.run_parallel([1, 2, 3, 4, 5, 6, 7, 8, 9], lambda _x: None)
    st = subpool.status()
    assert all(st.get(p, {}).get("status") == "exhausted"
               for p in ("zhipu", "minimax", "moonshot"))


def test_available_pins_skips_exhausted_before_probe_due(mem):
    mem[subpool.STATE_KEY] = {"zhipu": {"status": "exhausted",
                                        "last_probe_at": "2099-01-01T00:00:00+00:00"}}
    avail = [p for p, _ in subpool.available_pins()]
    assert "zhipu" not in avail                    # 冷却且探测未到期 → 跳过
    assert "minimax" in avail and "moonshot" in avail


def test_available_pins_probes_when_due(mem, monkeypatch):
    # 冷却但探测到期(古老 last_probe)→ probe;桩 llm.complete 成功 → 恢复纳入
    mem[subpool.STATE_KEY] = {"zhipu": {"status": "exhausted",
                                        "last_probe_at": "2000-01-01T00:00:00+00:00"}}
    monkeypatch.setattr(subpool.llm, "complete", lambda *a, **k: "ok")
    avail = [p for p, _ in subpool.available_pins()]
    assert "zhipu" in avail                         # 探针成功 → 恢复
    assert subpool.status()["zhipu"]["status"] == "ok"
