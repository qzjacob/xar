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
PRIORITY_SOURCES: tuple[str, ...] = ("aifinmarket",)


def priority_order_sql(col: str = "source") -> str:
    """A SQL boolean that is TRUE for priority-source rows. Prepend it as
    `ORDER BY {priority_order_sql(col)} DESC, …` so priority rows sort first.
    Built from the trusted PRIORITY_SOURCES literal — safe to inline."""
    if not PRIORITY_SOURCES:
        return "false"
    lit = ", ".join("'" + s.replace("'", "''") + "'" for s in PRIORITY_SOURCES)
    return f"({col} IN ({lit}))"
