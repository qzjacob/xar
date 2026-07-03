"""连接器解析（Phase 2.4a）单测：源→连接器发现表正确（dagster 无关，纯 ingestion 层）。

修复前 bug：解析器 import `ingestion.<source_id>`，但连接器实际在 `ingestion/connectors/*.py`，
故所有源都被误判"未实现"而跳过。本测试钉住修复后的发现语义。
"""
from __future__ import annotations

from slx.ingestion.discovery import discover_connectors, resolve_connector


def test_real_connectors_resolve_by_source_id():
    """6 个已落地连接器按其声明的 source_id 被发现为主源。"""
    cmap = discover_connectors()
    for src in ("sec_edgar", "epoch_ai", "fred", "bls", "stooq", "eia"):
        assert src in cmap, f"{src} 未被发现"
        cls, is_primary = cmap[src]
        assert is_primary is True
        assert getattr(cls, "source_id", None) == src or src in getattr(cls, "covers_sources", ())


def test_multi_source_connector_covers_secondary_sources():
    """iea_eia_ember 一次 run 覆盖 iea/ember：二者解析到同一连接器、标记为次源。"""
    iea_conn, iea_primary = resolve_connector("iea")
    ember_conn, ember_primary = resolve_connector("ember")
    assert iea_conn is not None and iea_primary is False
    assert ember_conn is not None and ember_primary is False
    # 主源 eia 的连接器即承载它们的那个。
    assert iea_conn.source_id == "eia" and ember_conn.source_id == "eia"


def test_unknown_source_resolves_to_none():
    conn, is_primary = resolve_connector("definitely_not_a_source")
    assert conn is None and is_primary is True
