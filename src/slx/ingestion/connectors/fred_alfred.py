"""FRED / ALFRED —— 带 vintage 的宏观真值（A_official）。

产出（registry sources 标 source_id=fred, vintage_aware=true）：
  labor.labor_share   ← FRED series PRS85006173（非农劳动报酬份额，季度）
  macro.fed_funds_rate ← FRED series FEDFUNDS（联邦基金有效利率，月度）

为什么必须带 vintage（前视防护命门）：
  这两个序列**会被修订**。回测/登记簿判定只能用"那天能知道的值"。ALFRED 暴露每个
  发布快照（vintage）；本连接器用 fredapi 的 get_series_all_releases，把同一 valid_time
  的多版发布展开为多行：
    valid_time     = 观测期日期（economic date）
    knowledge_time = 该版发布日（realtime_start，即"何时得知"）
    vintage_date   = 同上发布日（ALFRED 口径，哪一版）
  于是 PointInTimeContext 能精确复刻"as_of 当天看到的是哪一版数字"。

需 FRED_API_KEY（免费）。缺 key 时**不写库、清晰报错并给出申请地址**，绝不硬编码。
依赖 fredapi（已在 requirements）；延迟导入，缺库时给安装提示。

    python -m ingestion.connectors.fred_alfred
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone

import pandas as pd  # fredapi 依赖 pandas；与现有栈一致

from slx.ingestion.base import Connector

# (metric_key, series_id, unit) —— series_id 与 registry/metrics/*.yml 完全一致。
SERIES = [
    ("labor.labor_share", "PRS85006173", "pct"),
    ("macro.fed_funds_rate", "FEDFUNDS", "pct"),
]


def _to_date(x) -> date:
    if isinstance(x, date) and not isinstance(x, datetime):
        return x
    return pd.Timestamp(x).date()


class FredAlfredConnector(Connector):
    source_id = "fred"
    connector = "ingestion.connectors.fred_alfred"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("FRED_API_KEY", "").strip()

    def _client(self):
        if not self._api_key:
            raise RuntimeError(
                "[fred_alfred] 缺 FRED_API_KEY——不写库。"
                "免费申请：https://fred.stlouisfed.org/docs/api/api_key.html，"
                "然后写入 .env 的 FRED_API_KEY。"
            )
        try:
            from fredapi import Fred
        except ImportError as e:
            raise RuntimeError(
                "[fred_alfred] 未安装 fredapi。请 `pip install fredapi`（中央 venv 统一安装）。"
            ) from e
        return Fred(api_key=self._api_key)

    def fetch(self) -> list[dict]:
        fred = self._client()
        rows: list[dict] = []

        for metric_key, series_id, unit in SERIES:
            # get_series_all_releases：返回长表 [realtime_start(=发布日), date(=观测期), value]。
            # 这是 ALFRED 的 vintage 全量——每个 (观测期, 发布版) 一行。
            df = fred.get_series_all_releases(series_id)
            if df is None or len(df) == 0:
                print(f"[fred_alfred] 提示：{series_id} 无数据返回，跳过。")
                continue
            n_before = len(rows)
            for _, r in df.iterrows():
                val = r.get("value")
                if val is None or pd.isna(val):
                    continue  # ALFRED 用 NaN/None 标记该版尚未发布该期
                valid = _to_date(r["date"])               # 观测期（经济日期）
                release = _to_date(r["realtime_start"])    # 发布日（何时得知）
                rows.append({
                    "metric_key": metric_key,
                    "source_id": "fred",
                    "value": float(val),
                    "unit": unit,
                    "valid_time": valid,
                    "knowledge_time": datetime(release.year, release.month, release.day,
                                               tzinfo=timezone.utc),
                    "vintage_date": release,  # ALFRED 口径：哪一版发布
                })
            print(f"[fred_alfred] {metric_key}({series_id}): 展开 {len(rows) - n_before} 个 vintage 行。")

        if not rows:
            raise RuntimeError("[fred_alfred] 两序列均无数据——检查 API key 与连通性。")
        return rows


if __name__ == "__main__":
    run_id = FredAlfredConnector().run()
    print(f"✓ fred_alfred（含 vintage）已写入，ingest_run_id={run_id}")
