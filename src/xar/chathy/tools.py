"""Chathy's tool registry — code-as-truth.

Each `ToolSpec` maps a stable tool name + JSON-schema to one plain in-process function
(the same ones the dashboards/retrieval already expose), so Chathy invokes the platform
directly — no HTTP hop, no reimplementation. `openai_tool_defs()` renders the registry into
the function-calling schema LiteLLM/OpenAI expect; `execute()` runs a call and returns a
JSON string (truncated) for the tool-result message.

To add a capability: append a `ToolSpec`. To expose it to Fenny/Genny later, the same
pattern (a new ToolSpec pointing at a plain function) applies — see the Phase-3 note below.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from ..ingestion.registry import THEMES, company_by_id
from ..logging import get_logger

log = get_logger("xar.chathy.tools")

_THEME_ENUM = list(THEMES.keys()) if isinstance(THEMES, dict) else list(THEMES)
_MAX_RESULT_CHARS = 8000


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict           # JSON Schema for the arguments object
    fn: Callable[..., object]


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [],
            "additionalProperties": False}


_THEME = {"type": "string", "enum": _THEME_ENUM,
          "description": "One of the 8 investment themes."}
_CID = {"type": "string", "description": "A company id (e.g. 'nvidia', 'u_tw_2330'). "
        "Use find_company first if you only have a name/ticker."}


# --- tool implementations (thin wrappers so signatures stay explicit) -------------------
def _find_company(query: str) -> dict:
    from ..kg import resolve as _resolve
    cid, score = _resolve.resolve(query)
    if not cid:
        return {"query": query, "match": None, "note": "no confident match; try a ticker or exact name"}
    c = company_by_id(cid) or {}
    return {"query": query, "id": cid, "score": round(score, 3), "name": c.get("name"),
            "tickers": c.get("tickers"), "themes": c.get("themes"), "region": c.get("region")}


def _semantic_facts(company_id: str | None = None, theme: str | None = None,
                    as_of: str | None = None, since: str | None = None, limit: int = 40) -> list[dict]:
    from ..retrieval import graphrag
    return graphrag.semantic(company_id=company_id, theme=theme, as_of=as_of, since=since,
                             limit=min(int(limit or 40), 100))


def _search_documents(query: str, company_id: str | None = None, k: int = 8) -> list[dict]:
    from ..retrieval import vector
    hits = vector.hybrid_search(query, company_id=company_id, k=min(int(k or 8), 20))
    return [h.citation() for h in hits]


def _dash(fn_name: str):
    def call(**kw):
        from ..api import dashboard
        return getattr(dashboard, fn_name)(**kw)
    return call


def _graph(fn_name: str):
    def call(**kw):
        from ..retrieval import graphrag
        return getattr(graphrag, fn_name)(**kw)
    return call


def _macro_indicators(theme: str | None = None, metric_key: str | None = None,
                      as_of: str | None = None) -> dict:
    from ..api import andy_links
    if metric_key:
        out = andy_links.link_metric(metric_key)
        return out if out is not None else {"error": f"no crosswalk entry for {metric_key}"}
    if theme:
        out = andy_links.link_theme(theme, as_of)
        return out if out is not None else {"error": f"unknown theme {theme}"}
    return andy_links.link_themes()


TOOLS: list[ToolSpec] = [
    ToolSpec("find_company", "Resolve a company name or ticker to its platform id + basic profile.",
             _obj({"query": {"type": "string", "description": "company name or ticker"}}, ["query"]),
             _find_company),
    ToolSpec("semantic_facts",
             "The timestamped semantic-fact stream (catalyst events + kept expert insights) for a "
             "company and/or theme — the core 'what's happening / what changed' feed. as_of/since are "
             "YYYY-MM-DD point-in-time bounds.",
             _obj({"company_id": _CID, "theme": _THEME,
                   "as_of": {"type": "string", "description": "upper date bound YYYY-MM-DD"},
                   "since": {"type": "string", "description": "lower date bound YYYY-MM-DD"},
                   "limit": {"type": "integer", "default": 40}}),
             _semantic_facts),
    ToolSpec("search_documents",
             "Hybrid (vector + keyword) search over ingested documents & data-room uploads. Returns "
             "cited snippets — use for 'what does the research/filings say about X'.",
             _obj({"query": {"type": "string"}, "company_id": _CID, "k": {"type": "integer", "default": 8}},
                  ["query"]),
             _search_documents),
    ToolSpec("theme_overview", "Regime, segment scores, decision & coverage for a theme (the dashboard).",
             _obj({"theme": _THEME}, ["theme"]), _dash("overview")),
    ToolSpec("list_companies", "Companies in a theme with momentum/margin/conviction metrics.",
             _obj({"theme": _THEME}, ["theme"]), _dash("companies")),
    ToolSpec("company_detail", "Full profile for one company: segment, kpis, prices, fundamentals, signals, supply chain.",
             _obj({"cid": _CID, "theme": _THEME}, ["cid"]), _dash("company_detail")),
    ToolSpec("segment_detail", "Detail for one chain segment: members, landscape, KPIs.",
             _obj({"sid": {"type": "string", "description": "segment id, e.g. 'chip_memory'"}}, ["sid"]),
             _dash("segment_detail")),
    ToolSpec("list_segments", "The chain/cycle segments of a theme with tier + scores.",
             _obj({"theme": _THEME}, ["theme"]), _dash("segments")),
    ToolSpec("signals", "Recent per-company signal badges (catalyst events) for a theme.",
             _obj({"theme": _THEME}, ["theme"]), _dash("signals")),
    ToolSpec("catalysts", "Recent dated catalysts for a theme.",
             _obj({"theme": _THEME}, ["theme"]), _dash("catalysts")),
    ToolSpec("calendar", "Upcoming dated catalyst calendar for a theme.",
             _obj({"theme": _THEME}, ["theme"]), _dash("calendar")),
    ToolSpec("theme_landscape", "Industry-landscape (segment concentration / HHI / top players) for a theme.",
             _obj({"theme": _THEME}, ["theme"]), _dash("landscape")),
    ToolSpec("regime", "The demand-cycle regime score/phase for a theme.",
             _obj({"theme": _THEME}, ["theme"]), _dash("regime")),
    ToolSpec("decision", "The house-view decision rail (opportunities/risks/actions) for a theme.",
             _obj({"theme": _THEME}, ["theme"]), _dash("decision")),
    ToolSpec("coverage", "Platform coverage: the 8 themes with company/segment counts.",
             _obj({"theme": _THEME}), _dash("coverage")),
    ToolSpec("supply_chain", "A company's suppliers, customers, equity stakes, tech-routes and risk edges.",
             _obj({"company_id": _CID}, ["company_id"]), _graph("supply_chain")),
    ToolSpec("company_competitors", "The companies competing in the same end-markets as a company.",
             _obj({"company_id": _CID}, ["company_id"]), _graph("landscape")),
    ToolSpec("single_source_risks", "Single-source dependency risk edges (optionally for one company).",
             _obj({"company_id": _CID}), _graph("single_source_risks")),
    ToolSpec("events", "Raw dated KG events (optionally filtered by company / since-date / types).",
             _obj({"company_id": _CID, "since": {"type": "string"},
                   "types": {"type": "array", "items": {"type": "string"}},
                   "limit": {"type": "integer", "default": 40}}),
             _graph("events")),
    ToolSpec("dataroom_docs",
             "List documents in the Genny Data Room (user-uploaded reports/notes), optionally "
             "filtered by theme/segment/company. Use search_documents to read their contents.",
             _obj({"theme": _THEME, "segment": {"type": "string"},
                   "company_id": _CID, "q": {"type": "string", "description": "title contains"}}),
             lambda **kw: __import__("xar.api.dataroom", fromlist=["list_docs"]).list_docs(**kw)),
    ToolSpec("macro_indicators",
             "XAR Andy macro module (siliconomics 宏观指标库). Modes: theme → the macro panel "
             "cross-linked (勾稽) to one industry chain, with point-in-time readings at as_of "
             "(look-ahead-safe) + overclaim-claim statuses; metric → reverse crosswalk for one "
             "metric_key (linked themes/segments/tech-routes/companies + rationale); omit both → "
             "the full 8-theme crosswalk matrix. ALWAYS quote the identification watermark: soft "
             "metrics are 未识别 (unidentified) — never present them as causal facts.",
             _obj({"theme": _THEME,
                   "metric_key": {"type": "string",
                                  "description": "a siliconomics metric_key, e.g. 'capex.hyperscaler_capex'"},
                   "as_of": {"type": "string", "description": "ISO date look-ahead boundary; default today"}}),
             _macro_indicators),
]
# Phase 3 appends `dataroom_docs`; Fenny pricing (`fenny_quote`) and `start_report` are
# later additions — same ToolSpec pattern.

_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}


def openai_tool_defs() -> list[dict]:
    """Render the registry as OpenAI/LiteLLM function-calling tool definitions."""
    return [{"type": "function",
             "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in TOOLS]


def execute(name: str, args: dict) -> str:
    """Run tool `name` with `args`; return a JSON string (truncated). Never raises —
    an error becomes a JSON error payload so the agent loop can continue."""
    spec = _BY_NAME.get(name)
    if spec is None:
        return json.dumps({"error": f"unknown tool '{name}'"})
    try:
        result = spec.fn(**(args or {}))
    except TypeError as e:
        return json.dumps({"error": f"bad arguments for {name}: {e}"})
    except Exception as e:  # noqa: BLE001
        log.warning("tool %s failed: %s", name, e)
        return json.dumps({"error": f"{type(e).__name__}: {e}"})
    out = json.dumps(result, ensure_ascii=False, default=str)
    if len(out) > _MAX_RESULT_CHARS:
        out = out[:_MAX_RESULT_CHARS] + f'… [truncated, {len(out)} chars total]'
    return out
