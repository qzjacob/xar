"""中文路由表:命中语义 + 代码即真相不变式(key 必须 ∈ ontology,防漂移)。全离线。"""
from __future__ import annotations

from xar.ingestion.registry import ROUTE_THEMES, TECH_ROUTES, THEMES
from xar.ontology import cn_routing


def test_theme_keys_are_real_themes():
    for key in cn_routing.CN_THEME_TERMS:
        assert key in THEMES, f"CN_THEME_TERMS key {key!r} not a registry theme"
    # 8 主题全覆盖
    assert set(cn_routing.CN_THEME_TERMS) == set(THEMES)


def test_route_keys_are_real_routes():
    route_ids = {r["id"] for r in TECH_ROUTES}
    for key in cn_routing.CN_ROUTE_TERMS:
        assert key in route_ids, f"CN_ROUTE_TERMS key {key!r} not a tech route"
    # 33 路线全覆盖
    assert set(cn_routing.CN_ROUTE_TERMS) == route_ids


def test_no_empty_term_lists():
    for k, v in {**cn_routing.CN_THEME_TERMS, **cn_routing.CN_ROUTE_TERMS}.items():
        assert v and all(t.strip() for t in v), f"{k} has empty terms"


def test_theme_and_route_hits():
    t = "中际旭创1.6T光模块放量,CoWoS先进封装供不应求,算力芯片需求爆发"
    assert set(cn_routing.theme_hits(t)) >= {"ai_optical", "ai_chip"}
    assert set(cn_routing.route_hits(t)) >= {"tr_1600g", "tr_cowos"}


def test_route_themes_via_ontology():
    themes = cn_routing.route_themes(["tr_1600g", "tr_cowos"])
    # tr_1600g -> ai_optical/ai_chip (per ROUTE_THEMES)
    assert set(themes) == set(ROUTE_THEMES["tr_1600g"]) | set(ROUTE_THEMES["tr_cowos"])


def test_noise_zero_hits():
    for noise in ("今天天气不错大家注意身体", "这只票稳了兄弟们冲", ""):
        assert cn_routing.theme_hits(noise) == []
        assert cn_routing.route_hits(noise) == []


def test_case_insensitive_ascii():
    assert "tr_hbm" in cn_routing.route_hits("hbm 供应紧张")
    assert "tr_cowos" in cn_routing.route_hits("cowos 产能")
