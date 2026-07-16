"""alt_signals 的规范写入/读取面(providers/alt/* 与 thesis_signals 共用)。"""
from __future__ import annotations

import json
from datetime import date

from . import db


def upsert_signal(signal_key: str, *, period_end: date, value: float,
                  company_id: str | None = None, theme: str | None = None,
                  unit: str | None = None, source: str = "", meta: dict | None = None) -> None:
    # DO UPDATE 带值变守卫:值没变就不动行(尤其不动 observed_at)。observed_at 是
    # series(as_of) 的 PIT 谓词——回填式重写(flow 的 90 日尾巴/富途 7 日窗)若无条件
    # 刷 now(),历史行的"知晓时间"每日归零,任何过去 as_of 都读空(MF 评审 #1)。
    db.execute(
        """INSERT INTO alt_signals(signal_key, company_id, theme, period_end, value,
                                   unit, meta, source)
           VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
           ON CONFLICT (signal_key, COALESCE(company_id, ''), COALESCE(theme, ''), period_end)
           DO UPDATE SET value=EXCLUDED.value, meta=EXCLUDED.meta, observed_at=now()
           WHERE alt_signals.value IS DISTINCT FROM EXCLUDED.value""",
        (signal_key, company_id, theme, period_end, float(value), unit,
         json.dumps(meta or {}, ensure_ascii=False, default=str), source))


def series(signal_key: str, *, company_id: str | None = None, theme: str | None = None,
           as_of: date | None = None, limit: int = 36) -> list[dict]:
    """按 observed_at <= as_of 的 PIT 读(倒序 period_end)。"""
    sql = ("SELECT period_end, value, unit, meta, observed_at FROM alt_signals "
           "WHERE signal_key=%s AND company_id IS NOT DISTINCT FROM %s "
           "AND theme IS NOT DISTINCT FROM %s")
    params: list = [signal_key, company_id, theme]
    if as_of:
        sql += " AND observed_at <= %s"
        params.append(as_of)
    sql += " ORDER BY period_end DESC LIMIT %s"
    params.append(limit)
    return db.query(sql, params)


def latest_by_company(company_id: str) -> list[dict]:
    return db.query(
        "SELECT DISTINCT ON (signal_key) signal_key, period_end, value, unit, meta, observed_at "
        "FROM alt_signals WHERE company_id=%s ORDER BY signal_key, period_end DESC", (company_id,))
