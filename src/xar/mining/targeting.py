"""T0 目标化 —— 从活跃 thesis + ontology 决定「找什么」(挖掘非盲目)。零 LLM。

把「被信号/事件挑战的论点 → 该盯什么」翻译成可操作的挖掘目标:每个目标带公司中文
别名、主题、技术路线、活跃论点的 watch 事件/盯盘项、以及对应的中文关键词(cn_routing)。
用途:①名册采集优先级(优先拉与被挑战公司相关的号);②triage 待处理队列的目标化重排
(让被挑战公司的文档先 triage);③ops/CLI 可见「当前在猎什么」;④未来搜索的查询词源。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..ingestion.registry import ROUTE_THEMES, company_by_id
from ..logging import get_logger
from ..ontology import cn_routing

log = get_logger("xar.targeting")


@dataclass
class MiningTarget:
    company_id: str
    name: str
    themes: tuple[str, ...]
    aliases_zh: tuple[str, ...]
    routes: tuple[str, ...] = ()
    watch_event_types: tuple[str, ...] = ()
    watch_terms_zh: tuple[str, ...] = ()      # what_to_watch 的中文盯盘项
    challenged_pillars: tuple[str, ...] = ()
    hunt_terms_zh: tuple[str, ...] = field(default_factory=tuple)  # 别名+路线+主题中文词
    priority: float = 0.0                     # 被挑战优先


def _cn_aliases(c: dict) -> tuple[str, ...]:
    """公司的中文别名(含中文名段)。"""
    out = []
    for a in [c.get("name", ""), *c.get("aliases", [])]:
        if a and any(ord(ch) > 0x2E80 for ch in a):  # 含 CJK
            out.append(a.strip())
    return tuple(dict.fromkeys(out))


def _company_routes(c: dict) -> tuple[str, ...]:
    """公司主题对应的技术路线(经 ROUTE_THEMES 反查)。"""
    themes = set(c.get("themes") or ())
    return tuple(r for r, ths in ROUTE_THEMES.items() if themes & set(ths))


def build_target(company_id: str, *, challenged: bool = False) -> MiningTarget | None:
    c = company_by_id(company_id)
    if c is None:
        return None
    themes = tuple(c.get("themes") or ())
    aliases = _cn_aliases(c)
    routes = _company_routes(c)
    watch_ev: tuple[str, ...] = ()
    watch_terms: tuple[str, ...] = ()
    challenged_pillars: tuple[str, ...] = ()
    try:
        from ..research import thesis as th

        row = th.latest(company_id)
        if row:
            content = row["content"] if isinstance(row["content"], dict) else {}
            watch_ev = tuple(sorted({e for p in content.get("pillars", [])
                                     for e in p.get("watch_event_types", [])}))
            watch_terms = tuple(w.get("what_zh", "") for w in content.get("what_to_watch", [])
                                if w.get("what_zh"))
            h = th.health(company_id)
            if h:
                challenged_pillars = tuple(p["key"] for p in h.get("pillars", [])
                                           if p.get("status") == "challenging")
    except Exception:  # noqa: BLE001
        pass
    # 中文猎词:别名 + 主题词 + 路线词
    terms: list[str] = list(aliases)
    for t in themes:
        terms += list(cn_routing.CN_THEME_TERMS.get(t, ())[:4])
    for r in routes:
        terms += list(cn_routing.CN_ROUTE_TERMS.get(r, ())[:2])
    return MiningTarget(
        company_id=company_id, name=c.get("name", company_id), themes=themes,
        aliases_zh=aliases, routes=routes, watch_event_types=watch_ev,
        watch_terms_zh=watch_terms, challenged_pillars=challenged_pillars,
        hunt_terms_zh=tuple(dict.fromkeys(terms)),
        priority=1.0 if challenged else 0.5)


def build_targets(limit: int = 30) -> list[MiningTarget]:
    """当前挖掘目标(被挑战论点优先)。供名册优先级 / triage 重排 / 可见性。"""
    from ..research import thesis_signals

    seen: dict[str, MiningTarget] = {}
    try:
        for cid in thesis_signals.challenged_companies(limit=limit):
            t = build_target(cid, challenged=True)
            if t:
                seen[cid] = t
    except Exception as e:  # noqa: BLE001
        log.warning("challenged targets: %s", e)
    # 补充有论点的其余公司(非挑战,低优先)
    if len(seen) < limit:
        from ..storage import db

        rows = db.query("SELECT DISTINCT company_id FROM company_thesis "
                        "WHERE company_id IS NOT NULL LIMIT %s", (limit * 2,))
        for r in rows:
            cid = r["company_id"]
            if cid in seen:
                continue
            t = build_target(cid, challenged=False)
            if t:
                seen[cid] = t
            if len(seen) >= limit:
                break
    return sorted(seen.values(), key=lambda t: -t.priority)[:limit]


def hunt_terms(limit: int = 30) -> list[str]:
    """扁平的中文猎词集合(去重),ops 可见 / 未来搜索查询源。"""
    out: list[str] = []
    for t in build_targets(limit):
        for term in t.hunt_terms_zh:
            if term not in out:
                out.append(term)
    return out
