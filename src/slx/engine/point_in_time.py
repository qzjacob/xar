"""Point-in-time 查询上下文 —— 金融级前视防护的命门。

铁律：任何回测/登记簿判定只能取 knowledge_time <= as_of 的行（"那天能知道的值"），
严禁 SELECT latest。本模块是唯一允许读取 observation 做判定的入口。
"""
from __future__ import annotations

from datetime import date


class NoData(Exception):
    """as_of 之前该指标无可用读数。"""


class PointInTimeContext:
    """绑定一个 (连接, as_of) ——所有读取都自动加 knowledge_time <= as_of 谓词。"""

    def __init__(self, conn, as_of: date):
        self.conn = conn
        self.as_of = as_of

    def value(self, metric_key: str, source_id: str | None = None) -> float:
        """as_of 当日能知道的最新读数：valid_time 最近、其上 knowledge_time 最新的一行。"""
        sql = """
            SELECT value FROM observation
            WHERE metric_key = %s
              AND knowledge_time <= %s
              AND value IS NOT NULL
              AND (%s::text IS NULL OR source_id = %s)
            ORDER BY valid_time DESC, knowledge_time DESC
            LIMIT 1
        """
        row = self.conn.execute(sql, (metric_key, self.as_of, source_id, source_id)).fetchone()
        if not row or row[0] is None:
            raise NoData(metric_key)
        return float(row[0])

    def series(self, metric_key: str, n_points: int) -> list[tuple[date, float]]:
        """最近 n 个 valid_time 上的 point-in-time 最佳估计，按时间升序返回。"""
        sql = """
            SELECT DISTINCT ON (valid_time) valid_time, value
            FROM observation
            WHERE metric_key = %s
              AND knowledge_time <= %s
              AND value IS NOT NULL
            ORDER BY valid_time DESC, knowledge_time DESC
            LIMIT %s
        """
        rows = self.conn.execute(sql, (metric_key, self.as_of, n_points)).fetchall()
        if not rows:
            raise NoData(metric_key)
        return [(r[0], float(r[1])) for r in reversed(rows)]  # 升序

    def slope(self, metric_key: str, n_points: int) -> float:
        """近 n 点的最小二乘斜率（符号即趋势方向）。"""
        pts = self.series(metric_key, n_points)
        if len(pts) < 2:
            raise NoData(metric_key)
        ys = [v for _, v in pts]
        xs = list(range(len(ys)))
        n = len(xs)
        sx, sy = sum(xs), sum(ys)
        sxx = sum(x * x for x in xs)
        sxy = sum(x * y for x, y in zip(xs, ys))
        denom = n * sxx - sx * sx
        if denom == 0:
            raise NoData(metric_key)
        return (n * sxy - sx * sy) / denom
