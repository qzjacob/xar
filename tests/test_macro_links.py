"""勾稽本体（macro_links）离线一致性测试 —— 代码即真相的守卫。

不碰 DB、不联网：校验 43/43 指标全覆盖、id 全部合法（theme/segment/tech_route 以
xar.ingestion.registry 为准，metric/claim 以 src/slx/registry YAML 为准）、
segment↔theme 一致、极性词表合法。
"""
from __future__ import annotations

from pathlib import Path

import yaml

import slx
from xar.ingestion.registry import SEGMENTS, TECH_ROUTES, THEMES
from xar.ontology.macro_links import (
    LINKS_BY_KEY,
    MACRO_LINKS,
    OVERCLAIM_LINKS,
    PLATFORM_METRICS,
    THEME_TO_METRICS,
)

_REG = Path(slx.__file__).resolve().parent / "registry"
_ROUTE_IDS = {r["id"] for r in TECH_ROUTES}


def _registry_metric_keys() -> set[str]:
    keys: set[str] = set()
    for mf in (_REG / "metrics").glob("*.yml"):
        for m in yaml.safe_load(mf.read_text("utf-8"))["metrics"]:
            keys.add(m["metric_key"])
    return keys


def test_full_coverage_no_strays():
    """每个 slx 指标恰有一条勾稽记录；勾稽表无幽灵指标。"""
    reg = _registry_metric_keys()
    linked = set(LINKS_BY_KEY)
    assert linked == reg, (
        f"missing={sorted(reg - linked)} strays={sorted(linked - reg)}")
    assert len(MACRO_LINKS) == len(linked), "duplicate metric_key in MACRO_LINKS"


def test_all_ids_valid():
    for link in MACRO_LINKS:
        for t in link.themes:
            assert t in THEMES, f"{link.metric_key}: unknown theme {t}"
        for s in link.segments:
            assert s in SEGMENTS, f"{link.metric_key}: unknown segment {s}"
        for r in link.tech_routes:
            assert r in _ROUTE_IDS, f"{link.metric_key}: unknown tech_route {r}"
        assert link.scope in ("chain", "platform"), link.metric_key
        assert link.good_when in ("rising", "falling", None), link.metric_key
        assert link.rationale_zh, f"{link.metric_key}: rationale_zh required"


def test_segment_theme_consistency():
    """勾稽到的 segment 必须属于同一条链（防串线）。"""
    for link in MACRO_LINKS:
        for s in link.segments:
            home = SEGMENTS[s]["theme"]
            assert home in link.themes, (
                f"{link.metric_key}: segment {s} belongs to {home}, not in {link.themes}")


def test_chain_scope_has_themes():
    """chain 定向的指标必须至少挂一条链；空 themes 只允许 platform。"""
    for link in MACRO_LINKS:
        if link.scope == "chain":
            assert link.themes, f"{link.metric_key}: chain scope but no themes"
    assert all(li.scope == "platform" for li in PLATFORM_METRICS)


def test_overclaim_links_match_registry():
    doc = yaml.safe_load((_REG / "overclaim_registry.yml").read_text("utf-8"))
    reg_claims = {c["claim_key"] for c in doc["claims"]}
    assert set(OVERCLAIM_LINKS) == reg_claims
    for c in OVERCLAIM_LINKS.values():
        for t in c.themes:
            assert t in THEMES, f"{c.claim_key}: unknown theme {t}"
        assert c.polarity_on_fixation in ("positive", "negative", "neutral")
        assert c.polarity_on_falsified in ("positive", "negative", "neutral")


def test_reverse_index_consistency():
    for theme, links in THEME_TO_METRICS.items():
        assert theme in THEMES
        for li in links:
            assert theme in li.themes
