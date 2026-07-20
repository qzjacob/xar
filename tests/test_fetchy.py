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


def test_save_roundtrip_and_unknown_keys_ignored(seeded_db, monkeypatch):
    from xar.orchestration import glm_worker as gw

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")   # model_usable 只看在场性
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


def test_partial_save_merges_with_saved(seeded_db):
    """FY-R#4:部分 PUT 与已保存文档合并,不得抹掉先前的开关。"""
    from xar.orchestration import glm_worker as gw

    try:
        gw.save_fetchy({"enabled": False})
        out = gw.save_fetchy({"sources": {"twitter": False}})
        assert out["enabled"] is False           # 先前保存的总开关幸存
        assert out["sources"]["twitter"] is False
    finally:
        gw.save_fetchy(gw.fetchy_defaults())


def test_unknown_model_rejected(seeded_db):
    from xar.orchestration import glm_worker as gw

    with pytest.raises(ValueError):
        gw.save_fetchy({"model": "no-such-model"})


def test_worker_unusable_models_rejected(seeded_db, monkeypatch):
    """FY-R#2:host-only 执行器/退役/缺 key 的模型在保存时就拒绝,不留静默空转。"""
    from xar.orchestration import glm_worker as gw

    with pytest.raises(ValueError):
        gw.save_fetchy({"model": "claude-opus-max"})     # agent_sdk:工人容器内不可用
    with pytest.raises(ValueError):
        gw.save_fetchy({"model": "deepseek-chat"})       # DEPRECATED
    monkeypatch.setattr(gw.llm, "_ensure_keys", lambda: None)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError):
        gw.save_fetchy({"model": "deepseek-v4-pro"})     # provider key 缺位


def test_pin_puts_selected_model_first(seeded_db, monkeypatch):
    from xar.config import get_settings
    from xar.orchestration import glm_worker as gw

    # 钉死 local-first 关闭:本测试断言的是「无本地头时」的显式选型排序;宿主真实
    # .env 的 XAR_GLM_WORKER_LOCAL_FIRST=true 否则会把 glm4-local 前插进默认链。
    monkeypatch.setenv("XAR_GLM_WORKER_LOCAL_FIRST", "false")
    get_settings.cache_clear()
    try:
        assert gw._fetchy_pin({"model": None}) == gw.GLM_PIN
        assert gw._fetchy_pin({"model": gw.GLM_PIN[0]}) == gw.GLM_PIN
        pin = gw._fetchy_pin({"model": "deepseek-v4-pro"})
        assert pin[0] == "deepseek-v4-pro" and gw.GLM_PIN[0] in pin
    finally:
        get_settings.cache_clear()


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


def test_config_read_failure_fails_closed(seeded_db, monkeypatch):
    """FY-R#3:配置读取失败 → 跳过本轮(fail-closed),绝不按默认全开跑。"""
    from xar.orchestration import glm_worker as gw

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(gw, "fetchy_config", boom)
    called = {"pull": False}
    monkeypatch.setattr(gw, "_pull_fresh", lambda cfg=None: called.__setitem__("pull", True) or {})
    out = gw.run_once()
    assert str(out.get("skipped", "")).startswith("fetchy config unreadable")
    assert called["pull"] is False


def test_extract_gate_is_model_aware(seeded_db, monkeypatch):
    """FY-R#1:非 GLM 链首不受 GLM_SUB_API_KEY 门禁;GLM 链首缺订阅 key 仍拒绝。"""
    from xar.orchestration import glm_worker as gw

    model = ["deepseek-v4-pro"]
    base = {"enabled": True,
            "sources": dict.fromkeys(gw.FETCHY_SOURCES, False),
            "stages": dict.fromkeys(gw.FETCHY_STAGES, False) | {"extract": True}}
    real_get = gw.get_state
    monkeypatch.setattr(gw, "get_state",
                        lambda k, d=None: {"status": "ok"} if k == "quota" else real_get(k, d))
    monkeypatch.setattr(gw, "save_state", lambda k, v: None)   # 本测不落任何工人状态
    monkeypatch.setattr(gw, "fetchy_config", lambda strict=False: {**base, "model": model[0]})
    monkeypatch.setattr(gw, "_pull_fresh", lambda cfg=None: {})
    monkeypatch.setattr(gw, "_llm_stage",
                        lambda b, q, pin=gw.GLM_PIN: ({"pin_head": pin[0]}, q))
    monkeypatch.setattr(gw, "_sub_ready", lambda: False)       # GLM 订阅 key 缺位

    out = gw.run_once()
    assert out["extract"] == {"pin_head": "deepseek-v4-pro"}   # 非 GLM 链首:放行

    model[0] = gw.GLM_PIN[0]
    out = gw.run_once()
    assert "GLM_SUB_API_KEY" in out["extract"]["skipped"]      # GLM 链首:仍拒绝


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
