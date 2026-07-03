"""确定性 seed —— 为端到端验证注入可复现的双时态观测（不依赖任何外部 API）。

它故意制造两种被测现象：
  1) 同一 valid_time 的两版 knowledge_time（labor.labor_share 的首发 + 修订）——验证前视防护。
  2) 与 K 文当下数据一致的 earnings 序列——使登记簿 concentration_eq_earnings 判为 'falsified'
     （Mag7 盈利贡献跌破 0.33 且"其余 493"EPS 增速上行，正是"市值集中≠盈利集中"的证伪）。

    python -m ingestion.seed
"""
from __future__ import annotations

from datetime import date

from slx.ingestion.base import Connector


class SeedConnector(Connector):
    source_id = "seed"
    connector = "ingestion.seed"

    def fetch(self) -> list[dict]:
        rows: list[dict] = []

        # (1) labor.labor_share：同一经济季度（2025Q1，valid=2025-03-31）的首发与修订
        rows.append({"metric_key": "labor.labor_share", "source_id": "fred", "unit": "pct",
                     "valid_time": date(2025, 3, 31), "knowledge_time": date(2025, 4, 30), "value": 0.582})
        rows.append({"metric_key": "labor.labor_share", "source_id": "fred", "unit": "pct",
                     "valid_time": date(2025, 3, 31), "knowledge_time": date(2025, 7, 30), "value": 0.575})

        # (2) earnings.mag7_contribution_pct：当前读数 0.31（已跌破 0.33）
        rows.append({"metric_key": "earnings.mag7_contribution_pct", "source_id": "factset", "unit": "pct",
                     "valid_time": date(2026, 3, 31), "knowledge_time": date(2026, 5, 15), "value": 0.31})

        # (3) earnings.rest493_eps_growth_pct：四季上行（slope > 0）
        rest493 = [
            (date(2025, 6, 30), date(2025, 8, 15), 0.069),
            (date(2025, 9, 30), date(2025, 11, 15), 0.075),
            (date(2025, 12, 31), date(2026, 2, 15), 0.085),
            (date(2026, 3, 31), date(2026, 5, 15), 0.092),
        ]
        for vt, kt, val in rest493:
            rows.append({"metric_key": "earnings.rest493_eps_growth_pct", "source_id": "factset",
                         "unit": "pct", "valid_time": vt, "knowledge_time": kt, "value": val})

        return rows


if __name__ == "__main__":
    run_id = SeedConnector().run()
    print(f"✓ seed 已写入，ingest_run_id={run_id}")
