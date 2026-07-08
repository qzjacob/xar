"""UA-P0:能力登记簿 —— schema 合法性、Chathy re-export parity、execute 行为。"""
from __future__ import annotations

import json

# 迁移前的 24 个 Chathy 工具名(parity 锁:迁到 capabilities/registry 后必须一一对应)
_PRE_MOVE_24 = {
    "find_company", "semantic_facts", "search_documents", "theme_overview", "list_companies",
    "company_detail", "segment_detail", "list_segments", "signals", "catalysts", "calendar",
    "theme_landscape", "regime", "decision", "coverage", "supply_chain", "company_competitors",
    "single_source_risks", "events", "dataroom_docs", "get_thesis", "alt_signals",
    "coverage_360", "macro_indicators",
}


def test_registry_schemas_valid():
    from xar.capabilities import registry

    names = [c.name for c in registry.CAPABILITIES]
    assert len(names) == len(set(names)), "duplicate capability names"
    for c in registry.CAPABILITIES:
        assert c.parameters.get("type") == "object", f"{c.name}: params not an object schema"
        assert c.kind in ("read", "build")
        assert c.duration in ("fast", "slow")
        assert callable(c.fn)


def test_chathy_reexport_parity():
    from xar.chathy import tools

    # 迁移前的 24 个工具必须全部保留(回归锁;P3 后新增 chathy 工具 → 超集)
    names = {t.name for t in tools.TOOLS}
    assert _PRE_MOVE_24 <= names, f"missing pre-move tools: {_PRE_MOVE_24 - names}"
    # openai 工具定义渲染的名字集 == chathy 能力集
    defs = tools.openai_tool_defs()
    assert {d["function"]["name"] for d in defs} == names
    assert all(d["type"] == "function" and "parameters" in d["function"] for d in defs)
    # build 能力不进 Chathy
    assert "build_earnings_verdict" not in names and "report" not in names


def test_execute_unknown_tool_error_json():
    from xar.chathy import tools

    out = json.loads(tools.execute("nope", {}))
    assert "error" in out and "unknown" in out["error"]


def test_execute_real_tool_roundtrip(seeded_db):
    from xar.chathy import tools

    out = json.loads(tools.execute("coverage", {"theme": "ai_optical"}))
    assert isinstance(out, (dict, list)) and "error" not in (out if isinstance(out, dict) else {})


def test_by_name_and_chathy_filter():
    from xar.capabilities import registry

    assert registry.by_name("get_thesis") is not None
    assert registry.by_name("does_not_exist") is None
    assert all(c.chathy for c in registry.chathy_specs())
