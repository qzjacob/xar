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
    def _merge(k, field, patch):
        """如实模拟 `merge_state_field` 的**浅合并**语义(2026-08-02):
        只覆盖 patch 里出现的字段,不整体替换子对象 —— 桩若写成整体替换,
        测出来的就不是线上行为(exhaust_count 这类过渡计数会被悄悄抹掉)。"""
        blob = store.setdefault(k, {})
        blob[field] = {**blob.get(field, {}), **patch}

    monkeypatch.setattr(subpool, "merge_state_field", _merge)
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


# ── M2:并发冷却的读-改-写竞态(既有 flake 的真因,生产同样中招)────────────────────
def test_concurrent_cooling_never_loses_an_update(mem):
    """三个 provider 线程同时冷却时,sub_quota 这张 JSON blob 的读-改-写必须互斥。
    未加锁时后写覆盖先写 → 被覆盖的那家仍被当作可用,继续派活直到再失败三次。
    这正是 test_repeated_failure / returns_none 两条断言此前间歇性变红的原因。"""
    def boom(_x):
        raise ValueError("auth invalid")

    subpool.run_parallel(list(range(30)), boom)
    st = subpool.status()
    assert all(st.get(p, {}).get("status") == "exhausted"
               for p in ("zhipu", "minimax", "moonshot")), st


def test_mark_holds_lock_under_hammering(mem):
    """直接压 _mark:100 次并发标记后三家状态齐全,一条都不丢。"""
    import threading

    provs = ["zhipu", "minimax", "moonshot"]
    threads = [threading.Thread(target=subpool._mark, args=(provs[i % 3],),
                                kwargs={"ok": False, "reason": "hammer"})
               for i in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    st = subpool.status()
    assert all(st.get(p, {}).get("status") == "exhausted" for p in provs), st


# ── 毒项 vs provider 故障的归因(2026-07-31 审核 P2-3 回归)────────────────────────
def test_poison_item_does_not_cool_a_healthy_provider(mem):
    """单个「与 provider 健康无关」的毒项,不得冷掉任何一家订阅。

    此前 item 级失败与 provider 级失败共用同一个 fails 计数器:毒项被 requeue 后
    若连续三次落回同一个 worker,就会冷掉一家完全健康的 provider —— 白丢一份 5h 额度窗。
    现在只有**该项的首次失败**记到 provider 头上,再次失败即判毒项跳过。
    """
    def fn(x):
        if x == "poison":
            raise ValueError("thesis.build DB error —— 与 provider 无关")
        return f"ok:{x}"

    items = ["poison"] + [f"good{i}" for i in range(8)]
    out = subpool.run_parallel(items, fn)

    st = subpool.status()
    cooled = [p for p in ("zhipu", "minimax", "moonshot")
              if st.get(p, {}).get("status") == "exhausted"]
    assert not cooled, f"毒项冷掉了健康 provider: {cooled}"

    got = {it: res for it, res in out}
    assert got["poison"] is None, "毒项应放弃,result 为 None 交调用方下轮重试"
    assert all(got[f"good{i}"] == f"ok:good{i}" for i in range(8)), "健康项必须全部完成"


def test_poison_item_is_dropped_not_requeued_forever(mem):
    """毒项不得被无限 requeue —— 那会把所有 provider 逐一冷却直到全灭。"""
    calls = {"n": 0}

    def fn(x):
        calls["n"] += 1
        raise ValueError("always fails")

    subpool.run_parallel(["only_poison"], fn)
    # 首次失败(计 provider 账 + requeue)+ 第二次失败(判毒项丢弃)= 至多 2 次
    assert calls["n"] <= 2, f"毒项被重试了 {calls['n']} 次,说明仍在无限 requeue"


def test_genuinely_broken_provider_still_cools(mem):
    """归因改动不得削弱原有能力:provider 真坏时(在**不同项**上连续失败)照旧冷却。"""
    subpool.run_parallel([f"item{i}" for i in range(12)],
                         lambda _x: (_ for _ in ()).throw(ValueError("auth invalid")))
    st = subpool.status()
    assert any(st.get(p, {}).get("status") == "exhausted"
               for p in ("zhipu", "minimax", "moonshot")), "真坏的 provider 仍必须被冷却"
