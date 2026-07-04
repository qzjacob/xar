"""另类数据追踪编排器 —— 按信号谱系(ontology.altdata.ALT_SIGNALS)的 source
字段派发到 providers/alt/{source}.py 的 pull()。缺失模块优雅跳过(workflow 尚未
落地某追踪器时不崩)。glm_worker / CLI / 冒烟测试共用此入口。
"""
from __future__ import annotations

import importlib

from ..logging import get_logger
from ..ontology.altdata import ALT_SIGNALS

log = get_logger("xar.alt")


def _sources() -> list[str]:
    """信号谱系里出现的全部 provider source(去重,保持声明序)。"""
    seen: list[str] = []
    for spec in ALT_SIGNALS:
        if spec.source not in seen:
            seen.append(spec.source)
    return seen


def pull_source(source: str, limit: int | None = None) -> dict:
    """跑单个追踪器。模块/pull 缺失 → skipped(不抛)。"""
    try:
        mod = importlib.import_module(f"..providers.alt.{source}", __package__)
    except ModuleNotFoundError:
        return {"skipped": f"provider alt.{source} not installed"}
    fn = getattr(mod, "pull", None)
    if fn is None:
        return {"skipped": f"alt.{source} has no pull()"}
    try:
        return fn(limit=limit) if limit is not None else fn()
    except TypeError:
        return fn()
    except Exception as e:  # noqa: BLE001 — 单源失败不沉整轮
        log.warning("alt source %s failed: %s", source, e)
        return {"error": str(e)[:200]}


def pull_all(sources: list[str] | None = None, limit: int | None = None) -> dict:
    """跑全部(或指定)追踪器,返回 {source: stats}。"""
    out: dict = {}
    for src in (sources or _sources()):
        out[src] = pull_source(src, limit=limit)
        log.info("alt %s -> %s", src, out[src])
    return out


def sync_events(**kw) -> dict:
    """阈值信号 → kg_events(alt_signal) → semantic_facts(幂等)。"""
    from ..research import thesis_signals

    return thesis_signals.sync_alt_events(**kw)
