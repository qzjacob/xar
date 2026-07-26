"""Ingest-pipeline processing priority (code-as-truth, leaf module — no xar deps).

Some data streams must be drained through the local-model pipeline
(parse_pending → build_kg → expert.process → Ontology) AHEAD of the existing
backlog. `PRIORITY_SOURCES` lists the `documents.source` values that jump the
queue; every stage prepends `priority_order_sql()` as its FIRST ORDER BY key so
those docs are parsed / KG-extracted / expert-processed before all other pending
work, without disturbing the relative ordering among the rest.

Kept as a trusted code literal (never user input) so it is safe to inline in an
ORDER BY clause.
"""
from __future__ import annotations

# Highest-priority ingest streams — processed before all other pending documents.
PRIORITY_SOURCES: tuple[str, ...] = ("aifinmarket", "alphapai")

# 末位流:低 SNR 碎片(x 推文 / finnhub 新闻头条,200-440 字),体量巨大(~28 万)且 firehose 持续灌入。
# 它们**只在优先流与常规流都空时**才吃 GPU —— 保证 alphapai/aifinmarket 的研报纪要、以及
# gangtise/rss/wechat 等常规源永不被碎片挤饿(2026-07-26 用户裁定:x/finnhub 排在 alphapai 之后)。
DEPRIORITIZED_SOURCES: tuple[str, ...] = ("x", "finnhub")


def _lit(sources: tuple[str, ...]) -> str:
    return ", ".join("'" + s.replace("'", "''") + "'" for s in sources)


def priority_order_sql(col: str = "source") -> str:
    """A SQL boolean that is TRUE for priority-source rows. Prepend it as
    `ORDER BY {priority_order_sql(col)} DESC, …` so priority rows sort first.
    Built from the trusted PRIORITY_SOURCES literal — safe to inline."""
    if not PRIORITY_SOURCES:
        return "false"
    return f"({col} IN ({_lit(PRIORITY_SOURCES)}))"


def tier_order_sql(col: str = "source") -> str:
    """三档优先序:0=优先流(PRIORITY_SOURCES) / 1=常规 / 2=末位流(DEPRIORITIZED_SOURCES)。
    用作 `ORDER BY {tier_order_sql(col)} ASC, …`(升序:0 先取)。取代「只有优先/非优先」两档 ——
    否则 x/finnhub 的 28 万碎片会与 gangtise/rss 等常规源平权抢 GPU。均为可信代码常量,可内联。"""
    parts = []
    if PRIORITY_SOURCES:
        parts.append(f"WHEN {col} IN ({_lit(PRIORITY_SOURCES)}) THEN 0")
    if DEPRIORITIZED_SOURCES:
        parts.append(f"WHEN {col} IN ({_lit(DEPRIORITIZED_SOURCES)}) THEN 2")
    if not parts:
        return "1"
    return f"CASE {' '.join(parts)} ELSE 1 END"
