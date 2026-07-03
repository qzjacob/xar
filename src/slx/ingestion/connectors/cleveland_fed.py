"""Cleveland Fed —— 第 10 百分位纯低技能实际时薪（KEYLESS，best-effort，诚实数据缺口骨架）。

映射（registry sources 标 source_id=cleveland_fed, series_id=wage_growth_by_decile, 季度）：
  labor.bottom_decile_real_wage ← 第 10 百分位（bottom decile）实际时薪水平（unit=usd_per_hour_real）。

**已知数据缺口（开发计划 §7 诚实登记，绝不用相关冒充）**：
  Cleveland Fed 的分位工资增长（wage growth by decile / percentile）主要发表在**研究报告 / 博客**里
  （如 Wage Growth Tracker 的分位切片、economic-commentary PDF），**没有稳定的机器可读“分位工资”序列**。
  Cleveland Fed 确实有可下载数据产品（Median CPI / 通胀 Nowcast / SMI 等经 www.clevelandfed.org 的
  JSON/CSV 端点或 xlsx 直链发布），但**没有一条稳定直链暴露“第 10 百分位实际时薪”水平**。
  故本连接器做**诚实脚手架**：尝试可识别的真实 Cleveland Fed 数据端点，若拿不到稳定的
  bottom-decile 实际工资序列，则打印一行“已知数据缺口”原因并优雅降级为 []（干净 no-op），
  **不臆造任何数值**（与 epoch_ai 的 inference-price 骨架同构）。价值在于诚实脚手架 + 让 Dagster 资产可解析。

口径声明（审讯纪律，接真数据时按此对齐）：
  - value = 第 10 百分位（bottom decile, p10）实际时薪**水平**，单位 usd_per_hour_real（实际美元/小时）。
    注意 registry series 名为 wage_growth_by_decile（“增长”），但本指标 metric 是**实际时薪水平**；
    若届时源仅给“增长率”，需另配基期水平换算为水平序列，切勿把“增长率”直接当“时薪”写库。
  - valid_time = 季度末（ingest_cadence=quarterly；与 epoch_ai/fhfa 的季度点取季末一致）。
  - vintage_aware=false：Cleveland Fed 分位口径无稳定 vintage 快照 → knowledge_time=本次摄取时刻、
    vintage_date=哨兵。需 as-of 复刻历史修订者，应另接 vintage 源。
  - caveat（registry 同步）：仅研究报告无原始 API；2025Q2→Q3 的疫情外首次下降是**单点**，勿过度外推。

  python -m ingestion.connectors.cleveland_fed
"""
from __future__ import annotations

import csv
import io
import os
from datetime import date, datetime, timezone

from slx.ingestion.base import Connector

_TARGET_METRIC = "labor.bottom_decile_real_wage"

# Cleveland Fed 数据产品根域（其 indicators-and-data 下有 JSON/CSV/xlsx 可下载产品）。
# 说明：以下为“可识别的真实数据端点根”作为**尝试起点**，非承诺存在“分位工资”稳定直链——
# 若届时确认某产品含 p10 实际时薪水平，把该稳定直链填入 CLEVELAND_FED_WAGE_URL（环境变量优先）。
# 留默认 None → 直接走“已知数据缺口”降级路径，不做无谓网络请求、也不臆造。
_DATA_BASE = "https://www.clevelandfed.org"
# 环境变量覆盖：便于将来接入确认的稳定直链而不改代码（Connector() 仍可无参实例化）。
_WAGE_URL = os.environ.get("CLEVELAND_FED_WAGE_URL", "").strip() or None

# 容错匹配用关键词：一旦真链到手，用这些关键词在表头里定位“分位/百分位”与“实际时薪”列。
_DECILE_HINTS = ("decile", "percentile", "p10", "10th", "bottom", "lowest")
_WAGE_HINTS = ("wage", "hourly", "earnings")
_REAL_HINTS = ("real", "inflation-adjusted", "constant")


def _col(fieldnames: list[str], *keywords: str) -> str | None:
    """容错列名匹配：返回第一个包含全部关键词（小写）的列名（镜像 epoch_ai._col）。"""
    for f in fieldnames:
        low = f.lower()
        if all(k.lower() in low for k in keywords):
            return f
    return None


def _col_any(fieldnames: list[str], keywords) -> str | None:
    """返回第一个包含**任一**关键词的列名（用于“分位/实际”这类多同义词场景）。"""
    for f in fieldnames:
        low = f.lower()
        if any(k.lower() in low for k in keywords):
            return f
    return None


def _to_float(s) -> float | None:
    try:
        return float(str(s).replace(",", "").replace("$", "").strip())
    except (ValueError, AttributeError):
        return None


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y", "%m/%d/%Y", "%Y Q%q"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _quarter_end(d: date) -> date:
    q = (d.month - 1) // 3 + 1
    return {1: date(d.year, 3, 31), 2: date(d.year, 6, 30),
            3: date(d.year, 9, 30), 4: date(d.year, 12, 31)}[q]


class ClevelandFedConnector(Connector):
    source_id = "cleveland_fed"
    connector = "ingestion.connectors.cleveland_fed"

    def __init__(self, url: str | None = None, session=None):
        # url 作默认参数（默认从环境变量读，缺省 None）→ Connector() 可无参实例化。
        self._url = url if url is not None else _WAGE_URL
        self._session = session

    def _get_csv(self, url: str) -> tuple[list[str], list[dict]]:
        """下载并解析 CSV（小重试循环 + 统一 UA，镜像 epoch_ai/fhfa）。"""
        import requests
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(
                {"User-Agent": "Silicon-Index research qzjacob@gmail.com"}
            )
        last = None
        for i in range(3):  # 小重试循环
            try:
                r = self._session.get(url, timeout=45)
                r.raise_for_status()
                rdr = csv.DictReader(io.StringIO(r.text))
                rows = list(rdr)
                return (rdr.fieldnames or []), rows
            except Exception as e:  # noqa: BLE001
                last = e
                import time
                time.sleep(0.8 * (i + 1))
        raise last

    def fetch(self) -> list[dict]:
        now = datetime.now(timezone.utc)

        # ── 诚实数据缺口路径 ─────────────────────────────────────────────────────────
        # 无确认的稳定“分位实际时薪”直链 → 打印一行清晰原因（点名已知缺口），返回 []（不臆造、不崩）。
        # 与 epoch_ai 的 inference-price 骨架同构：写清“接入后预期字段假设 + 映射”，便于将来替换。
        if not self._url:
            print(
                "[cleveland_fed] 已知数据缺口：Cleveland Fed 的分位工资（wage growth by decile）"
                "主要见于研究报告/博客，无稳定机器可读的“第 10 百分位实际时薪”序列"
                f"（开发计划 §7 已登记；数据产品根 {_DATA_BASE}/indicators-and-data）。本轮跳过、返回空，绝不臆造。"
                " 接入后预期字段假设：date（季度）、decile/percentile（分位，取 p10/bottom）、"
                "real_wage_usd_per_hour（实际美元/小时）；映射 valid_time=季度末, "
                f"value=p10 实际时薪, unit=usd_per_hour_real, metric_key={_TARGET_METRIC}。"
                " 若已确认稳定直链：设 CLEVELAND_FED_WAGE_URL 或传 url= 后自动走下方解析骨架。"
            )
            return []

        # ── 接入后的真实解析骨架（字段名以届时 CSV 为准，用 _col/_col_any 容错匹配）─────────
        # 网络/数据不可用 → 打印清晰一行原因并返回 []（干净 no-op）。
        try:
            fields, rows = self._get_csv(self._url)
        except Exception as e:  # noqa: BLE001
            print(f"[cleveland_fed] 提示：下载分位工资数据失败（{type(e).__name__}: {e}），"
                  f"本轮跳过、返回空。链接：{self._url}")
            return []

        c_date = _col(fields, "date") or _col(fields, "quarter") or _col(fields, "period")
        # 分位列：优先“10th/p10/bottom/lowest”等；找不到明确 p10 则视为无稳定分位口径 → 降级。
        c_decile = _col(fields, "10") or _col_any(fields, ("p10", "bottom", "lowest"))
        # 实际时薪值列：需同时像“工资/时薪”且像“实际”；宽松兜底取任一工资列。
        c_value = (_col(fields, "real", "wage") or _col(fields, "real", "hourly")
                   or _col_any(fields, _WAGE_HINTS))

        if not (c_date and c_value):
            print(f"[cleveland_fed] 告警：数据表缺关键列（date/real-wage），实得表头={fields}；"
                  f"本轮跳过、返回空（不臆造）。")
            return []
        if not c_decile:
            # 没有明确的“第 10 百分位”口径 → 不能把总体/中位工资冒充 bottom decile。诚实降级。
            print(f"[cleveland_fed] 已知数据缺口：数据表无明确“第 10 百分位/bottom decile”列"
                  f"（实得表头={fields}），拒绝以中位/总体工资冒充分位口径，返回空、不臆造。")
            return []

        out: list[dict] = []
        for r in rows:
            d = _parse_date(r.get(c_date, ""))
            if not d:
                continue
            # 若分位以“行值”而非“列切分”呈现（如某列=分位标签），过滤到 bottom-decile 行。
            dec_cell = (r.get(c_decile) or "").strip().lower() if c_decile in r else ""
            if dec_cell and not any(h in dec_cell for h in ("10", "bottom", "lowest", "p10")):
                continue
            val = _to_float(r.get(c_value))
            if val is None or val <= 0:
                continue
            out.append({
                "metric_key": _TARGET_METRIC,
                "source_id": "cleveland_fed",
                "value": val,                        # p10 实际时薪水平（usd_per_hour_real）
                "unit": "usd_per_hour_real",
                "valid_time": _quarter_end(d),       # 季度点取季末
                "knowledge_time": now,               # 无稳定 vintage 快照
            })

        if not out:
            print("[cleveland_fed] 提示：解析到 0 条 p10 实际时薪点（源结构或与假设不符），返回空、不臆造。")
            return []

        print(f"[cleveland_fed] {_TARGET_METRIC}（p10 实际时薪）："
              f"{len(out)} 个季度点，{out[0]['valid_time']} → {out[-1]['valid_time']}。")
        return out


if __name__ == "__main__":
    run_id = ClevelandFedConnector().run()
    print(f"✓ cleveland_fed（第 10 百分位实际时薪；诚实缺口脚手架）已写入，ingest_run_id={run_id}")
