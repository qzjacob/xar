"""构建拒绝/跳过台账写入(phanny / thesis / earnings 共用)。

**为什么存在**:此前一次构建没产出的原因只进 stdout —— validate 违规还被截断到前 6 条 ——
于是「上周论点为何停产」在库里查不到,thesis build 停摆时只能靠翻日志猜。台账让它变成一句 SQL。

**契约:绝不 raise**。台账是观测面,不是业务面;它自己坏掉不许拖垮一次真实构建
(与 `llm._record` 同philosophy,但失败要 log.warning 而非裸 pass —— 静默的观测面等于没有)。
"""
from __future__ import annotations

import json

from ..logging import get_logger
from .db import execute

log = get_logger("xar.buildlog")

DOMAINS = ("phanny", "thesis", "earnings")


def record(domain: str, company_id: str | None, *, stage: str, status: str,
           reason: str | None = None, problems: list | None = None,
           run_id: str | None = None, event_date=None, model: str | None = None,
           attempt: int | None = None) -> None:
    """记一条「没产出」的原因。problems 全量入库(不截断)—— 截断过的清单无法用来定位系统性纪律模式。"""
    try:
        execute(
            "INSERT INTO build_rejections(domain, company_id, event_date, run_id, stage, status, "
            "reason, problems, attempt, model) VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)",
            (domain, company_id, event_date, run_id, stage, status,
             (reason or "")[:2000] or None,
             json.dumps(list(problems or []), ensure_ascii=False, default=str),
             attempt, model))
    except Exception as e:  # noqa: BLE001 — never-raise 契约;但要留声,静默的观测面等于没有
        log.warning("buildlog record failed (%s/%s %s): %s", domain, company_id, status, str(e)[:120])


def recent(domain: str | None = None, company_id: str | None = None,
           limit: int = 20) -> list[dict]:
    """读回台账(CLI `xar phanny why` / 诊断用)。"""
    from .db import query

    where, params = [], []
    if domain:
        where.append("domain=%s")
        params.append(domain)
    if company_id:
        where.append("company_id=%s")
        params.append(company_id)
    params.append(limit)
    sql = ("SELECT id, domain, company_id, event_date, run_id, stage, status, reason, problems, "
           "attempt, model, created_at FROM build_rejections"
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY created_at DESC LIMIT %s")
    try:
        return query(sql, tuple(params))
    except Exception as e:  # noqa: BLE001 — 读侧同样不许炸调用方
        log.warning("buildlog recent failed: %s", str(e)[:120])
        return []


def summary(domain: str | None = None, hours: int = 24) -> list[dict]:
    """近 N 小时按 (domain, stage, status) 聚合 —— 一眼看出「是模型不出 JSON 还是纪律不过」。"""
    from .db import query

    sql = ("SELECT domain, stage, status, count(*) AS n, max(created_at) AS latest "
           "FROM build_rejections WHERE created_at >= now() - (%s || ' hours')::interval"
           + (" AND domain=%s" if domain else "")
           + " GROUP BY domain, stage, status ORDER BY n DESC")
    params = (hours, domain) if domain else (hours,)
    try:
        return query(sql, params)
    except Exception as e:  # noqa: BLE001
        log.warning("buildlog summary failed: %s", str(e)[:120])
        return []
