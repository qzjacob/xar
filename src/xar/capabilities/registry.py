"""能力登记簿(代码即真相)—— 一处定义分析能力,多入口共用(UA-P0)。

由 chathy/tools.py 的 ToolSpec 泛化而来:每个 `CapabilitySpec` = 稳定名 + JSON-schema + 一个
**进程内普通函数**(dashboards/retrieval/research 已暴露的那些),无 HTTP 自调、不重实现。
- `chathy=True` 的能力经 `openai_tool_defs()` 渲染成 Chathy 函数调用工具,`execute()` 跑并压缩返回;
- `kind="build"/duration="slow"` 的能力(生成/写库、分钟级)由 `capabilities/runs.py` 异步跑,
  经 `/api/run/{name}`、`xar run`、Chathy 的 schedule 型工具触发。

新增能力 = 往 `CAPABILITIES` 追加一条。chathy/tools.py 保留为 re-export shim(import 面不变)。
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from ..ingestion.registry import THEMES, company_by_id
from ..logging import get_logger

log = get_logger("xar.capabilities")

_THEME_ENUM = list(THEMES.keys()) if isinstance(THEMES, dict) else list(THEMES)
_MAX_RESULT_CHARS = 8000


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    description: str
    parameters: dict           # JSON Schema for the arguments object
    fn: Callable[..., object]
    kind: str = "read"         # read | build(写库/生成)
    duration: str = "fast"     # fast(即答)| slow(分钟级 → 走 capability_runs)
    chathy: bool = True        # 是否渲染为 Chathy 工具


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [],
            "additionalProperties": False}


_THEME = {"type": "string", "enum": _THEME_ENUM,
          "description": "One of the 8 investment themes."}
_CID = {"type": "string", "description": "A company id (e.g. 'nvidia', 'u_tw_2330'). "
        "Use find_company first if you only have a name/ticker."}


# --- read-tool implementations (thin wrappers so signatures stay explicit) --------------
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


def _get_thesis(company_id: str, refresh: bool = False) -> dict:
    from ..research import thesis as th
    if refresh:
        out = th.build(company_id)
        if out["status"] not in ("built", "skipped"):
            return out
    row = th.latest(company_id)
    if row is None:
        return {"company_id": company_id, "thesis": None,
                "note": "无论点版本;可先调用 refresh=true 生成(需数秒),或该公司接地事实不足。"}
    content = row["content"]
    return {"company_id": company_id, "version": row["version"], "as_of": str(row["as_of"]),
            "stance": row["stance"], "conviction": row["conviction"],
            "quality": row["quality"], "changed_because": row["changed_because"],
            "content": content, "health": th.health(company_id)}


def _alt_signals(company_id: str) -> dict:
    """另类数据信号(月营收/招聘/开源动能等)的 z-score 快照 + 支柱信号分 + 合并健康度。"""
    from ..research import thesis_signals
    return {"signals": thesis_signals.signal_snapshot(company_id),
            "pillar_scores": thesis_signals.pillar_signal_scores(company_id),
            "health": thesis_signals.health_v2(company_id)}


def _coverage_360(company_id: str | None = None) -> dict | list:
    from ..ontology import coverage360
    if company_id:
        cov = coverage360.coverage_for(company_id)
        return {"company_id": company_id, "coverage": cov,
                "gaps": coverage360.gaps_for(company_id)}
    return coverage360.summary_by_theme()


def _macro_indicators(theme: str | None = None, metric_key: str | None = None,
                      as_of: str | None = None) -> dict:
    from ..api import andy_links
    if metric_key:
        out = andy_links.link_metric(metric_key)
        return out if out is not None else {"error": f"no crosswalk entry for {metric_key}"}
    if theme:
        from ..macro import view as macro_view
        out = andy_links.link_theme(theme, as_of)
        if out is None:
            return {"error": f"unknown theme {theme}"}
        return macro_view.compact_theme_macro(out)     # UA-P2:共享压缩器(drop series 保水印)
    full = andy_links.link_themes()
    return {"themes": [{
        "theme": t["theme"], "name_cn": t["name_cn"], "kind": t["kind"],
        "metric_keys": [m["metric_key"] for m in t["metrics"]],
        "overclaims": t["overclaims"],
    } for t in full["themes"]],
        "platform_metric_keys": [m["metric_key"] for m in full["platform_metrics"]]}


# --- build-capability implementations (kind=build; run via capabilities/runs.py) --------
def _build_earnings_verdict(company_id: str, force: bool = False) -> dict:
    from ..research import earnings
    return earnings.build_verdict(company_id, force=force)


def _build_thesis(company_id: str, force: bool = False) -> dict:
    from ..research import thesis
    return thesis.build(company_id, force=force)


def _refresh_exploration(domain: str | None = None) -> dict:
    from ..exploration import ingest, synthesis
    if domain:
        ing = ingest.ingest_domain(domain)
        syn = synthesis.synthesize(domain)
        return {"domain": domain, "ingest": ing, "synthesis": syn}
    ing = ingest.ingest_all()
    syn = synthesis.synthesize_all()
    return {"ingest": ing, "synthesis": syn}


def _report(company_id: str, kind: str = "deep_report", since: str | None = None) -> dict:
    from ..agents import graph
    return graph.run_report({"kind": kind, "company_id": company_id, "since": since})


CAPABILITIES: list[CapabilitySpec] = [
    CapabilitySpec("find_company", "Resolve a company name or ticker to its platform id + basic profile.",
                   _obj({"query": {"type": "string", "description": "company name or ticker"}}, ["query"]),
                   _find_company),
    CapabilitySpec("semantic_facts",
                   "The timestamped semantic-fact stream (catalyst events + kept expert insights) for a "
                   "company and/or theme — the core 'what's happening / what changed' feed. as_of/since are "
                   "YYYY-MM-DD point-in-time bounds.",
                   _obj({"company_id": _CID, "theme": _THEME,
                         "as_of": {"type": "string", "description": "upper date bound YYYY-MM-DD"},
                         "since": {"type": "string", "description": "lower date bound YYYY-MM-DD"},
                         "limit": {"type": "integer", "default": 40}}),
                   _semantic_facts),
    CapabilitySpec("search_documents",
                   "Hybrid (vector + keyword) search over ingested documents & data-room uploads. Returns "
                   "cited snippets — use for 'what does the research/filings say about X'.",
                   _obj({"query": {"type": "string"}, "company_id": _CID, "k": {"type": "integer", "default": 8}},
                        ["query"]),
                   _search_documents),
    CapabilitySpec("theme_overview", "Regime, segment scores, decision & coverage for a theme (the dashboard).",
                   _obj({"theme": _THEME}, ["theme"]), _dash("overview")),
    CapabilitySpec("list_companies", "Companies in a theme with momentum/margin/conviction metrics.",
                   _obj({"theme": _THEME}, ["theme"]), _dash("companies")),
    CapabilitySpec("company_detail", "Full profile for one company: segment, kpis, prices, fundamentals, signals, supply chain.",
                   _obj({"cid": _CID, "theme": _THEME}, ["cid"]), _dash("company_detail")),
    CapabilitySpec("segment_detail", "Detail for one chain segment: members, landscape, KPIs.",
                   _obj({"sid": {"type": "string", "description": "segment id, e.g. 'chip_memory'"}}, ["sid"]),
                   _dash("segment_detail")),
    CapabilitySpec("list_segments", "The chain/cycle segments of a theme with tier + scores.",
                   _obj({"theme": _THEME}, ["theme"]), _dash("segments")),
    CapabilitySpec("signals", "Recent per-company signal badges (catalyst events) for a theme.",
                   _obj({"theme": _THEME}, ["theme"]), _dash("signals")),
    CapabilitySpec("catalysts", "Recent dated catalysts for a theme.",
                   _obj({"theme": _THEME}, ["theme"]), _dash("catalysts")),
    CapabilitySpec("calendar", "Upcoming dated catalyst calendar for a theme.",
                   _obj({"theme": _THEME}, ["theme"]), _dash("calendar")),
    CapabilitySpec("theme_landscape", "Industry-landscape (segment concentration / HHI / top players) for a theme.",
                   _obj({"theme": _THEME}, ["theme"]), _dash("landscape")),
    CapabilitySpec("regime", "The demand-cycle regime score/phase for a theme.",
                   _obj({"theme": _THEME}, ["theme"]), _dash("regime")),
    CapabilitySpec("decision", "The house-view decision rail (opportunities/risks/actions) for a theme.",
                   _obj({"theme": _THEME}, ["theme"]), _dash("decision")),
    CapabilitySpec("coverage", "Platform coverage: the 8 themes with company/segment counts.",
                   _obj({"theme": _THEME}), _dash("coverage")),
    CapabilitySpec("supply_chain", "A company's suppliers, customers, equity stakes, tech-routes and risk edges.",
                   _obj({"company_id": _CID}, ["company_id"]), _graph("supply_chain")),
    CapabilitySpec("company_competitors", "The companies competing in the same end-markets as a company.",
                   _obj({"company_id": _CID}, ["company_id"]), _graph("landscape")),
    CapabilitySpec("single_source_risks", "Single-source dependency risk edges (optionally for one company).",
                   _obj({"company_id": _CID}), _graph("single_source_risks")),
    CapabilitySpec("events", "Raw dated KG events (optionally filtered by company / since-date / types).",
                   _obj({"company_id": _CID, "since": {"type": "string"},
                         "types": {"type": "array", "items": {"type": "string"}},
                         "limit": {"type": "integer", "default": 40}}),
                   _graph("events")),
    CapabilitySpec("dataroom_docs",
                   "List documents in the Genny Data Room (user-uploaded reports/notes), optionally "
                   "filtered by theme/segment/company. Use search_documents to read their contents.",
                   _obj({"theme": _THEME, "segment": {"type": "string"},
                         "company_id": _CID, "q": {"type": "string", "description": "title contains"}}),
                   lambda **kw: __import__("xar.api.dataroom", fromlist=["list_docs"]).list_docs(**kw)),
    CapabilitySpec("get_thesis",
                   "The company's first-class INVESTMENT THESIS (typed pillars with evidence anchors, "
                   "bull/bear cases, risks, valuation scenarios, watch items) + machine-checked thesis "
                   "health (new facts confirming/challenging each pillar). The core decision object — "
                   "prefer this over re-deriving a view from raw facts. refresh=true rebuilds it from "
                   "the latest facts (slow, seconds).",
                   _obj({"company_id": _CID, "refresh": {"type": "boolean", "default": False}},
                        ["company_id"]),
                   _get_thesis),
    CapabilitySpec("alt_signals",
                   "Hedge-fund-grade ALTERNATIVE-DATA tracking for a company: z-scored high-frequency "
                   "signals (TW monthly revenue, ATS hiring velocity incl. AI-role share, GitHub OSS "
                   "momentum, package downloads, Wikipedia attention, theme-level KR chip exports) "
                   "mapped to thesis pillars, plus the merged event+signal thesis health. Use this to "
                   "answer 'is the thesis still on track right now' — signals lead earnings by weeks.",
                   _obj({"company_id": _CID}, ["company_id"]),
                   _alt_signals),
    CapabilitySpec("coverage_360",
                   "360° information-coverage score: per-dimension (16 dims: financials/estimates/"
                   "ownership/catalysts/thesis/...) coverage for one company (with gap list), or the "
                   "per-theme summary when company_id is omitted. Use to state honestly what the "
                   "platform does NOT know.",
                   _obj({"company_id": _CID}),
                   _coverage_360),
    CapabilitySpec("macro_indicators",
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

    # ── UA-P1 build 能力(kind=build/slow;经 capability_runs 异步跑;暂不直接暴露给 Chathy,
    #    Chathy 经 get_thesis(refresh)/earnings_verdict(refresh)/start_report 触达)──
    CapabilitySpec("build_earnings_verdict",
                   "生成/刷新某公司季报前多空裁决(分钟级 LLM,host 择优订阅执行器,docker 落 token)。",
                   _obj({"company_id": _CID, "force": {"type": "boolean", "default": False}}, ["company_id"]),
                   _build_earnings_verdict, kind="build", duration="slow", chathy=False),
    CapabilitySpec("build_thesis",
                   "生成/刷新某公司投资论点(接地事实 → 结构化 CompanyThesis)。",
                   _obj({"company_id": _CID, "force": {"type": "boolean", "default": False}}, ["company_id"]),
                   _build_thesis, kind="build", duration="slow", chathy=False),
    CapabilitySpec("refresh_exploration",
                   "刷新前沿探索(抓取 + LLM 合成 research fronts);domain 省略则全域。",
                   _obj({"domain": {"type": "string"}}),
                   _refresh_exploration, kind="build", duration="slow", chathy=False),
    CapabilitySpec("report",
                   "多智能体深度报告 DAG(scope→retrieve→analysts→debate/risk→editor→证据门→审批)。",
                   _obj({"company_id": _CID, "kind": {"type": "string", "default": "deep_report"},
                         "since": {"type": "string"}}, ["company_id"]),
                   _report, kind="build", duration="slow", chathy=False),
]


_BY_NAME: dict[str, CapabilitySpec] = {c.name: c for c in CAPABILITIES}


def by_name(name: str) -> CapabilitySpec | None:
    return _BY_NAME.get(name)


def chathy_specs() -> list[CapabilitySpec]:
    return [c for c in CAPABILITIES if c.chathy]


def openai_tool_defs() -> list[dict]:
    """Render the chathy-exposed capabilities as OpenAI/LiteLLM function-calling tool definitions."""
    return [{"type": "function",
             "function": {"name": c.name, "description": c.description, "parameters": c.parameters}}
            for c in chathy_specs()]


def execute(name: str, args: dict) -> str:
    """Run capability `name` with `args`; return a JSON string (truncated). Never raises —
    an error becomes a JSON error payload so the agent loop can continue."""
    spec = _BY_NAME.get(name)
    if spec is None:
        return json.dumps({"error": f"unknown tool '{name}'"})
    try:
        result = spec.fn(**(args or {}))
    except TypeError as e:
        return json.dumps({"error": f"bad arguments for {name}: {e}"})
    except Exception as e:  # noqa: BLE001
        log.warning("capability %s failed: %s", name, e)
        return json.dumps({"error": f"{type(e).__name__}: {e}"})
    out = json.dumps(result, ensure_ascii=False, default=str)
    if len(out) > _MAX_RESULT_CHARS:
        # Degrade list payloads by dropping tail items so the result STAYS valid JSON
        # (a blind slice leaves an unterminated string the model then misreads).
        trimmed = result
        while True:
            lists = [v for v in (trimmed.values() if isinstance(trimmed, dict) else [trimmed])
                     if isinstance(v, list) and v]
            longest = max(lists, key=len, default=None)
            if longest is None or len(longest) <= 1:
                break
            del longest[len(longest) // 2:]
            out = json.dumps(trimmed, ensure_ascii=False, default=str)
            if len(out) <= _MAX_RESULT_CHARS - 60:
                break
        if len(out) > _MAX_RESULT_CHARS:
            out = json.dumps({"error": "result too large", "chars": len(out)})
        else:
            trimmed_note = json.loads(out)
            if isinstance(trimmed_note, dict):
                trimmed_note["_truncated"] = "list tails dropped to fit the tool budget"
                out = json.dumps(trimmed_note, ensure_ascii=False, default=str)
    return out
