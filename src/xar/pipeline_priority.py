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

# 严格优先序(2026-07-28 用户裁定):alphapai > gangtise > aifinmarket,**100% 抢占**——
# 只要靠前的源还有待抽文档,后面的源与尾部源一律拿不到 GPU。次序是用户的战略取舍(alphapai 时效性
# 最强),**刻意不等于**实测信噪比(gangtise 70.2% > alphapai 38.2% > aifinmarket 8.8%)。
STRICT_PRIORITY_ORDER: tuple[str, ...] = ("alphapai", "gangtise", "aifinmarket")

# 向后兼容:parse/build_kg/expert 仍用 priority_order_sql 做"是否优先流"的二元判断。
PRIORITY_SOURCES: tuple[str, ...] = STRICT_PRIORITY_ORDER

# 尾部源的**信息质量权重** = 实测 expert kept_rate(%),2026-07-28 全量统计:
#   wechat 8.5 / edgar 6.0 / finnhub 5.9 / x 3.5 / rss 2.3 / arxiv 0.3 / journal 0.0
# 尾部只分配「严格头部取完后剩下的产能」,并按此权重成比例切分每日额度 —— 质量越高分得越多,
# 某源没有待抽文档时它的份额自动流给其他尾部源(不浪费 GPU)。未列出的源用 _TAIL_DEFAULT_WEIGHT。
TAIL_QUALITY_WEIGHTS: dict[str, float] = {
    "wechat": 8.5, "edgar": 6.0, "finnhub": 5.9, "x": 3.5,
    "rss": 2.3, "arxiv": 0.3, "journal": 0.1, "futu": 2.0, "social": 2.0,
}
_TAIL_DEFAULT_WEIGHT = 2.0

# 队列深度阻尼指数(2026-07-29 加入)。纯质量权重对**积压有多深**零感知:实测 edgar 只占尾部
# backlog 的 1.3% 却与占 64.7% 的 finnhub 拿到相同绝对份额,深队列因此长期收敛不动。
# 有效权重 = 质量权重 × pending^alpha:
#   alpha=0   → 完全退回纯质量(**逐位兼容旧行为**,回滚位);
#   alpha=0.5 → 平方根阻尼(默认):既跟随积压深度,又不让大源彻底吃掉高质量小源;
#   alpha=1   → 按积压质量加权总量分配(小源会被饿死,不建议)。
# 取 0.5 的实测效果(每批 8 篇,pending finnhub 271k / x 142k / edgar 5.4k):
#   旧 edgar 3 / finnhub 3 / x 2  →  新 edgar 1 / finnhub 5 / x 2。
DEFAULT_TAIL_DEPTH_ALPHA = 0.5


def tail_weight(source: str) -> float:
    return TAIL_QUALITY_WEIGHTS.get(source, _TAIL_DEFAULT_WEIGHT)


def effective_tail_weight(source: str, pending: int,
                          alpha: float = DEFAULT_TAIL_DEPTH_ALPHA) -> float:
    """信息质量 × 队列深度阻尼。alpha<=0 或 pending<=0 时退化为纯质量权重
    (后者保证空池不会因 0**alpha 抹平权重而拿到异常份额)。纯函数,无 xar 依赖。"""
    w = tail_weight(source)
    if alpha <= 0 or pending <= 0:
        return w
    return w * (float(pending) ** alpha)


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
    """严格优先档位:alphapai=0 < gangtise=1 < aifinmarket=2 < 其余(尾部)=3。
    用作 `ORDER BY {tier_order_sql(col)} ASC, …`(升序:0 先取)。头部三源**逐级 100% 抢占**;
    尾部同档,其内部配额由 qwen_drain 按 TAIL_QUALITY_WEIGHTS 成比例切分。均为可信常量,可内联。"""
    whens = " ".join(f"WHEN {col} = '{s}' THEN {i}"
                     for i, s in enumerate(STRICT_PRIORITY_ORDER))
    tail = len(STRICT_PRIORITY_ORDER)
    return f"CASE {whens} ELSE {tail} END" if whens else str(tail)
