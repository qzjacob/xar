"""T0 目标化 + 策展名册:目标装配、中文猎词、名册往返。"""
from __future__ import annotations

import pytest

from xar.mining import targeting


def test_build_target_cn_aliases_and_hunt_terms():
    # innolight = 中际旭创 (ai_optical) → has Chinese aliases + optical hunt terms
    t = targeting.build_target("innolight")
    assert t is not None
    assert any("旭创" in a or "中际" in a for a in t.aliases_zh)
    assert "ai_optical" in t.themes
    assert "光模块" in t.hunt_terms_zh


def test_build_target_unknown_company():
    assert targeting.build_target("no_such_company") is None


def test_company_routes_via_route_themes():
    t = targeting.build_target("innolight")
    # optical company → optical routes present
    assert any(r in ("tr_800g", "tr_1600g", "tr_cpo") for r in t.routes)


@pytest.fixture
def seeded(seeded_db):
    return seeded_db


def test_build_targets_ranks_challenged_first(seeded):
    targets = targeting.build_targets(10)
    # challenged (priority 1.0) sorted before non-challenged (0.5)
    prios = [t.priority for t in targets]
    assert prios == sorted(prios, reverse=True)


def test_roster_register_list_deactivate(seeded):
    from xar.mining import roster
    from xar.storage import db

    db.execute("DELETE FROM wechat_accounts WHERE feed_id='pytest_feed'")
    roster.register("pytest_feed", name="测试", theme="ai_optical",
                    company_id="innolight", tier=1)
    feeds = {f["feed_id"] for f in roster.active_feeds()}
    assert "pytest_feed" in feeds
    roster.deactivate("pytest_feed")
    feeds = {f["feed_id"] for f in roster.active_feeds()}
    assert "pytest_feed" not in feeds
    db.execute("DELETE FROM wechat_accounts WHERE feed_id='pytest_feed'")


def test_hunt_terms_flat_dedup(seeded):
    terms = targeting.hunt_terms(5)
    assert terms == list(dict.fromkeys(terms))  # no dupes
    assert all(t.strip() for t in terms)
