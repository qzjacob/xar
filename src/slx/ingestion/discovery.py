"""连接器发现 —— 源 source_id → 连接器类的映射（dagster 无关，属 ingestion 层）。

为什么单列一层：编排（Dagster）与测试都要"按 source_id 找连接器"，但该逻辑只依赖
`ingestion.base.Connector` 与 `ingestion.connectors.*`，与 Dagster 无关。放在 ingestion
层让它能被无 Dagster 环境（含单测）直接用，也避免编排层承载本不属于它的发现逻辑。

修复的 bug（Phase 2.4a）：旧解析器 import `ingestion.<source_id>`，但连接器实际落在
`ingestion/connectors/*.py`（且 iea_eia_ember 一个类服务 iea/eia/ember 三源），故所有源都被
误判"未实现"。本模块按连接器类声明的 `.source_id`（主）与 `.covers_sources`（次）建表。
"""
from __future__ import annotations

import importlib
import pkgutil


def discover_connectors() -> dict[str, tuple[type, bool]]:
    """扫描 `ingestion.connectors.*`，返回 {source_id: (ConnectorClass, is_primary)}。

      · 每个 Connector 子类按其 `.source_id`（主源，is_primary=True）登记；
      · 并按其 `.covers_sources`（次源，is_primary=False）登记——一个连接器一次 run
        可同时填充多个源（如 iea_eia_ember 一次写 iea/eia/ember），次源不再单独拉取。
    单个连接器导入失败（缺可选依赖等）不拖垮整表：跳过该模块。
    """
    from slx.ingestion.base import Connector
    import slx.ingestion.connectors as pkg

    out: dict[str, tuple[type, bool]] = {}
    for mod_info in pkgutil.iter_modules(pkg.__path__):
        if mod_info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"slx.ingestion.connectors.{mod_info.name}")
        except Exception:  # noqa: BLE001 —— 缺可选依赖的连接器不应阻断发现/资产图加载
            continue
        for obj in vars(mod).values():
            if isinstance(obj, type) and issubclass(obj, Connector) and obj is not Connector:
                primary = getattr(obj, "source_id", None)
                if primary:
                    out.setdefault(primary, (obj, True))
                for sec in getattr(obj, "covers_sources", ()) or ():
                    out.setdefault(sec, (obj, False))
    return out


def resolve_connector(source_id: str, table: dict[str, tuple[type, bool]] | None = None):
    """返回 (连接器实例 | None, is_primary)。

    先查 `ingestion.connectors.*` 发现表（真正落地处）；未命中再回退文档约定的顶层模块
    `ingestion/<source_id>.py`（便于将来把某源拆成独立顶层模块时仍兼容）。
    is_primary=False 表示该源由某连接器的同一次 run 覆盖（次源），编排据此跳过重复拉取。
    """
    table = table if table is not None else discover_connectors()
    hit = table.get(source_id)
    if hit is not None:
        cls, is_primary = hit
        return cls(), is_primary

    from slx.ingestion.base import Connector

    try:
        mod = importlib.import_module(f"slx.ingestion.{source_id}")
    except ModuleNotFoundError:
        return None, True
    for obj in vars(mod).values():
        if isinstance(obj, type) and issubclass(obj, Connector) and obj is not Connector:
            if getattr(obj, "source_id", None) == source_id:
                return obj(), True
    return None, True
