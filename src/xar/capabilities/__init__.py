"""统一能力架构(UA):一处定义分析能力 → Chathy 工具 + /api/run + UI 按钮 + CLI + worker。

`registry.py` 是代码即真相的能力登记簿(由 chathy 工具泛化而来);`runs.py` 是统一异步触发
(capability_runs 表 + schedule/execute)。见 UNIFIED_ARCH_PLAN.md。
"""
from .registry import CAPABILITIES, CapabilitySpec, by_name, chathy_specs, execute, openai_tool_defs

__all__ = ["CAPABILITIES", "CapabilitySpec", "by_name", "chathy_specs", "execute",
           "openai_tool_defs"]
