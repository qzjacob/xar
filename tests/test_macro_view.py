"""UA-P2:macro/view.py —— 活读数 / 无 slx 降级 / 压缩器 / dossier 行 + known_ids。"""
from __future__ import annotations

from xar.macro import view


def test_static_degrade_without_andy(monkeypatch):
    # andy_links 整体不可用 → 降级 static rationale(live=False),绝不 raise
    import xar.macro.view as mv

    def _boom(theme, asof):
        raise RuntimeError("andy down")
    # 强制 theme_macro_view 走 except → _static_view
    monkeypatch.setattr(mv, "theme_macro_view", view.theme_macro_view)  # keep
    import xar.api.andy_links as al
    monkeypatch.setattr(al, "link_theme", _boom)
    v = view.theme_macro_view("ai_optical")
    assert v is not None and v["live"] is False
    assert all(m.get("value") is None for m in v["metrics"])   # 无活读数
    assert v["metrics"], "static view should still list crosswalk metrics"


def test_unknown_theme_returns_none(monkeypatch):
    import xar.api.andy_links as al
    monkeypatch.setattr(al, "link_theme", lambda t, a: None)
    assert view.theme_macro_view("not_a_theme") is None


def test_compact_drops_series_keeps_watermark():
    fake = {"theme": "x", "metrics": [
        {"metric_key": "m1", "value": 1.2, "slope": 0.3, "valid_time": "2026-06",
         "series": [{"valid_time": "a", "value": 1}] * 12,
         "identification": {"identification_status": "soft", "watermark": "未识别·勿作因果"}},
    ]}
    c = view.compact_theme_macro(fake)
    m = c["metrics"][0]
    assert "series" not in m                       # drop series
    assert m["watermark"] == "未识别·勿作因果"      # 水印保留
    assert m["identification_status"] == "soft" and m["value"] == 1.2


def test_dossier_lines_and_ids(monkeypatch):
    fake = {"theme": "ai_optical", "metrics": [
        {"metric_key": "capex.x", "rationale_zh": "算力资本开支", "value": 100.0, "slope": 0.5,
         "valid_time": "2026-06", "identification": {"watermark": "已识别"}},
        {"metric_key": "opt.y", "rationale_zh": "光模块出口", "value": None},
    ]}
    monkeypatch.setattr(view, "theme_macro_view", lambda t, as_of=None: fake if t == "ai_optical" else None)
    lines, ids = view.macro_dossier_lines(["ai_optical"], per_theme=5)
    assert "registry:macro:capex.x" in ids and "registry:macro:opt.y" in ids
    assert any("最新 100.0" in ln and "斜率 0.5" in ln for ln in lines)   # 活读数带值/斜率
    assert any("光模块出口" in ln for ln in lines)                        # 无值也出行(rationale)
