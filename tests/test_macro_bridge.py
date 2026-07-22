"""勾稽数据层桥（macro_bridge）+ /api/andy/link 路由次序 —— 需本地 Postgres。

复用 tests/andy 的 seeded 语义（slx schema + registry + 确定性 seed），验证：
  · 印字/判定跃迁事件落 kg_events 且 semantic_facts 可见；
  · 幂等：重跑第二遍 inserted=0；
  · 极性映射纯函数正确；
  · 原生 /api/andy/link/* 与 mounted slx 路由在同一前缀下并存。
"""
from __future__ import annotations

from datetime import date

import pytest

from tests.andy.conftest import DB

requires_db = pytest.mark.skipif(not DB, reason="无 Postgres")


@pytest.fixture(scope="module")
def slx_seeded(request):
    if not DB:
        pytest.skip("无 Postgres")
    from slx.db import init_schema
    from slx.engine import overclaim
    from slx.ingestion.identification_panels import run_identification
    from slx.ingestion.seed import SeedConnector
    from slx.tools.load_registry import main as load_registry
    from xar.storage import db as xdb

    init_schema()
    load_registry()
    SeedConnector().run()
    run_identification(date(2026, 6, 23))
    overclaim.run(date(2026, 6, 25))
    xdb.init_schema()
    return True


def test_print_polarity_mapping():
    from xar.ingestion.macro_bridge import _print_polarity

    assert _print_polarity("rising", 1.0) == "positive"
    assert _print_polarity("rising", -1.0) == "negative"
    assert _print_polarity("falling", 1.0) == "negative"
    assert _print_polarity("falling", -1.0) == "positive"
    assert _print_polarity(None, 1.0) == "neutral"
    assert _print_polarity("rising", None) == "neutral"
    assert _print_polarity("rising", 0.0) == "neutral"


@requires_db
def test_sync_idempotent_and_visible(slx_seeded):
    from xar.ingestion import macro_bridge
    from xar.storage import db

    as_of = date(2026, 6, 30)
    # self-contained on any DB state: clear this bridge's own rows (license_tag='slx')
    db.execute("DELETE FROM kg_events WHERE license_tag='slx'")
    first = macro_bridge.sync(as_of)
    second = macro_bridge.sync(as_of)
    total_first = first["prints"]["inserted"] + first["claims"]["inserted"]
    assert total_first >= 1, f"seed data should yield at least one macro event: {first}"
    assert second["prints"]["inserted"] == 0 and second["claims"]["inserted"] == 0, (
        f"second run must dedup everything: {second}")

    rows = db.query(
        "SELECT category, theme, polarity FROM semantic_facts "
        "WHERE category='macro_print' LIMIT 50")
    assert rows, "macro_print events must surface through semantic_facts"
    assert all(r["polarity"] in ("positive", "negative", "neutral") for r in rows)


@requires_db
def test_link_routes_and_mount_coexist(slx_seeded):
    from starlette.testclient import TestClient

    from xar.api.app import app

    with TestClient(app) as client:
        native = client.get("/api/andy/link/themes")
        assert native.status_code == 200
        body = native.json()
        assert len(body["themes"]) == 8
        mounted = client.get("/api/andy/registry/anchors")
        assert mounted.status_code == 200
        assert mounted.json()["count"] == 12   # A1–A8 + 4 META(theory_anchors.yml,K.1.1 同步)
        theme = client.get("/api/andy/link/theme/ai_chip?as_of=2026-06-30")
        assert theme.status_code == 200
        keys = {m["metric_key"] for m in theme.json()["metrics"]}
        assert "capex.hyperscaler_capex" in keys
        rev = client.get("/api/andy/link/metric/capex.hyperscaler_capex")
        assert rev.status_code == 200
        rj = rev.json()
        assert {t["theme"] for t in rj["themes"]} == {"ai_chip", "ai_optical", "ai_software"}
        assert any(s["genny_link"].startswith("/genny/segment/") for s in rj["segments"])
        assert client.get("/api/andy/link/theme/not_a_theme").status_code == 404
