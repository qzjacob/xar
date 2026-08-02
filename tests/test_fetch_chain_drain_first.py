"""榨干优先:alphapai / aifinmarket 必须额度耗尽才准交棒(2026-08-02 用户裁定)。

审计实测的问题:alphapai 的 `203`(当日额度耗尽)在 2.7 天日志里出现 **0 次**,
而它天天早上 05:00–08:46 沪时就以 `complete`(清单跑完)或 `backoff_giveup`
(42900 短窗限流连击)交棒,之后 15–18 小时零产出 —— **付费额度大量剩在桌上**。
按沪日统计的日产量 80→1564 篇(19.5×方差),没有任何「撞天花板」该有的聚集特征,
也印证了约束不是额度本身。

所以进位规则改成:
  · `drain_first` 段只认 `exhausted()`;
  · 清单跑完但额度还在 → **加深回看窗**继续榨,不交棒;
  · 短退避 → 原地等,不累计弃权;
  · 唯一例外是墙钟安全阀,防止坏掉的供应商把整条链吊死一整天。
"""
from __future__ import annotations

import datetime as dt


from xar.orchestration import fetch_chain as fc


def _stage(name="alphapai", *, exhausted=False, backing_off=False, drain=True, items=None):
    return fc.Stage(
        name=name, available=lambda: True,
        build_worklist=lambda st: (items if items is not None else [["minutes", "a"]]),
        run_item=lambda item, st: 1,
        exhausted=lambda: exhausted, backing_off=lambda: backing_off,
        drain_first=drain)


def _state(**kw):
    st = {"date": "2026-08-02", "stage": 0, "cursor": 0, "b204": 0,
          "order": ["alphapai", "gangtise"], "counts": {"alphapai": {}, "gangtise": {}},
          "stage_log": [], "done": False, "alphapai_start": "2026-07-03",
          "stage_since": {"alphapai": fc._cn_now_iso()}, "drain_rounds": 0}
    st.update(kw)
    return st


# ── 清单跑完但额度还在 → 不交棒,加深回看窗 ──────────────────────────────────
def test_worklist_complete_does_not_advance_when_quota_remains():
    """核心不变量:『跑完』不等于『榨干』。"""
    st = _state()
    before = st["alphapai_start"]
    fc._deepen(st, "alphapai")
    assert st["cursor"] == 0
    assert st["alphapai_start"] < before, "回看窗必须往前推,否则清单不会重新长出来"
    assert st["stage"] == 0, "不得进位"
    assert st["drain_rounds"] == 1


def test_deepen_is_monotonic_across_rounds():
    """连续加深要一轮比一轮更深 —— 否则会在同一个窗上空转。"""
    st = _state()
    seen = []
    for _ in range(3):
        fc._deepen(st, "alphapai")
        seen.append(st["alphapai_start"])
    assert seen == sorted(seen, reverse=True) and len(set(seen)) == 3


def test_deepen_on_non_alphapai_just_rewinds_cursor():
    """没有回看窗旋钮的源(如 aifinmarket)退化成游标归零重扫,同样能继续消耗额度。"""
    st = _state(stage=1, order=["gangtise", "aifinmarket"], cursor=99)
    fc._deepen(st, "aifinmarket")
    assert st["cursor"] == 0 and st["stage"] == 1


# ── 安全阀 ────────────────────────────────────────────────────────────────────
def test_valve_not_expired_within_window():
    st = _state(stage_since={"alphapai": fc._cn_now_iso()})
    assert fc._drain_valve_expired(st, "alphapai") is False


def test_valve_expires_after_configured_hours(monkeypatch):
    """供应商坏掉时必须放行 —— 否则一棒吊死整条链一整天,下游一粒米吃不到。"""
    old = (dt.datetime.now(fc._CN_TZ) - dt.timedelta(hours=11)).isoformat(timespec="seconds")
    st = _state(stage_since={"alphapai": old})
    assert fc._drain_valve_expired(st, "alphapai") is True


def test_valve_can_be_disabled_with_zero(monkeypatch):
    """配 0 = 纯硬阻塞:除非额度耗尽,永不交棒(用户要极端行为时的显式开关)。"""
    class _S:
        fetch_chain_drain_max_hours = 0.0

    monkeypatch.setattr(fc, "get_settings", lambda: _S())
    old = (dt.datetime.now(fc._CN_TZ) - dt.timedelta(hours=99)).isoformat(timespec="seconds")
    st = _state(stage_since={"alphapai": old})
    assert fc._drain_valve_expired(st, "alphapai") is False


def test_valve_without_start_time_does_not_fire():
    """没有起表时间就没有阀 —— 不能凭空判超时(宁可多榨,不可误放)。"""
    st = _state(stage_since={})
    assert fc._drain_valve_expired(st, "alphapai") is False


# ── 注册表:哪些源是榨干优先 ──────────────────────────────────────────────────
def test_drain_first_enabled_on_paid_sources():
    """alphapai 三棒 + aifinmarket 必须开;gangtise 没有额度信号,开了会永远卡住。"""
    reg = fc.stages()
    for name in ("alphapai", "aifinmarket"):
        assert reg[name].drain_first is True, f"{name} 应为榨干优先"
    if "gangtise" in reg:
        assert reg["gangtise"].drain_first is False, \
            "gangtise 无额度谓词(exhausted 恒 False),开榨干优先会把链永久卡死在这一棒"


def test_aifinmarket_exhaustion_requires_all_seats():
    """多账号语义:必须**每个席位**都触顶/冷却才算耗尽,任一账号还有额度就不准交棒。"""
    import inspect

    from xar.providers import aifinmarket

    src = inspect.getsource(aifinmarket.all_seats_exhausted)
    assert "all(" in src, "必须是 all(...) 而不是 any(...) —— 否则一个账号触顶就交棒"
