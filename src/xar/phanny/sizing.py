"""Phanny 确定性头寸 sizing(代码侧,LLM 不报 size)——采 glm+kimi。

单名:size = clip( kelly(conviction, asymmetry) × inv_vol(implied_move), 1, 15 )。
组合层:总敞口 gross_cap;超限**按比例缩 size(绝不动 conviction)**,再各自 clip 回 [1,15]
(每名 1-15% 是硬约束,gross_cap 为尽力而为的组合护栏)。纯函数,零 LLM,可测。
"""
from __future__ import annotations

_SIZE_MIN, _SIZE_MAX = 1.0, 15.0
_VOL_REF = 0.06   # 锚定隐含波动(≈6% 单季财报日),用于 inv_vol 缩放


def name_size(conviction: float, *, asymmetry: float = 1.0,
              implied_move: float | None = None, vol_ref: float = _VOL_REF) -> tuple[float, str]:
    """单名 size%(未过组合层)。conviction 1-10 → kelly 基;asymmetry(0.4..1.6)赔率调节;
    inv_vol:implied_move 越大(风险越高)→ size 越小(以 vol_ref 为锚)。clip[1,15]。"""
    kelly = (conviction / 10.0) * max(0.4, min(1.6, asymmetry))
    inv_vol = 1.0
    if implied_move and implied_move > 0:
        inv_vol = max(0.5, min(1.5, vol_ref / float(implied_move)))
    raw = _SIZE_MAX * kelly * inv_vol
    size = round(max(_SIZE_MIN, min(_SIZE_MAX, raw)), 1)
    rationale = (f"conviction {conviction:.1f} → kelly {kelly:.2f}"
                 + (f" × inv_vol {inv_vol:.2f}(implied {float(implied_move) * 100:.1f}%)" if implied_move else "")
                 + f" → {size:.1f}%")
    return size, rationale


def apply_portfolio(sizes: list[dict], *, gross_cap: float = 150.0) -> dict:
    """组合层。sizes=[{company_id, direction, size_pct, theme?}](原地更新 size_pct)。
    若 Σsize > gross_cap → 按比例缩(不动 conviction),再各自 clip 回 [1,15]。返回 stats。"""
    gross = sum(s["size_pct"] for s in sizes)
    scale = 1.0
    if gross_cap and gross > gross_cap and gross > 0:
        scale = gross_cap / gross
        for s in sizes:
            s["size_pct"] = round(max(_SIZE_MIN, min(_SIZE_MAX, s["size_pct"] * scale)), 1)
    final_gross = round(sum(s["size_pct"] for s in sizes), 1)
    net = round(sum(s["size_pct"] if s["direction"] == "long" else -s["size_pct"] for s in sizes), 1)
    # 主题集中度(同主题 Σsize)
    by_theme: dict = {}
    for s in sizes:
        by_theme.setdefault(s.get("theme") or "?", 0.0)
        by_theme[s.get("theme") or "?"] += s["size_pct"]
    return {"sizes": sizes, "gross": final_gross, "net": net, "gross_cap": gross_cap,
            "scaled": round(scale, 3), "long": sum(1 for s in sizes if s["direction"] == "long"),
            "short": sum(1 for s in sizes if s["direction"] == "short"),
            "theme_gross": {k: round(v, 1) for k, v in by_theme.items()}}
