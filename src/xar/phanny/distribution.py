"""Phanny 组合正态门 + 反作弊守卫 + 假收敛检测(纯函数,零 LLM/零 DB,可测)。

正态是"分析足够锐"的**副产品**,不是捏造函数:不达标 → 补数据/重辩(REDEBATE),
**绝不为凑钟形而统一压低 conviction**。scipy 可选(有则 Shapiro-Wilk,无则退矩法),不引硬依赖。
"""
from __future__ import annotations

import statistics

# 默认门参(纯函数测试用;engine/book 会用 config 覆盖)
_MEAN_LO, _MEAN_HI = 4.5, 6.5
_STD_FLOOR = 1.5
_HIGH_CONV = 7.0
_HIGH_RATIO_FLOOR = 0.10
_HAIRCUT_DELTA = 2.0
# 校准分桶(镜像 earnings,但无 abstain;Phanny 强制 long/short)。半开区间覆盖 1-10。
CONVICTION_BUCKETS = (("1-3", 1.0, 4.0), ("4-6", 4.0, 7.0), ("7-8", 7.0, 9.0), ("9-10", 9.0, 10.01))


def _skew_kurt(xs: list[float], mean: float, std: float) -> tuple[float, float]:
    n = len(xs)
    if std == 0 or n < 3:
        return 0.0, 0.0
    m3 = sum((x - mean) ** 3 for x in xs) / n
    m4 = sum((x - mean) ** 4 for x in xs) / n
    return m3 / std ** 3, m4 / std ** 4 - 3.0


def ensemble_normality(convictions, *, mean_lo: float = _MEAN_LO, mean_hi: float = _MEAN_HI,
                       std_floor: float = _STD_FLOOR, high_ratio_floor: float = _HIGH_RATIO_FLOOR) -> dict:
    """整本 book 的 conviction 是否**合法正态**。返回 {ok, mean, std, skew, exkurt, shapiro_p, n,
    high_ratio, buckets, reason}。门:
      mean∈[lo,hi] ∧ std≥floor ∧ 高信念(≥7)占比≥floor ∧ 非全低/全高聚集
      ∧ (scipy 且 n≥8 → Shapiro-Wilk p≥0.05;否则退矩法 |skew|<1 ∧ |exkurt|<1.5)。
    n<3 → insufficient_sample(不判 ok,也不算违规,由调用方走诚实兜底)。"""
    xs = [float(c) for c in convictions if c is not None]
    n = len(xs)
    buckets = {lbl: sum(1 for x in xs if lo <= x < hi) for lbl, lo, hi in CONVICTION_BUCKETS}
    if n < 3:
        return {"ok": False, "n": n, "reason": "insufficient_sample", "buckets": buckets}
    mean = statistics.fmean(xs)
    std = statistics.pstdev(xs)
    skew, exkurt = _skew_kurt(xs, mean, std)
    high_ratio = sum(1 for x in xs if x >= _HIGH_CONV) / n
    shapiro_p = None
    reasons: list[str] = []
    if not (mean_lo <= mean <= mean_hi):
        reasons.append(f"mean {round(mean, 2)} not in [{mean_lo},{mean_hi}] (禁全低/全高聚集)")
    if std < std_floor:
        reasons.append(f"std {round(std, 2)} < {std_floor} (区分度不足)")
    if high_ratio < high_ratio_floor:
        reasons.append(f"high(≥7) ratio {round(high_ratio, 2)} < {high_ratio_floor} (高端空)")
    normal_ok = True
    use_shapiro = n >= 8 and std > 0    # 退化(range 0)不喂 Shapiro(std<floor 已判 fail)
    if use_shapiro:
        try:
            from scipy import stats  # optional
            shapiro_p = float(stats.shapiro(xs).pvalue)
            if shapiro_p < 0.05:
                normal_ok = False
                reasons.append(f"Shapiro-Wilk p={round(shapiro_p, 3)} < 0.05 (非正态)")
        except Exception:  # noqa: BLE001 — scipy 缺失退矩法
            use_shapiro = False
    if not use_shapiro:
        if abs(skew) >= 1.0 or abs(exkurt) >= 1.5:
            normal_ok = False
            reasons.append(f"矩法非正态 |skew|={round(abs(skew), 2)} |exkurt|={round(abs(exkurt), 2)}")
    ok = (not reasons) and normal_ok
    return {"ok": ok, "mean": round(mean, 3), "std": round(std, 3), "skew": round(skew, 3),
            "exkurt": round(exkurt, 3), "shapiro_p": shapiro_p, "n": n,
            "high_ratio": round(high_ratio, 3), "buckets": buckets,
            "reason": "in_target" if ok else "; ".join(reasons)}


def conviction_only_haircut(prev: dict | None, cur: dict | None) -> bool:
    """假收敛检测:方向未变 ∧ 证据锚未增 ∧ 仅 conviction 下调(<0)→ True(**不算收敛**)。
    prev/cur 形如 {'direction':.., 'conviction':.., 'anchors': int}。"""
    if not prev or not cur:
        return False
    if prev.get("direction") != cur.get("direction"):
        return False
    dc = float(cur.get("conviction", 0)) - float(prev.get("conviction", 0))
    anchors_grew = int(cur.get("anchors", 0)) > int(prev.get("anchors", 0))
    return (dc < 0) and (not anchors_grew)


def convergence_integrity(traces: list[dict], ensemble_ok: bool, *, delta: float = _HAIRCUT_DELTA) -> list[str]:
    """整本反作弊守卫:批**非正态**时,逐名比 final vs round1 —— 下调 > delta 且锚未增 → 违规
    (须补数据/重辩,严禁降 conviction 凑收敛)。批正态则免检(自然涌现即合法)。
    traces 每项 {company_id, round1_conviction, final_conviction, round1_anchors, final_anchors}。"""
    if ensemble_ok:
        return []
    bad: list[str] = []
    for t in traces:
        r1, rf = t.get("round1_conviction"), t.get("final_conviction")
        if r1 is None or rf is None:
            continue
        if (float(r1) - float(rf)) > delta and int(t.get("final_anchors", 0)) <= int(t.get("round1_anchors", 0)):
            bad.append(f"{t.get('company_id')}: conviction {r1}→{rf} 下调>{delta} 且锚未增(疑为凑收敛降分)")
    return bad


def histogram(convictions) -> dict:
    """整数桶 1..10 直方图(前端画钟形用)。"""
    h = {i: 0 for i in range(1, 11)}
    for c in convictions:
        if c is None:
            continue
        b = max(1, min(10, int(round(float(c)))))
        h[b] += 1
    return h


def calibration_buckets(rows: list[dict]) -> dict:
    """按 conviction 分桶回看命中率 × 平均反应。rows=[{conviction, outcome{direction_hit, reaction_pct}}]。"""
    out: dict = {}
    for lbl, lo, hi in CONVICTION_BUCKETS:
        sel = [r for r in rows if lo <= float(r["conviction"]) < hi]
        reacts = [float((r.get("outcome") or {}).get("reaction_pct"))
                  for r in sel if (r.get("outcome") or {}).get("reaction_pct") is not None]
        decided = [(r.get("outcome") or {}).get("direction_hit") for r in sel
                   if isinstance((r.get("outcome") or {}).get("direction_hit"), bool)]
        out[lbl] = {"n": len(sel), "decided": len(decided),
                    "hit_rate": round(sum(decided) / len(decided), 3) if decided else None,
                    "avg_reaction_pct": round(sum(reacts) / len(reacts), 3) if reacts else None}
    return out
