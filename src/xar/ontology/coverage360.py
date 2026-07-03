"""360° 覆盖度本体 —— "一家公司我们知道多少"的机器可算口径。

代码即真相地定义单一公司信息覆盖的 16 个维度(每维:探针 SQL + 目标行数 + 权重),
据此为 947 家公司算出 0–1 的维度分与加权综合分。用途:
  · Ops 覆盖度看板(哪条链/哪个维度是盲区);
  · 采集优先级(缺什么补什么,而非全量重拉);
  · CompanyThesis 的 coverage_gaps 诚实声明与 conviction 上限;
  · 前端公司页的 coverage ring。

探针只做 GROUP BY company_id 的批量计数(全库一轮 16 条 SQL,而非 947×16),
单表缺失/查询失败降级为该维度全 0,绝不让评分器崩掉调用方。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..storage import db


@dataclass(frozen=True)
class Dimension:
    key: str
    name: str
    name_cn: str
    weight: float          # 合计 = 1.0
    target: int            # 行数达到 target 记满分(线性)
    sql: str | None        # (company_id, n) 聚合;None = 代码侧探针


DIMENSIONS: tuple[Dimension, ...] = (
    Dimension("identity", "Identity & classification", "身份与分类", 0.04, 1, None),
    Dimension("documents", "Filings & documents", "公告与文档", 0.08, 5,
              "SELECT company_id, count(*) FROM documents WHERE company_id IS NOT NULL GROUP BY 1"),
    Dimension("catalysts", "Dated catalysts (past)", "已发生催化剂", 0.10, 8,
              "SELECT company_id, count(*) FROM kg_events "
              "WHERE company_id IS NOT NULL AND invalidated_at IS NULL GROUP BY 1"),
    Dimension("forward", "Forward calendar", "前瞻日历", 0.07, 2,
              "SELECT company_id, count(*) FROM event_calendar "
              "WHERE company_id IS NOT NULL AND scheduled_for >= CURRENT_DATE GROUP BY 1"),
    Dimension("guidance", "Guidance / forward claims", "指引与前瞻声明", 0.06, 2,
              "SELECT company_id, count(*) FROM kg_events "
              "WHERE time_orientation='forward_looking' AND invalidated_at IS NULL GROUP BY 1"),
    Dimension("fundamentals", "Financial snapshot", "财务快照", 0.08, 10,
              "SELECT company_id, count(DISTINCT metric) FROM fundamentals GROUP BY 1"),
    Dimension("fin_series", "Financial time series", "财务时序(含 capex)", 0.08, 8,
              "SELECT company_id, count(*) FROM fundamentals WHERE period_end IS NOT NULL GROUP BY 1"),
    Dimension("estimates", "Analyst estimates", "分析师预期", 0.07, 4,
              "SELECT company_id, count(*) FROM estimates GROUP BY 1"),
    Dimension("ratings", "Ratings & price targets", "评级与目标价", 0.05, 3,
              "SELECT company_id, count(*) FROM analyst_ratings GROUP BY 1"),
    Dimension("prices", "Market prices", "行情", 0.06, 30,
              "SELECT company_id, count(*) FROM prices GROUP BY 1"),
    Dimension("ownership", "Institutional ownership", "机构持仓(13F)", 0.06, 5,
              "SELECT company_id, count(*) FROM holdings GROUP BY 1"),
    Dimension("insider", "Insider activity", "内部人交易", 0.04, 3,
              "SELECT company_id, count(*) FROM insider_trades GROUP BY 1"),
    Dimension("supply_chain", "Supply-chain edges", "供应链关系", 0.08, 6,
              "SELECT c.id, count(*) FROM companies c JOIN kg_edges e "
              "ON (e.src_id = c.id OR e.dst_id = c.id) GROUP BY 1"),
    Dimension("sentiment", "Social & expert voice", "社媒与专家声音", 0.04, 5,
              "SELECT company_id, count(*) FROM social_posts GROUP BY 1"),
    Dimension("insights", "Expert insights", "专家洞见", 0.04, 2,
              "SELECT company_id, count(*) FROM expert_insights WHERE kept GROUP BY 1"),
    Dimension("thesis", "Investment thesis", "投资论点", 0.05, 1,
              "SELECT company_id, count(DISTINCT version) FROM company_thesis GROUP BY 1"),
)

assert abs(sum(d.weight for d in DIMENSIONS) - 1.0) < 1e-6, "dimension weights must sum to 1"


def _probe(dim: Dimension) -> dict[str, int]:
    if dim.sql is None:
        return {}
    try:
        return {r["company_id"]: int(r["count"]) for r in db.query(dim.sql)}
    except Exception:  # noqa: BLE001 — 缺表/缺列 = 该维度全 0,不崩评分
        return {}


def coverage_all() -> dict[str, dict]:
    """全宇宙覆盖度:{company_id: {dims: {key: {n, score}}, composite}}。一轮批量 SQL。"""
    from ..ingestion.registry import COMPANIES

    counts = {d.key: _probe(d) for d in DIMENSIONS}
    out: dict[str, dict] = {}
    for c in COMPANIES:
        cid = c["id"]
        dims: dict[str, dict] = {}
        composite = 0.0
        for d in DIMENSIONS:
            n = 1 if d.sql is None else counts[d.key].get(cid, 0)
            score = min(n / d.target, 1.0)
            dims[d.key] = {"n": n, "score": round(score, 3)}
            composite += d.weight * score
        out[cid] = {"dims": dims, "composite": round(composite, 3)}
    return out


def coverage_for(company_id: str) -> dict | None:
    return coverage_all().get(company_id)


def gaps_for(company_id: str, *, threshold: float = 0.34) -> list[str]:
    """低于阈值的维度中文名清单 —— 喂给论点生成器的 coverage_gaps 素材。"""
    cov = coverage_for(company_id)
    if not cov:
        return []
    return [d.name_cn for d in DIMENSIONS if cov["dims"][d.key]["score"] < threshold]


def summary_by_theme() -> list[dict]:
    """Ops 看板口径:每条链 × 每维度的满足率(score≥0.34 的公司占比)+ 平均综合分。"""
    from ..ingestion.registry import COMPANIES, THEMES

    cov = coverage_all()
    rows: list[dict] = []
    for tid, meta in THEMES.items():
        members = [c["id"] for c in COMPANIES if tid in (c.get("themes") or ())]
        if not members:
            continue
        dims = {}
        for d in DIMENSIONS:
            ok = sum(1 for cid in members if cov[cid]["dims"][d.key]["score"] >= 0.34)
            dims[d.key] = round(ok / len(members), 3)
        rows.append({
            "theme": tid, "name": meta["name"], "name_cn": meta["nameCn"],
            "companies": len(members),
            "avg_composite": round(sum(cov[cid]["composite"] for cid in members) / len(members), 3),
            "dims": dims,
        })
    return rows
