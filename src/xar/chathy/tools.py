"""Chathy 工具 —— re-export shim(UA-P0)。

真正的能力登记簿已迁到 `xar.capabilities.registry`(泛化为 CapabilitySpec,供 Chathy + /api/run
+ UI 按钮 + CLI 共用)。此模块保留原 import 面(`ToolSpec`/`TOOLS`/`execute`/`openai_tool_defs`/
`_MAX_RESULT_CHARS`),让 `agent.py` 与既有测试零改动。新增工具请去 `capabilities/registry.py`。
"""
from __future__ import annotations

from ..capabilities.registry import (
    _MAX_RESULT_CHARS,
    CapabilitySpec as ToolSpec,
    chathy_specs,
    execute,
    openai_tool_defs,
)

# 仅 Chathy 暴露的能力(chathy=True);与迁移前 24 个工具一一对应。
TOOLS = chathy_specs()

__all__ = ["ToolSpec", "TOOLS", "execute", "openai_tool_defs", "_MAX_RESULT_CHARS"]
