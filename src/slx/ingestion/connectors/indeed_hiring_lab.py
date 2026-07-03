"""Indeed Hiring Lab —— 招聘岗位指数（软件开发板块，作为"高 AI 暴露初级岗位"的原始输入代理，公开 GitHub CSV，无需 key，B_public_curated）。

直下 Indeed Hiring Lab 公开发布的 Job Postings Tracker 明细 CSV
（raw.githubusercontent.com/hiring-lab/job_postings_tracker/…），产出（registry sources 标 source_id=indeed_hiring_lab）：
  labor.junior_postings_high_vs_low_ai_exposure ← job_postings_by_sector_US.csv 中
      display_name="Software Development" 的岗位指数（基期 100 = 2020-02，即 Feb 2020）。

已对照线上真实表头（截至构建日 2026-07）：
  job_postings_by_sector_US.csv 列：'date'(YYYY-MM-DD 日频),'jobcountry'(US),
      'indeed_job_postings_index'(浮点，基期100=2020-02),'variable'('new postings'/'total postings'),
      'display_name'(板块名，含 'Software Development' 等 40 个板块)。
  取 variable='total postings'（存量水平指数，而非当日新增）；软件开发板块作为"高 AI 暴露"技术岗代理。
若 Indeed 改表头/换仓路径，解析以"列名包含关键词"做容错匹配（见 _col），并打印告警、降级为 [] 不臆造。

口径声明（审讯纪律）：
  - **重要**：原始数据并【无】现成的"high vs low AI exposure（高/低 AI 暴露）"分类。本连接器只提供
    **原始岗位指数**（软件开发板块 = 高暴露侧的代理 INPUT）；真正的"暴露度映射 + 控制期 DID 面板
    （high-exposure 处理组 vs low-exposure 对照组、事件研究/双重差分识别）"在 engine/identification
    层单独完成。故此单源写入的是**分子侧的原始技术岗位指数**，尚非最终的"高vs低"相对量。
  - 频率：源为**日频**；本连接器按月折叠为月度点——取每个自然月内**最后一个有观测的交易日**的指数值，
    valid_time=该月月末（与 epoch_ai / fhfa 的月末口径一致）。value=指数点位，unit=index。
  - variable：锁定 'total postings'（岗位存量指数，做趋势/DID 更稳），非 'new postings'（当日新增）。
  - 板块选择：display_name='Software Development'（软件开发）作为高 AI 暴露技术岗代理；若该板块缺失，
    尝试匹配含 'software'/'develop' 的板块名兜底，仍无则降级为 []（不臆造、不改用别的板块顶替）。
  - 双时态：Indeed 明细 CSV 只提供"最新一版"日频序列，历史会随修订覆盖、无 vintage 快照；
    因此 knowledge_time=本次摄取时刻、vintage_date=哨兵（无独立 vintage）。
  - 仓路径会漂移（Indeed 时不时重组 job_postings_tracker 目录）：故把 CSV 直链设为默认参数
    （可经环境变量 INDEED_HIRING_LAB_CSV_URL 覆盖），不可达时打印清晰一行原因并返回 []。

    python -m ingestion.connectors.indeed_hiring_lab
"""
from __future__ import annotations

import csv
import io
import os
from datetime import date, datetime, timezone

from slx.ingestion.base import Connector

# Indeed Hiring Lab 岗位跟踪器（板块级明细，公开 GitHub raw CSV，无需 key）。
# 设为默认参数 + 环境变量覆盖，便于将来换仓/换链（Indeed 会重组目录，路径易漂移）。
_DEFAULT_CSV_URL = (
    "https://raw.githubusercontent.com/hiring-lab/job_postings_tracker/"
    "master/US/job_postings_by_sector_US.csv"
)

_TARGET_METRIC = "labor.junior_postings_high_vs_low_ai_exposure"

# 高 AI 暴露技术岗代理板块（display_name 精确名 + 关键词兜底）。
_WANT_SECTOR = "software development"
_SECTOR_KEYWORDS = ("software", "develop")  # 兜底：含这些关键词的板块名

# 岗位存量水平指数（而非当日新增）。
_WANT_VARIABLE = "total postings"


def _col(fieldnames: list[str], *keywords: str) -> str | None:
    """容错列名匹配：返回第一个包含全部关键词（小写）的列名。"""
    for f in fieldnames:
        low = f.lower()
        if all(k.lower() in low for k in keywords):
            return f
    return None


def _to_float(s) -> float | None:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _month_end(year: int, month: int) -> date:
    """给定年月 → 该月月末日期（月度点取月末，口径与 epoch_ai / fhfa 一致）。"""
    nm = date(year + (month == 12), (month % 12) + 1, 1)
    return date.fromordinal(nm.toordinal() - 1)


class IndeedHiringLabConnector(Connector):
    source_id = "indeed_hiring_lab"
    connector = "ingestion.connectors.indeed_hiring_lab"

    def __init__(self, url: str | None = None, session=None):
        # url 作默认参数（+ 环境变量覆盖）→ Connector() 可无参实例化；便于将来替换直链/注入本地缓存。
        self._url = url or os.environ.get("INDEED_HIRING_LAB_CSV_URL", _DEFAULT_CSV_URL)
        self._session = session

    def _get_csv(self, url: str) -> tuple[list[str], list[dict]]:
        import requests
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": "Silicon-Index research qzjacob@gmail.com"})
        last = None
        for i in range(3):  # 小重试循环，镜像 epoch_ai / fhfa
            try:
                r = self._session.get(url, timeout=60)
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

        # 网络/数据不可用 → 打印清晰一行原因并返回 []（干净 no-op，不臆造、不崩）。
        try:
            fields, rows = self._get_csv(self._url)
        except Exception as e:  # noqa: BLE001
            print(f"[indeed_hiring_lab] 提示：下载岗位板块 CSV 失败（{type(e).__name__}: {e}），"
                  f"本轮跳过、返回空。链接：{self._url}")
            return []

        # 容错定位关键列；Indeed 若改表头则降级（不臆造）。
        c_date = _col(fields, "date")
        c_index = _col(fields, "index") or _col(fields, "postings", "index")
        c_var = _col(fields, "variable")
        c_sector = _col(fields, "display", "name") or _col(fields, "sector")
        if not (c_date and c_index and c_sector):
            print(f"[indeed_hiring_lab] 告警：CSV 缺关键列（date/index/display_name），"
                  f"实得表头={fields}；本轮跳过、返回空（不臆造）。")
            return []

        # 先确认目标板块是否存在（精确名优先，关键词兜底），否则降级——不改用别的板块顶替。
        all_sectors = {(r.get(c_sector) or "").strip() for r in rows}
        target_sector = None
        for s in all_sectors:
            if s.lower() == _WANT_SECTOR:
                target_sector = s
                break
        if target_sector is None:  # 兜底：关键词匹配
            for s in all_sectors:
                low = s.lower()
                if all(k in low for k in _SECTOR_KEYWORDS):
                    target_sector = s
                    break
        if target_sector is None:
            print(f"[indeed_hiring_lab] 提示：未找到 'Software Development' 板块（现有板块={sorted(all_sectors)}），"
                  f"返回空（不臆造、不以他板块顶替）。")
            return []

        # 折叠到月度：每月取"最后一个有观测日期"的指数值（valid_time=该月月末）。
        # 键=(年,月) → (最新日期, 指数)；遍历中以更晚日期覆盖。
        monthly: dict[tuple[int, int], tuple[date, float]] = {}
        for r in rows:
            if (r.get(c_sector) or "").strip() != target_sector:
                continue
            # variable 存在时锁定 'total postings'；若无该列则不过滤（容错）。
            if c_var and (r.get(c_var) or "").strip().lower() != _WANT_VARIABLE:
                continue
            d = _parse_date(r.get(c_date, ""))
            v = _to_float(r.get(c_index))
            if d is None or v is None or v <= 0:
                continue
            key = (d.year, d.month)
            prev = monthly.get(key)
            if prev is None or d > prev[0]:
                monthly[key] = (d, v)

        if not monthly:
            print("[indeed_hiring_lab] 提示：目标板块无可用月度观测（可能 Indeed 改了 variable/口径），返回空。")
            return []

        out: list[dict] = []
        for (yr, mo), (_d, v) in sorted(monthly.items()):
            out.append({
                "metric_key": _TARGET_METRIC,
                "source_id": "indeed_hiring_lab",
                "value": v,                          # 软件开发板块岗位指数（基期100=Feb 2020）
                "unit": "index",                     # 无量纲指数（base 100 = 2020-02）
                "valid_time": _month_end(yr, mo),    # 月度点取月末
                "knowledge_time": now,               # 明细 CSV 仅"最新一版"，无 vintage 快照
            })

        print(f"[indeed_hiring_lab] {_TARGET_METRIC}（软件开发板块原始岗位指数，尚未做 high/low 暴露映射）："
              f"板块='{target_sector}'，{len(out)} 个月度点，"
              f"{out[0]['valid_time']} → {out[-1]['valid_time']}。")
        return out


if __name__ == "__main__":
    run_id = IndeedHiringLabConnector().run()
    print(f"✓ indeed_hiring_lab（软件开发岗位指数，高AI暴露侧原始输入）已写入，ingest_run_id={run_id}")
