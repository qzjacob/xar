"""Fetchy — glmworker 管理面(配置存 glm_worker_state,工人每轮读取)。"""
from __future__ import annotations

import pytest


def test_defaults_everything_on(seeded_db):
    from xar.orchestration import glm_worker as gw

    cfg = gw.fetchy_defaults()
    assert cfg["enabled"] is True
    assert cfg["model"] == gw.GLM_PIN[0]
    assert set(cfg["sources"]) == set(gw.FETCHY_SOURCES)
    assert set(cfg["stages"]) == set(gw.FETCHY_STAGES)
    assert all(cfg["sources"].values()) and all(cfg["stages"].values())


def test_save_roundtrip_and_unknown_keys_ignored(seeded_db):
    from xar.orchestration import glm_worker as gw

    out = gw.save_fetchy({"enabled": False, "model": "deepseek-v4-pro",
                          "sources": {"twitter": False, "bogus": True},
                          "stages": {"extract": False}})
    assert out["enabled"] is False
    assert out["model"] == "deepseek-v4-pro"
    assert out["sources"]["twitter"] is False
    assert "bogus" not in out["sources"]
    assert out["sources"]["wechat"] is True      # 未提及的键保持默认开
    assert out["stages"]["extract"] is False
    # 复原
    gw.save_fetchy(gw.fetchy_defaults())


def test_unknown_model_rejected(seeded_db):
    from xar.orchestration import glm_worker as gw

    with pytest.raises(ValueError):
        gw.save_fetchy({"model": "no-such-model"})


def test_pin_puts_selected_model_first(seeded_db):
    from xar.orchestration import glm_worker as gw

    assert gw._fetchy_pin({"model": None}) == gw.GLM_PIN
    assert gw._fetchy_pin({"model": gw.GLM_PIN[0]}) == gw.GLM_PIN
    pin = gw._fetchy_pin({"model": "deepseek-v4-pro"})
    assert pin[0] == "deepseek-v4-pro" and gw.GLM_PIN[0] in pin


def test_disabled_run_once_heartbeats_only(seeded_db, monkeypatch):
    from xar.orchestration import glm_worker as gw

    gw.save_fetchy({"enabled": False})
    called = {"pull": False}
    monkeypatch.setattr(gw, "_pull_fresh", lambda cfg=None: called.__setitem__("pull", True) or {})
    try:
        out = gw.run_once()
        assert out.get("skipped") == "fetchy disabled"
        assert called["pull"] is False           # 总开关关:什么都不拉
        assert "extract" not in out
    finally:
        gw.save_fetchy(gw.fetchy_defaults())


def test_all_sources_off_pulls_nothing(seeded_db, monkeypatch):
    """门禁在 provider 导入之前生效:全关时 _pull_fresh 零导入/零网络/零输出。"""
    from xar.orchestration import glm_worker as gw

    cfg = gw.fetchy_defaults()
    cfg["sources"] = dict.fromkeys(cfg["sources"], False)
    monkeypatch.setattr(gw, "_due", lambda k, s: True)   # 即便全部到点,也不得放行
    assert gw._pull_fresh(cfg) == {}


def test_ops_api_roundtrip(seeded_db):
    from fastapi.testclient import TestClient
    from xar.api.app import app
    from xar.orchestration import glm_worker as gw

    c = TestClient(app)
    r = c.get("/api/ops/fetchy").json()
    assert r["config"]["enabled"] in (True, False)
    assert any(m["id"] == gw.GLM_PIN[0] for m in r["models"])
    assert {s["key"] for s in r["sources"]} == set(gw.FETCHY_SOURCES)

    r2 = c.put("/api/ops/fetchy", json={"sources": {"rss": False}}).json()
    assert r2["config"]["sources"]["rss"] is False
    assert c.put("/api/ops/fetchy", json={"model": "nope"}).status_code == 400
    # 复原
    c.put("/api/ops/fetchy", json=gw.fetchy_defaults())
