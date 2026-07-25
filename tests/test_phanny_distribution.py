"""Phanny 组合正态门 + 反作弊守卫 + 假收敛检测 + sizing。纯离线,无 DB。"""
from __future__ import annotations

from xar.phanny import distribution as dist
from xar.phanny import sizing


# ── ensemble_normality ────────────────────────────────────────────────────────────
def test_all_low_conviction_fails():
    r = dist.ensemble_normality([2, 2, 3, 2, 3, 2, 3, 2, 3, 2])
    assert r["ok"] is False and "mean" in r["reason"]  # 禁全低聚集


def test_degenerate_narrow_fails():
    r = dist.ensemble_normality([5, 5, 5, 5, 5, 5, 5, 5, 5, 5])
    assert r["ok"] is False and ("std" in r["reason"] or "high" in r["reason"])


def test_bell_passes():
    r = dist.ensemble_normality([2, 3, 4, 4, 5, 5, 5, 6, 6, 7, 8, 4, 6, 5, 7])
    assert r["ok"] is True, r["reason"]
    assert 4.5 <= r["mean"] <= 6.5 and r["std"] >= 1.5 and r["high_ratio"] >= 0.10


def test_insufficient_sample():
    r = dist.ensemble_normality([5, 6])
    assert r["ok"] is False and r["reason"] == "insufficient_sample"


# ── 假收敛(conviction_only_haircut)────────────────────────────────────────────────
def test_haircut_true_when_only_conviction_dropped():
    prev = {"direction": "long", "conviction": 7.0, "anchors": 6}
    cur = {"direction": "long", "conviction": 5.0, "anchors": 6}   # 仅降 conviction,锚未增
    assert dist.conviction_only_haircut(prev, cur) is True


def test_haircut_false_when_anchors_grew():
    prev = {"direction": "long", "conviction": 7.0, "anchors": 6}
    cur = {"direction": "long", "conviction": 5.0, "anchors": 8}   # 锚增 → 合法的证据驱动降分
    assert dist.conviction_only_haircut(prev, cur) is False


def test_haircut_false_when_direction_flipped():
    prev = {"direction": "long", "conviction": 7.0, "anchors": 6}
    cur = {"direction": "short", "conviction": 5.0, "anchors": 6}
    assert dist.conviction_only_haircut(prev, cur) is False


# ── convergence_integrity(整本反作弊守卫)──────────────────────────────────────────
def test_integrity_flags_lowering_when_non_normal():
    traces = [{"company_id": "a", "round1_conviction": 8, "final_conviction": 4,
               "round1_anchors": 6, "final_anchors": 6}]   # 下调 4>2 且锚未增
    assert dist.convergence_integrity(traces, ensemble_ok=False)   # 非空 → 违规


def test_integrity_clean_when_normal():
    traces = [{"company_id": "a", "round1_conviction": 8, "final_conviction": 4,
               "round1_anchors": 6, "final_anchors": 6}]
    assert dist.convergence_integrity(traces, ensemble_ok=True) == []   # 正态则免检


def test_integrity_clean_when_anchors_grew():
    traces = [{"company_id": "a", "round1_conviction": 8, "final_conviction": 4,
               "round1_anchors": 6, "final_anchors": 9}]   # 锚增 → 合法
    assert dist.convergence_integrity(traces, ensemble_ok=False) == []


# ── sizing ────────────────────────────────────────────────────────────────────────
def test_size_monotonic_and_clipped():
    lo = sizing.name_size(1.0)[0]
    hi = sizing.name_size(10.0, asymmetry=1.4)[0]
    assert 1.0 <= lo < hi <= 15.0          # 地板 ≥1%,随 conviction 单调,封顶 15%


def test_size_inv_vol_shrinks_on_high_iv():
    calm = sizing.name_size(7.0, implied_move=0.04)[0]
    wild = sizing.name_size(7.0, implied_move=0.12)[0]
    assert wild < calm   # 高隐波 → 小仓


def test_portfolio_gross_cap_scales_size_not_below_floor():
    rows = [{"company_id": f"c{i}", "direction": "long", "size_pct": 12.0, "theme": "t"} for i in range(20)]
    pf = sizing.apply_portfolio(rows, gross_cap=100.0)
    assert pf["gross"] <= 100.0 * 1.35   # 缩放后接近帽(下限 clip 可能略超)
    assert all(1.0 <= s["size_pct"] <= 15.0 for s in pf["sizes"])   # 每名仍 [1,15]
    assert pf["scaled"] < 1.0


def test_histogram_and_calibration_buckets():
    h = dist.histogram([1, 5, 5, 8, 10])
    assert h[5] == 2 and h[8] == 1 and h[10] == 1
    cal = dist.calibration_buckets([
        {"conviction": 8, "outcome": {"direction_hit": True, "reaction_pct": 3.0}},
        {"conviction": 8, "outcome": {"direction_hit": False, "reaction_pct": -1.0}},
    ])
    assert cal["7-8"]["n"] == 2 and cal["7-8"]["decided"] == 2 and cal["7-8"]["hit_rate"] == 0.5
