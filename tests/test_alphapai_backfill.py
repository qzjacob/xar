"""alphapai 扩量抓取 + 三档 drain 优先序回归(2026-07-26 目标:让 alphapai 吃满本地 GPU)。

覆盖:① 过去一年逐窗**新→旧**;② 窗口推进游标;③ 主题维含宏观/策略/资金流;④ 补上的 3 个召回类型;
⑤ x/finnhub 在 drain 里排到 alphapai 之后(三档 tier)。全离线。
"""
from __future__ import annotations

import datetime as dt

import pytest

from xar.orchestration import fetch_chain as fc
from xar.pipeline_priority import STRICT_PRIORITY_ORDER, tier_order_sql


class _S:
    alphapai_backfill_enabled = True
    alphapai_backfill_days = 365
    alphapai_backfill_window_days = 30
    alphapai_theme_dims = "industry,strategy,macro,moneyflow"
    fetch_chain_alphapai_rest_top = 0
    fetch_chain_alphapai_theme_first = True
    fetch_chain_order = "alphapai,alphapai_backfill,gangtise,aifinmarket,alphapai_agents"
    fetch_chain_enabled = True
    fetch_chain_slice_seconds = 1000.0
    fetch_chain_refetch_days = 3
    alphapai_lookback_days = 30
    fetch_chain_repoll_seconds = 3600
    fetch_chain_agent_companies = 30
    fetch_chain_aifin_chunk = 25
    fetch_chain_gangtise_chunk = 10
    alphapai_agent_modes = "2,7"
    gangtise_core_size = 30
    enable_alphapai = True


@pytest.fixture
def st(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(fc, "get_state", lambda k, d=None: store.get(k, d if d is not None else {}))
    monkeypatch.setattr(fc, "save_state", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(fc, "get_settings", lambda: _S())
    return store


# ── ① 时间窗:过去一年,新→旧 ────────────────────────────────────────────────────
def test_backfill_windows_span_a_year_newest_first(st):
    wins = fc._bf_windows()
    assert len(wins) == 13, f"365d/30d 应切出 13 窗,实得 {len(wins)}"
    starts = [w[0] for w in wins]
    assert starts == sorted(starts, reverse=True), "窗口必须**新→旧**排列"
    oldest = dt.date.fromisoformat(wins[-1][0])
    span = (dt.date.today() - oldest).days
    assert 360 <= span <= 400, f"回溯深度应≈一年,实得 {span} 天"
    # 每窗宽度 = window_days,且相邻窗首尾相接(无空洞)
    for (s1, e1), (s2, e2) in zip(wins, wins[1:]):
        assert (dt.date.fromisoformat(e1) - dt.date.fromisoformat(s1)).days == 30
        assert e2 == s1, "相邻窗应首尾相接,不留空洞"


def test_backfill_worklist_uses_current_window_and_advances(st, monkeypatch):
    from xar.providers import alphapai
    monkeypatch.setattr(alphapai, "has_cjk_name", lambda cid: True)
    state = {"pinned_ids": ["c0", "c1"]}
    wl = fc._bf_worklist(state)
    wins = fc._bf_windows()
    co = [i for i in wl if i[0] == "bf_co"]
    assert len(co) == 2
    assert co[0][2:] == [wins[0][0], wins[0][1]], "首个 pass 应走最新窗"
    assert wl[-1][0] == "bf_advance", "清单末尾须有窗口推进标记"
    # 执行 advance → 游标 +1 → 下一 pass 走更旧的窗
    fc._bf_run(wl[-1], state)
    assert st[fc.BF_KEY]["win"] == 1
    wl2 = fc._bf_worklist(state)
    assert [i for i in wl2 if i[0] == "bf_co"][0][2:] == [wins[1][0], wins[1][1]]


def test_backfill_stops_after_a_year(st, monkeypatch):
    from xar.providers import alphapai
    monkeypatch.setattr(alphapai, "has_cjk_name", lambda cid: True)
    st[fc.BF_KEY] = {"win": 999}
    assert fc._bf_worklist({"pinned_ids": ["c0"]}) == [], "回完一年后该段应空转,不再重复抓"


# ── ③ 主题维:宏观/策略/资金流 ──────────────────────────────────────────────────
def test_theme_dims_cover_macro_strategy_moneyflow(st):
    qs = fc.theme_queries()
    scopes = {s for s, _ in qs}
    assert {"industry", "strategy", "macro", "moneyflow"} <= scopes, f"主题维不全: {scopes}"
    joined = " ".join(q for _, q in qs)
    assert "宏观" in joined and "策略" in joined
    assert "北向资金" in joined and "ETF" in joined, "资金流(Moneyflow)词表未接入"
    assert len(qs) > 60, f"主题词表规模偏小: {len(qs)}"


def test_theme_dims_configurable(st, monkeypatch):
    class _Only(_S):
        alphapai_theme_dims = "macro"
    monkeypatch.setattr(fc, "get_settings", lambda: _Only())
    assert {s for s, _ in fc.theme_queries()} == {"macro"}


def test_backfill_worklist_includes_theme_windows(st, monkeypatch):
    from xar.providers import alphapai
    monkeypatch.setattr(alphapai, "has_cjk_name", lambda cid: True)
    wl = fc._bf_worklist({"pinned_ids": ["c0"]})
    themes = [i for i in wl if i[0] == "bf_theme"]
    assert themes, "回溯段应包含主题维工作单元"
    assert {i[1] for i in themes} >= {"macro", "strategy", "moneyflow"}
    assert len(themes[0]) == 5, "主题回溯单元须带 (scope, query, start, end)"


# ── ④ 补上的三个召回类型 ────────────────────────────────────────────────────────
def test_missing_recall_types_now_configured():
    from xar.config import Settings
    types = set(Settings.model_fields["alphapai_recall_types"].default.split(","))
    assert {"roadShow_ir", "vps", "qa"} <= types, f"仍缺类型: {{'roadShow_ir','vps','qa'}} - {types}"
    from xar.providers.alphapai import _DOCTYPE_MAP
    assert types <= set(_DOCTYPE_MAP), "配置了 provider 不支持的召回类型"


# ── ⑤ 三档 drain 优先序:x/finnhub 排在 alphapai 之后 ─────────────────────────────
def test_tier_order_strict_head_then_tail():
    """严格头部序 alphapai > gangtise > aifinmarket,其余(含 x/finnhub)同为尾部档。"""
    assert STRICT_PRIORITY_ORDER == ("alphapai", "gangtise", "aifinmarket")
    sql = tier_order_sql("source")
    assert "'alphapai' THEN 0" in sql and "'gangtise' THEN 1" in sql
    assert "'aifinmarket' THEN 2" in sql and "ELSE 3" in sql
    assert "finnhub" not in sql, "尾部源不再单列档位,由质量权重在 drain 内切分"


def test_qwen_drain_claim_uses_tier_order():
    import inspect

    from xar.orchestration import qwen_drain
    src = inspect.getsource(qwen_drain._claim_sql) + inspect.getsource(qwen_drain._claim)
    assert "tier_order_sql" in src and "ASC" in src, "drain 未按严格档位序领取"
    # 头部 100% 抢占 + 尾部按质量权重分剩余
    assert "STRICT_PRIORITY_ORDER" in src and "_split_by_quality" in src


# ── 主题维前置 + fresh 段收窄(2026-07-27 提速处方)──────────────────────────────
def test_theme_first_puts_themes_before_minutes(st, monkeypatch):
    """主题前置:76 条主题词排在纪要之前 → 宏观/策略/资金流立刻开始落库。"""
    from xar.providers import alphapai
    monkeypatch.setattr(alphapai, "has_cjk_name", lambda cid: True)
    wl = fc._alphapai_worklist({"pinned_ids": ["c0", "c1"]})
    kinds = [i[0] for i in wl]
    assert kinds[0] == "theme", "theme_first=True 时主题必须排最前"
    assert kinds.index("minutes") > max(i for i, k in enumerate(kinds) if k == "theme"), \
        "纪要须在全部主题之后"
    assert kinds[-1] == "rest", "rest 仍排最后"


def test_theme_first_can_be_disabled(st, monkeypatch):
    from xar.providers import alphapai

    class _Off(_S):
        fetch_chain_alphapai_theme_first = False
    monkeypatch.setattr(fc, "get_settings", lambda: _Off())
    monkeypatch.setattr(alphapai, "has_cjk_name", lambda cid: True)
    kinds = [i[0] for i in fc._alphapai_worklist({"pinned_ids": ["c0"]})]
    assert kinds[0] == "minutes", "关闭前置时应回到 纪要→主题→rest"


def test_rest_top_narrows_fresh_stage(st, monkeypatch):
    """rest 收窄:fresh 段长度可控,让回溯段更快拿到额度。"""
    from xar.providers import alphapai

    class _Narrow(_S):
        fetch_chain_alphapai_rest_top = 2
    monkeypatch.setattr(fc, "get_settings", lambda: _Narrow())
    monkeypatch.setattr(alphapai, "has_cjk_name", lambda cid: True)
    wl = fc._alphapai_worklist({"pinned_ids": ["c0", "c1", "c2", "c3", "c4"]})
    assert len([i for i in wl if i[0] == "rest"]) == 2, "rest 应被收窄到 rest_top"
    assert len([i for i in wl if i[0] == "minutes"]) == 5, "纪要仍覆盖全部可寻址公司"

