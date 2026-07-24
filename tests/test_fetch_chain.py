"""fetch_chain 接力状态机离线测试(monkeypatch kvstate + stages + settings + 时钟)。

验证:相关性排序(种子优先→综合分降序)、alphapai 清单序(纪要→主题→其余)、CSV order 解析、
额度中途耗尽进位、清单跑完进位、不可用跳过、204 退避暂停+3 连击弃权、时间片间停+崩溃续传、
沪日滚动重挂 pinned 序、done 后当日 idle、disabled 跳过。全部离线(无 DB)。
"""
from __future__ import annotations

import pytest

from xar.orchestration import fetch_chain as fc


class _S:
    fetch_chain_enabled = True
    fetch_chain_order = "alphapai,gangtise,aifinmarket,alphapai_agents"
    fetch_chain_slice_seconds = 1000.0
    fetch_chain_refetch_days = 3
    fetch_chain_repoll_seconds = 3600
    alphapai_lookback_days = 30
    fetch_chain_alphapai_rest_top = 60
    fetch_chain_agent_companies = 30
    fetch_chain_aifin_chunk = 25
    fetch_chain_gangtise_chunk = 10
    alphapai_agent_modes = "2,7"
    gangtise_core_size = 30
    enable_alphapai = True


@pytest.fixture
def mem(monkeypatch):
    """内存 kvstate + 固定 settings/日期/相关性序(状态机测试不碰真 DB / 真 provider)。"""
    store: dict = {}
    monkeypatch.setattr(fc, "get_state",
                        lambda k, d=None: store.get(k, (d if d is not None else {})))
    monkeypatch.setattr(fc, "save_state", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(fc, "get_settings", lambda: _S())
    monkeypatch.setattr(fc, "_cn_today", lambda: "2026-07-24")
    monkeypatch.setattr(fc, "_cn_now_iso", lambda: "2026-07-24T09:00:00+08:00")
    monkeypatch.setattr(fc, "universe_priority_order", lambda: ["c0", "c1", "c2", "c3", "c4"])
    return store


def _autoclock(monkeypatch):
    """每次调用 +1.0 的单调时钟 —— 配 budget_seconds 精确控制一片跑几个 item。"""
    t = {"v": 0.0}

    def mono():
        v = t["v"]
        t["v"] += 1.0
        return v
    monkeypatch.setattr(fc.time, "monotonic", mono)
    return t


def _fake_stage(name, *, items, exhaust_after=None, avail=True, backoff_flag=None, ran=None):
    ran = [] if ran is None else ran
    calls = {"n": 0}

    def run_item(item, st):
        ran.append(item)
        calls["n"] += 1
        return 1

    def exhausted():
        return exhaust_after is not None and calls["n"] >= exhaust_after

    def backing_off():
        return bool(backoff_flag and backoff_flag[0])

    return fc.Stage(name=name, available=lambda: avail, build_worklist=lambda st: list(items),
                    run_item=run_item, exhausted=exhausted, backing_off=backing_off), ran


def _reg(monkeypatch, **stages):
    full = {n: _fake_stage(n, items=[])[0]
            for n in ("alphapai", "gangtise", "aifinmarket", "alphapai_agents")}
    full.update(stages)
    monkeypatch.setattr(fc, "stages", lambda: full)
    return full


def _log(store):
    return [(e["stage"], e["ended"]) for e in store[fc.STATE_KEY]["stage_log"]]


# ── 排序 / 清单构造 ──────────────────────────────────────────────────────────────
def test_universe_priority_seeds_first_then_composite(monkeypatch):
    import xar.ingestion.registry as reg
    import xar.ontology.coverage360 as cov
    import xar.ontology.debates as deb
    monkeypatch.setattr(reg, "COMPANIES", [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}])
    monkeypatch.setattr(deb, "seed_company_ids", lambda: {"c"})
    monkeypatch.setattr(cov, "coverage_all", lambda: {
        "a": {"composite": 0.9}, "b": {"composite": 0.2},
        "c": {"composite": 0.1}, "d": {"composite": 0.5}})
    order = fc.universe_priority_order()
    assert order[0] == "c"                      # 种子优先,不看综合分
    assert order[1:] == ["a", "d", "b"]         # 其余按综合分降序


def test_universe_priority_degrades_without_coverage(monkeypatch):
    import xar.ingestion.registry as reg
    import xar.ontology.coverage360 as cov
    import xar.ontology.debates as deb
    monkeypatch.setattr(reg, "COMPANIES", [{"id": "a"}, {"id": "b"}, {"id": "c"}])
    monkeypatch.setattr(deb, "seed_company_ids", lambda: {"b"})

    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(cov, "coverage_all", boom)
    order = fc.universe_priority_order()
    assert order[0] == "b"                       # 种子优先;其余保注册表序
    assert order[1:] == ["a", "c"]


def test_alphapai_worklist_minutes_theme_rest(monkeypatch):
    import xar.ingestion.registry as reg
    from xar.providers import alphapai
    monkeypatch.setattr(fc, "get_settings", lambda: _S())
    monkeypatch.setattr(alphapai, "has_cjk_name", lambda cid: cid in ("c0", "c1"))
    monkeypatch.setattr(reg, "THEMES", {"cpo": {"nameCn": "光模块"}})
    wl = fc._alphapai_worklist({"pinned_ids": ["c0", "c1", "c2"]})
    assert [it[0] for it in wl] == ["minutes", "minutes", "theme", "rest", "rest"]
    assert wl[0] == ["minutes", "c0"]
    assert wl[2] == ["theme", "光模块"]


def test_order_csv_unknown_names_skipped(monkeypatch):
    class _S2(_S):
        fetch_chain_order = "alphapai,bogus,gangtise"
    monkeypatch.setattr(fc, "get_settings", lambda: _S2())
    _reg(monkeypatch)
    assert fc._resolved_order() == ["alphapai", "gangtise"]


# ── 状态机进位 ──────────────────────────────────────────────────────────────────
def test_stage_advances_on_quota_mid_worklist(mem, monkeypatch):
    _autoclock(monkeypatch)
    a_ran: list = []
    _reg(monkeypatch,
         alphapai=_fake_stage("alphapai", items=[["m", i] for i in range(10)],
                              exhaust_after=2, ran=a_ran)[0],
         gangtise=_fake_stage("gangtise", items=[["g", 0]])[0],
         aifinmarket=_fake_stage("aifinmarket", items=[["c", 0]])[0])
    out = fc.step()
    assert out["done"] is True
    assert len(a_ran) == 2                        # 额度耗尽 → 只跑 2 个就 fallback
    logs = _log(mem)
    assert ("alphapai", "quota") in logs
    assert ("gangtise", "complete") in logs
    assert ("aifinmarket", "complete") in logs


def test_worklist_complete_advances(mem, monkeypatch):
    _autoclock(monkeypatch)
    g_ran: list = []
    _reg(monkeypatch,
         gangtise=_fake_stage("gangtise", items=[["g", 0], ["g", 1], ["g", 2]], ran=g_ran)[0])
    out = fc.step()
    assert out["done"] is True
    assert len(g_ran) == 3
    logs = _log(mem)
    assert ("alphapai", "complete") in logs       # 空清单 → 立即 complete
    assert ("gangtise", "complete") in logs


def test_unavailable_stage_skipped(mem, monkeypatch):
    _autoclock(monkeypatch)
    _reg(monkeypatch,
         alphapai=_fake_stage("alphapai", items=[["m", 0]], avail=False)[0])
    out = fc.step()
    assert out["done"] is True
    assert ("alphapai", "unavailable") in _log(mem)


def test_aifin_exhausts_via_predicate(mem, monkeypatch):
    _autoclock(monkeypatch)
    f_ran: list = []
    _reg(monkeypatch,
         aifinmarket=_fake_stage("aifinmarket", items=[["c", i] for i in range(9)],
                                exhaust_after=3, ran=f_ran)[0])
    fc.step()
    assert len(f_ran) == 3
    assert ("aifinmarket", "quota") in _log(mem)


# ── 204 退避 ─────────────────────────────────────────────────────────────────────
def test_204_mid_item_retries_not_skips(mem, monkeypatch):
    """本 item 执行中触发 204(run_item 返回 0 + backing_off 翻真)→ cursor 不前进,退避到期原地重试
    (回归:旧版把该 item 当完成、cursor 前进过它 → 当日永久丢一个高相关性 item)。"""
    flag = [False]
    ran: list = []
    calls = {"n": 0}

    def run_item(item, st):
        ran.append(item)
        calls["n"] += 1
        if item == ["m", 0] and calls["n"] == 1:
            flag[0] = True                          # 首次跑 item0 触发 204 退避
            return 0
        return 1

    stage = fc.Stage(name="alphapai", available=lambda: True,
                     build_worklist=lambda st: [["m", 0], ["m", 1]],
                     run_item=run_item, exhausted=lambda: False,
                     backing_off=lambda: flag[0])
    _reg(monkeypatch, alphapai=stage)
    _autoclock(monkeypatch)
    o1 = fc.step()
    assert o1.get("paused") == "backoff" and o1["b204"] == 1
    assert mem[fc.STATE_KEY]["cursor"] == 0        # 未越过 item0
    assert ran == [["m", 0]]
    flag[0] = False                                 # 退避到期
    _autoclock(monkeypatch)
    o2 = fc.step()
    assert ran == [["m", 0], ["m", 0], ["m", 1]]   # item0 被重试(而非跳过),再到 item1
    assert o2["done"] is True


def test_204_backoff_pauses_then_gives_up(mem, monkeypatch):
    flag = [True]
    a_ran: list = []
    _reg(monkeypatch,
         alphapai=_fake_stage("alphapai", items=[["m", 0]], backoff_flag=flag, ran=a_ran)[0],
         gangtise=_fake_stage("gangtise", items=[["g", 0]])[0])
    o1 = fc.step()
    assert o1.get("paused") == "backoff" and o1["b204"] == 1
    o2 = fc.step()
    assert o2.get("paused") == "backoff" and o2["b204"] == 2
    fc.step()                                     # 第 3 片:弃权进位
    assert a_ran == []                            # 退避期间从不跑 alphapai item
    logs = _log(mem)
    assert ("alphapai", "backoff_giveup") in logs
    assert ("gangtise", "complete") in logs


# ── 时间片预算 + 崩溃续传 ────────────────────────────────────────────────────────
def test_slice_budget_stops_and_resumes(mem, monkeypatch):
    a_ran: list = []
    _reg(monkeypatch,
         alphapai=_fake_stage("alphapai", items=[["m", i] for i in range(5)], ran=a_ran)[0])
    _autoclock(monkeypatch)
    o1 = fc.step(budget_seconds=2.5)              # t0=0;检查 1,2<2.5 → 2 个;3≥2.5 停
    assert o1["ran"] == 2 and o1["done"] is False
    assert mem[fc.STATE_KEY]["cursor"] == 2       # 每 item 落盘 → 精确续传
    _autoclock(monkeypatch)                        # 新一片时钟复位
    o2 = fc.step(budget_seconds=2.5)
    assert o2["ran"] == 2
    assert mem[fc.STATE_KEY]["cursor"] == 4
    assert a_ran == [["m", 0], ["m", 1], ["m", 2], ["m", 3]]


# ── 日界 / idle / 开关 ───────────────────────────────────────────────────────────
def test_day_rollover_resets_and_repins(mem, monkeypatch):
    mem[fc.STATE_KEY] = {
        "date": "2026-07-23", "stage": 4, "cursor": 0, "b204": 0,
        "order": ["alphapai", "gangtise", "aifinmarket", "alphapai_agents"],
        "pinned_ids": ["old"], "last_done_date": "2026-07-23",
        "counts": {"alphapai": {"minutes": 99}}, "stage_log": [], "done": True}
    _reg(monkeypatch)
    _autoclock(monkeypatch)
    fc.step()                                      # _cn_today → 2026-07-24 → 重挂
    st = mem[fc.STATE_KEY]
    assert st["date"] == "2026-07-24"
    assert st["pinned_ids"] == ["c0", "c1", "c2", "c3", "c4"]   # 重新定序
    assert st["counts"] == {n: {} for n in st["order"]}         # 计数清零
    assert st["alphapai_start"] == "2026-07-21"                 # 已完成过 → 3d 窗(非 30d)


def test_first_ever_run_uses_lookback_window(mem, monkeypatch):
    _reg(monkeypatch)
    _autoclock(monkeypatch)
    fc.step()                                      # last_done_date=None → 首轮 30d
    assert mem[fc.STATE_KEY]["alphapai_start"] == "2026-06-24"  # 2026-07-24 - 30d


def test_done_idles_within_repoll_cooldown(mem, monkeypatch):
    monkeypatch.setattr(fc.time, "time", lambda: 5000.0)   # 5000-4000=1000 < 3600 冷却内
    mem[fc.STATE_KEY] = {
        "date": "2026-07-24", "stage": 4, "cursor": 0, "b204": 0,
        "order": ["alphapai", "gangtise", "aifinmarket", "alphapai_agents"],
        "pinned_ids": ["c0"], "last_done_date": "2026-07-24", "passes": 1,
        "done_at_epoch": 4000.0, "counts": {}, "stage_log": [], "done": True}
    a_ran: list = []
    _reg(monkeypatch, alphapai=_fake_stage("alphapai", items=[["m", 0]], ran=a_ran)[0])
    out = fc.step()
    assert "idle" in out and out["passes"] == 1
    assert a_ran == []                             # 冷却内空转,不跑任何 stage


def test_repoll_starts_new_pass_after_cooldown(mem, monkeypatch):
    monkeypatch.setattr(fc.time, "time", lambda: 10000.0)  # 10000-4000=6000 > 3600 → 重开一轮
    mem[fc.STATE_KEY] = {
        "date": "2026-07-24", "stage": 4, "cursor": 0, "b204": 0,
        "order": ["alphapai", "gangtise", "aifinmarket", "alphapai_agents"],
        "pinned_ids": ["c0", "c1"], "last_done_date": "2026-07-24", "passes": 1,
        "done_at_epoch": 4000.0, "counts": {"alphapai": {"minutes": 99}},
        "stage_log": [], "done": True}
    a_ran: list = []
    _reg(monkeypatch, alphapai=_fake_stage("alphapai", items=[["m", 0]], ran=a_ran)[0])
    _autoclock(monkeypatch)
    out = fc.step()
    st = mem[fc.STATE_KEY]
    assert st["passes"] == 2                        # 新一轮
    assert a_ran == [["m", 0]]                      # alphapai 段在新轮里被重跑(捕捉新内容)
    assert st["alphapai_start"] == "2026-07-21"     # 窗口刷新到近端(3d)
    assert out["done"] is True                      # 其余空阶段 → 本轮又跑完


def test_repoll_disabled_idles_forever(mem, monkeypatch):
    class _Soff(_S):
        fetch_chain_repoll_seconds = 0
    monkeypatch.setattr(fc, "get_settings", lambda: _Soff())
    monkeypatch.setattr(fc.time, "time", lambda: 10_000_000.0)
    mem[fc.STATE_KEY] = {
        "date": "2026-07-24", "stage": 4, "cursor": 0, "b204": 0,
        "order": ["alphapai", "gangtise", "aifinmarket", "alphapai_agents"],
        "pinned_ids": ["c0"], "last_done_date": "2026-07-24", "passes": 1,
        "done_at_epoch": 4000.0, "counts": {}, "stage_log": [], "done": True}
    _reg(monkeypatch)
    assert "idle" in fc.step()                      # repoll=0 → 跑完即空转到次日


def test_alphapai_worklist_rest_covers_all_when_zero(monkeypatch):
    import xar.ingestion.registry as reg
    from xar.providers import alphapai

    class _S0(_S):
        fetch_chain_alphapai_rest_top = 0
    monkeypatch.setattr(fc, "get_settings", lambda: _S0())
    monkeypatch.setattr(alphapai, "has_cjk_name", lambda cid: True)
    monkeypatch.setattr(reg, "THEMES", {})
    wl = fc._alphapai_worklist({"pinned_ids": ["c0", "c1", "c2"]})
    assert [it[1] for it in wl if it[0] == "rest"] == ["c0", "c1", "c2"]   # 0=全库


def test_disabled_flag_skips(mem, monkeypatch):
    class _Off(_S):
        fetch_chain_enabled = False
    monkeypatch.setattr(fc, "get_settings", lambda: _Off())
    assert fc.step().get("skipped") == "fetch_chain disabled"
