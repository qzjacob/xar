"""EIA / Ember / IEA —— 能源—算力瓶颈（原子相，A_official）。

产出（registry sources：power.datacenter_twh 有 eia/ember/iea 三源；power.ai_power_constraint 有 eia）：
  power.datacenter_twh      ← EIA 全国发电/用电量（数据中心专项不开源，此处用宏观电量代理）
                              + Ember 月度电力需求（结构补充）。
  power.ai_power_constraint ← EIA 工业电价派生的"约束度"代理。

口径与近似声明（审讯纪律）：
  - EIA 无"数据中心专项耗电"公开序列（设施级私有）。registry caveat 已明示须
    "IEA 宏观 + Ember 结构 + Epoch 地理"融合。本连接器只搬运可得的宏观电量/电价，
    数据中心占比的拆分属下游派生，不在此臆造。
  - ai_power_constraint 定义为派生合成（电价 × 容量缺口）。本连接器仅取**工业电价**作
    可得分量，并显式以 unit=index 标注为代理；容量缺口分量需 LBNL 队列（另连接器），
    在其接入前 constraint 仅反映电价信号。权重透明、可审计。

需 EIA_API_KEY / EMBER_API_KEY（免费）。缺 key 时**跳过该源并打印提示**，不报致命错误
（多源容错：有一个源可得即产出该源的行）。绝不硬编码 key。

EIA v2 端点契约（已对照官方文档）：
  GET https://api.eia.gov/v2/electricity/retail-sales/data/
      ?api_key=...&frequency=monthly&data[0]=price
      &facets[sectorid][]=IND&facets[stateid][]=US&start=YYYY-MM&sort[0][column]=period&sort[0][direction]=desc
  返回 {"response":{"data":[{"period":"2026-03","price":<cents/kWh>,...}]}}

    python -m ingestion.connectors.iea_eia_ember
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone

from slx.ingestion.base import Connector

_EIA_BASE = "https://api.eia.gov/v2"
_EMBER_BASE = "https://api.ember-energy.org"  # Ember 公开 API 主机（按其文档）


def _period_to_date(p: str) -> date:
    """EIA period 'YYYY-MM' / 'YYYY' / 'YYYY-MM-DD' → 该期最后一天（粗到月取月末近似）。"""
    parts = p.split("-")
    if len(parts) == 1:
        return date(int(parts[0]), 12, 31)
    if len(parts) == 2:
        y, m = int(parts[0]), int(parts[1])
        # 月末：下月一号减一天
        nm = date(y + (m == 12), (m % 12) + 1, 1)
        return date.fromordinal(nm.toordinal() - 1)
    return date(int(parts[0]), int(parts[1]), int(parts[2]))


class IeaEiaEmberConnector(Connector):
    source_id = "eia"  # 主源；Ember/IEA 行各自带 source_id
    connector = "ingestion.connectors.iea_eia_ember"
    covers_sources = ("iea", "ember")  # 一次 run 同时填充 iea / ember 源行

    def __init__(self, eia_key: str | None = None, ember_key: str | None = None,
                 start: str = "2022-01"):
        self._eia_key = eia_key or os.environ.get("EIA_API_KEY", "").strip()
        self._ember_key = ember_key or os.environ.get("EMBER_API_KEY", "").strip()
        self._start = start

    def _get(self, url: str, params: dict, *, retries: int = 3, timeout: int = 30):
        import requests
        last = None
        for i in range(retries):
            try:
                r = requests.get(url, params=params, timeout=timeout,
                                 headers={"User-Agent": "Silicon-Index research"})
                r.raise_for_status()
                return r.json()
            except Exception as e:  # noqa: BLE001
                last = e
                import time
                time.sleep(0.8 * (i + 1))
        raise last

    # ── EIA：工业电价（→ ai_power_constraint 代理）+ 全国用电量（→ datacenter_twh 代理）──
    def _eia_rows(self, now) -> list[dict]:
        if not self._eia_key:
            print("[iea_eia_ember] 提示：缺 EIA_API_KEY，跳过 EIA 源。"
                  "免费申请：https://www.eia.gov/opendata/register.php")
            return []
        rows: list[dict] = []

        # (1) 工业零售电价（美分/kWh）→ ai_power_constraint 的电价分量（index 代理）
        j = self._get(f"{_EIA_BASE}/electricity/retail-sales/data/", {
            "api_key": self._eia_key, "frequency": "monthly", "data[0]": "price",
            "facets[sectorid][]": "IND", "facets[stateid][]": "US",
            "start": self._start, "sort[0][column]": "period", "sort[0][direction]": "desc",
            "length": 5000,
        })
        for d in j.get("response", {}).get("data", []):
            price = d.get("price")
            if price is None:
                continue
            vt = _period_to_date(d["period"])
            rows.append({
                "metric_key": "power.ai_power_constraint",
                "source_id": "eia",
                "value": float(price),  # 美分/kWh，作约束度代理（unit=index 标注口径为代理）
                "unit": "index",
                "valid_time": vt,
                "knowledge_time": now,
            })

        # (2) 全国总发电量（TWh）→ datacenter_twh 的宏观代理（非数据中心专项；caveat 已声明）
        try:
            j2 = self._get(f"{_EIA_BASE}/electricity/electric-power-operational-data/data/", {
                "api_key": self._eia_key, "frequency": "monthly", "data[0]": "generation",
                "facets[fueltypeid][]": "ALL", "facets[location][]": "US",
                "start": self._start, "sort[0][column]": "period", "sort[0][direction]": "desc",
                "length": 5000,
            })
            for d in j2.get("response", {}).get("data", []):
                gen = d.get("generation")
                if gen is None:
                    continue
                # EIA generation 单位通常为 thousand MWh = GWh；/1000 → TWh
                vt = _period_to_date(d["period"])
                rows.append({
                    "metric_key": "power.datacenter_twh",
                    "source_id": "eia",
                    "value": round(float(gen) / 1000.0, 4),
                    "unit": "TWh",  # 注：全国总发电代理，非数据中心专项
                    "valid_time": vt,
                    "knowledge_time": now,
                })
        except Exception as e:  # noqa: BLE001
            print(f"[iea_eia_ember] 提示：EIA 发电量序列取数失败（{e}），仅产出电价行。")

        return rows

    # ── Ember：月度电力需求（→ datacenter_twh 结构补充）─────────────────────────
    def _ember_rows(self, now) -> list[dict]:
        if not self._ember_key:
            print("[iea_eia_ember] 提示：缺 EMBER_API_KEY，跳过 Ember 源。"
                  "免费申请：https://ember-energy.org/data/")
            return []
        rows: list[dict] = []
        try:
            # Ember electricity-demand 月度（US）。字段以官方文档为准；若 schema 变更此处需校正。
            j = self._get(f"{_EMBER_BASE}/v1/electricity-demand/monthly", {
                "entity_code": "USA", "start_date": self._start, "api_key": self._ember_key,
            })
            data = j.get("data", j) if isinstance(j, dict) else j
            for d in (data or []):
                val = d.get("demand_twh") or d.get("value")
                period = d.get("date") or d.get("period")
                if val is None or not period:
                    continue
                rows.append({
                    "metric_key": "power.datacenter_twh",
                    "source_id": "ember",
                    "value": float(val),
                    "unit": "TWh",  # 全社会电力需求代理（结构补充，非数据中心专项）
                    "valid_time": _period_to_date(str(period)[:7]),
                    "knowledge_time": now,
                })
        except Exception as e:  # noqa: BLE001
            print(f"[iea_eia_ember] 提示：Ember 取数失败（{e}）；字段假设可能需按最新文档校正，跳过。")
        return rows

    def fetch(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        rows = self._eia_rows(now) + self._ember_rows(now)
        if not rows:
            raise RuntimeError(
                "[iea_eia_ember] 无任何源产出——EIA_API_KEY 与 EMBER_API_KEY 均缺失或不可达。"
                "至少配置 EIA_API_KEY 后重试。"
            )
        return rows


if __name__ == "__main__":
    run_id = IeaEiaEmberConnector().run()
    print(f"✓ iea_eia_ember 已写入，ingest_run_id={run_id}")
