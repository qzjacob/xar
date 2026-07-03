"""Epoch AI —— 推理经济学 / 算力 / 能力（CC-BY CSV，无需 key，B_public_curated）。

直下 Epoch 的公开 CSV（CC-BY），产出（registry sources 标 source_id=epoch_ai）：
  compute.training_scaling             ← notable_ai_models.csv：前沿训练算力 FLOP（按月取当月最大）。
  compute.gpu_geographic_distribution  ← gpu_clusters.csv：算力地理分布（美国份额 % + HHI 集中度）。
  capability.model_release_cadence     ← notable_ai_models.csv：值得关注模型发布数/季。
  cost.intelligence.inference_price_per_mtok ← 推理价格（**Epoch 未提供稳定下载 CSV** → 写抓取/解析骨架，字段假设见下）。

已对照线上真实表头（截至构建日）：
  notable_ai_models.csv 关键列：'Model','Publication date'(YYYY-MM-DD),'Domain',
      'Training compute (FLOP)'(浮点),'Country (of organization)','Frontier model'。
  gpu_clusters.csv 关键列：'Name','H100 equivalents'(浮点),'Country',
      'First Operational Date'(YYYY-MM-DD),'Status','Include in Standard Analysis'。
若 Epoch 改表头，解析以"列名包含关键词"做容错匹配（见 _col），并打印告警。

口径声明（审讯纪律）：
  - training_scaling：每月取该月发布模型的**最大** FLOP（前沿包络），非均值；缺 FLOP 的行跳过。
  - gpu_geographic_distribution：以 H100-equivalents 加权的国别份额；valid_time 取数据快照日，
    value=美国份额(%)；附带 HHI 以 value_high 区间位携带（同行另存为派生口径）。
    Epoch 仅覆盖约 10–20% 全球 GPU 能力、中国匿名打折（registry caveat）。
  - release_cadence：按发布季度计数，valid_time=季度末。

    python -m ingestion.connectors.epoch_ai
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date, datetime, timezone

from slx.ingestion.base import Connector

_NOTABLE_URL = "https://epoch.ai/data/notable_ai_models.csv"
_CLUSTERS_URL = "https://epoch.ai/data/gpu_clusters.csv"
# 推理价格：Epoch 以 data-insights 页面呈现，无稳定 CSV 直链。占位常量，便于将来替换。
_INFERENCE_PRICE_URL = None  # TODO: 接入 Epoch llm-inference-price 数据集稳定直链后填入

_US_NAMES = {"United States of America", "United States", "USA", "US"}


def _col(fieldnames: list[str], *keywords: str) -> str | None:
    """容错列名匹配：返回第一个包含全部关键词（小写）的列名。"""
    for f in fieldnames:
        low = f.lower()
        if all(k.lower() in low for k in keywords):
            return f
    return None


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _month_end(d: date) -> date:
    nm = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
    return date.fromordinal(nm.toordinal() - 1)


def _quarter_end(d: date) -> date:
    q = (d.month - 1) // 3 + 1
    return {1: date(d.year, 3, 31), 2: date(d.year, 6, 30),
            3: date(d.year, 9, 30), 4: date(d.year, 12, 31)}[q]


def _to_float(s) -> float | None:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


class EpochAiConnector(Connector):
    source_id = "epoch_ai"
    connector = "ingestion.connectors.epoch_ai"

    def __init__(self, session=None):
        self._session = session

    def _get_csv(self, url: str) -> tuple[list[str], list[dict]]:
        import requests
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": "Silicon-Index research qzjacob@gmail.com"})
        last = None
        for i in range(3):
            try:
                r = self._session.get(url, timeout=40)
                r.raise_for_status()
                rdr = csv.DictReader(io.StringIO(r.text))
                rows = list(rdr)
                return (rdr.fieldnames or []), rows
            except Exception as e:  # noqa: BLE001
                last = e
                import time
                time.sleep(0.8 * (i + 1))
        raise last

    # ── notable models → training_scaling + release_cadence ─────────────────────
    def _notable_rows(self, now) -> list[dict]:
        fields, rows = self._get_csv(_NOTABLE_URL)
        c_date = _col(fields, "publication", "date") or _col(fields, "date")
        c_flop = _col(fields, "training compute") or _col(fields, "flop")
        if not c_date:
            print("[epoch_ai] 告警：notable CSV 未找到发布日期列，跳过该源。")
            return []

        max_flop_by_month: dict[date, float] = {}
        count_by_quarter: dict[date, int] = defaultdict(int)
        for r in rows:
            d = _parse_date(r.get(c_date, ""))
            if not d:
                continue
            count_by_quarter[_quarter_end(d)] += 1
            if c_flop:
                fl = _to_float(r.get(c_flop))
                if fl and fl > 0:
                    me = _month_end(d)
                    if fl > max_flop_by_month.get(me, 0.0):
                        max_flop_by_month[me] = fl

        out: list[dict] = []
        for me, fl in sorted(max_flop_by_month.items()):
            out.append({
                "metric_key": "compute.training_scaling", "source_id": "epoch_ai",
                "value": fl, "unit": "FLOP", "valid_time": me, "knowledge_time": now,
            })
        for qe, n in sorted(count_by_quarter.items()):
            out.append({
                "metric_key": "capability.model_release_cadence", "source_id": "epoch_ai",
                "value": float(n), "unit": "count_per_quarter",
                "valid_time": qe, "knowledge_time": now,
            })
        print(f"[epoch_ai] training_scaling {len(max_flop_by_month)} 月点，"
              f"release_cadence {len(count_by_quarter)} 季点。")
        return out

    # ── gpu clusters → geographic_distribution（美国份额 % + HHI）───────────────────
    def _cluster_rows(self, now) -> list[dict]:
        fields, rows = self._get_csv(_CLUSTERS_URL)
        c_h100 = _col(fields, "h100", "equivalent")
        c_country = _col(fields, "country")
        if not (c_h100 and c_country):
            print("[epoch_ai] 告警：gpu_clusters CSV 缺 H100/Country 列，跳过地理分布。")
            return []

        by_country: dict[str, float] = defaultdict(float)
        for r in rows:
            w = _to_float(r.get(c_h100))
            country = (r.get(c_country) or "").strip()
            if w and w > 0 and country:
                by_country[country] += w
        total = sum(by_country.values())
        if total <= 0:
            print("[epoch_ai] 告警：gpu_clusters 总算力为 0，跳过。")
            return []

        us = sum(v for k, v in by_country.items() if k in _US_NAMES)
        us_share = us / total * 100.0
        hhi = sum((v / total) ** 2 for v in by_country.values())  # 0..1 集中度
        snap = now.date()  # 快照日作为 valid_time（横截面分布）
        return [{
            "metric_key": "compute.gpu_geographic_distribution", "source_id": "epoch_ai",
            "value": round(us_share, 4),        # 主值=美国份额(%)
            "value_low": round(hhi, 6),          # 区间下界位携带 HHI（派生集中度，口径见 docstring）
            "unit": "share_pct", "valid_time": snap, "knowledge_time": now,
        }]

    # ── inference price：Epoch 无稳定 CSV → 解析骨架 + 字段假设（不臆造数值）──────────
    def _inference_price_rows(self, now) -> list[dict]:
        if _INFERENCE_PRICE_URL is None:
            print("[epoch_ai] 提示：cost.intelligence.inference_price_per_mtok 暂无 Epoch 稳定 CSV 直链，"
                  "本轮跳过。接入后预期字段假设：date(YYYY-MM-DD)、capability_quantile（前沿能力分位）、"
                  "price_usd_per_mtok（美元/百万 token）；映射 valid_time=date, value=price, unit=USD_per_Mtok。")
            return []
        # 接入后的解析骨架（字段名以届时 CSV 为准，用 _col 容错匹配）：
        fields, rows = self._get_csv(_INFERENCE_PRICE_URL)
        c_date = _col(fields, "date")
        c_price = _col(fields, "price") or _col(fields, "usd")
        out: list[dict] = []
        for r in rows:
            d = _parse_date(r.get(c_date, "")) if c_date else None
            p = _to_float(r.get(c_price)) if c_price else None
            if d and p is not None:
                out.append({
                    "metric_key": "cost.intelligence.inference_price_per_mtok",
                    "source_id": "epoch_ai", "value": p, "unit": "USD_per_Mtok",
                    "valid_time": d, "knowledge_time": now,
                })
        return out

    def fetch(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        rows: list[dict] = []
        rows += self._notable_rows(now)
        rows += self._cluster_rows(now)
        rows += self._inference_price_rows(now)
        if not rows:
            raise RuntimeError("[epoch_ai] 无任何源产出——检查到 epoch.ai 的连通性。")
        return rows


if __name__ == "__main__":
    run_id = EpochAiConnector().run()
    print(f"✓ epoch_ai 已写入，ingest_run_id={run_id}")
