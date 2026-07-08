"""宏观视图服务层 —— 主题级 Andy 勾稽活读数,供 dossier/报告/Chathy 共用(UA-P2)。

分层:research/agents → macro → (惰性)api.andy_links + ontology.macro_links + slx。
andy_links 不 import research/app,故无循环。slx/andy 不可用 → 优雅降级为 static rationale
(live=False),绝不 raise。PIT 纪律:传 as_of(裁决/事件日),读数严格 knowledge_time <= as_of。
"""
from __future__ import annotations

from datetime import date

from ..logging import get_logger

log = get_logger("xar.macro.view")


def _static_view(theme: str) -> dict | None:
    """slx 不可用时的降级视图:仅勾稽逻辑层的 rationale(无活读数)。"""
    from ..ontology.macro_links import THEME_TO_METRICS

    links = THEME_TO_METRICS.get(theme, ())
    if not links:
        return None
    return {"theme": theme, "live": False,
            "metrics": [{"metric_key": li.metric_key, "rationale_zh": li.rationale_zh,
                         "good_when": li.good_when, "scope": li.scope} for li in links]}


def theme_macro_view(theme: str, as_of: date | str | None = None) -> dict | None:
    """单主题宏观面板(PIT 最新值/斜率/序列 + 水印 + overclaims)。
    andy_links 可用 → 活读数(link_theme 内部已对 slx 不可用容错,只是 value=None);
    整体失败 → 静态降级。None = 未知主题。"""
    asof = as_of.isoformat() if isinstance(as_of, date) else as_of
    try:
        from ..api import andy_links

        view = andy_links.link_theme(theme, asof)
    except Exception as e:  # noqa: BLE001 — api/slx 整体不可用 → 降级
        log.warning("theme_macro_view %s: andy_links unavailable (%s)", theme, str(e)[:120])
        return _static_view(theme)
    if view is None:
        return None
    view["live"] = any(m.get("value") is not None for m in (view.get("metrics") or []))
    return view


def compact_theme_macro(view: dict | None, max_metrics: int = 8) -> dict | None:
    """LLM 压缩形:drop series(省 token),保 value/slope/valid_time + 识别水印
    (soft ⇒「未识别·勿作因果」必须原文到模型)。dossier/Chathy 共享此压缩器。"""
    if not view:
        return view
    out = {k: v for k, v in view.items() if k != "metrics"}
    ms = []
    for m in (view.get("metrics") or [])[:max_metrics]:
        mm = {k: m.get(k) for k in ("metric_key", "display_name_zh", "value", "slope",
                                    "valid_time", "good_when", "rationale_zh", "unit")}
        ident = m.get("identification") or {}
        mm["identification_status"] = ident.get("identification_status")
        mm["watermark"] = ident.get("watermark")
        ms.append(mm)
    out["metrics"] = ms
    return out


def macro_dossier_lines(themes: list[str], as_of: date | str | None = None,
                        per_theme: int = 5) -> tuple[list[str], set[str]]:
    """dossier 注入行 + known_ids。id 沿用 [registry:macro:<key>](与论点证据格式兼容);
    有活读数则行内追加 值/斜率/valid_time + 水印后缀。"""
    lines: list[str] = []
    ids: set[str] = set()
    for t in themes:
        view = theme_macro_view(t, as_of)
        if not view:
            continue
        for m in (view.get("metrics") or [])[:per_theme]:
            key = m.get("metric_key")
            if not key:
                continue
            ids.add(f"registry:macro:{key}")
            base = f"[registry:macro:{key}] {m.get('rationale_zh') or key}"
            if m.get("value") is not None:
                ident = m.get("identification") or {}
                wm = ident.get("watermark")
                base += f" — 最新 {m['value']}"
                if m.get("slope") is not None:
                    base += f",斜率 {round(float(m['slope']), 3)}"
                if m.get("valid_time"):
                    base += f"(@{m['valid_time']})"
                if wm:
                    base += f" · {wm}"
            lines.append(base)
    return lines, ids
