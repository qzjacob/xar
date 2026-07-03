"""LBNL "Queued Up" 并网排队 —— 电网/并网【约束】腿（公开 XLSX，无需 key，A_official）。

直下 Lawrence Berkeley National Lab（LBNL / Berkeley Lab EMP）"Queued Up" 数据集的公开 Excel
工作簿，产出（registry sources 标 source_id=lbnl）：
  power.grid_interconnection_queue ← 全美处于【活跃】并网排队中的总装机容量（GW），按年（年末）。

背景与经济学映射（审讯纪律）：
  - LBNL 汇总 >50 家电网运营商（7 家 ISO/RTO + 49 家非 ISO 平衡区，覆盖 ~97% 全美装机）的并网
    排队数据，即"排队等待接入输电网"的拟建电厂/储能容量。排队量逐年膨胀（2014 约 400+ GW →
    2024 约 2000+ GW 级）正是 A6"能源—算力"论题里的电网/并网【约束】信号：新增发电与数据中心
    负荷都卡在并网审批与输电扩容瓶颈上。故本指标是"约束"而非"产出"。
  - 口径：取工作簿中 "07. Active Capacity by Year" 汇总页（标题"Cumulative capacity of active
    interconnection requests"）。该页每年有两行 Category（"Entered queues in earlier year"
    与 "Entered queues in year shown"），二者相加 = 当年年末仍活跃在排队中的累计总容量（GW）。
    本连接器按年求和这两类，得到年末活跃排队总量；valid_time=当年 12-31，value=总 GW，unit="GW"。
    只包含"截至年末仍剩余"的容量（同年提交又撤回的不计），与 LBNL 图表口径一致（见该页 Notes）。
  - 单一年份内若只有一类 Category 也照单求和（容错）；负值/空值行跳过。

工程现实与降级（重要）：
  - 文件为 .xlsx，链接随版本漂移（每年新版换文件名/目录，如 .../2026-05/lbnl_ix_queue_data_file_thru2025.xlsx）。
    故把 URL 设为默认参数、可用环境变量 LBNL_QUEUES_XLSX_URL 覆盖，便于将来换新版直链。
  - 本机可能【未装 openpyxl】（pandas 的 xlsx 引擎），因此本连接器【不依赖 openpyxl】：xlsx 本质是
    zip+XML，用 Python 标准库 zipfile + xml.etree 手工解析目标汇总页（sharedStrings + sheet XML）。
    这样在没有 openpyxl 的环境下仍能真实取数、真实解析，符合"尽力接真实端点"的要求。
  - 若下载失败（如 emp.lbl.gov 主站有 WAF 返回 403）、或 zip/XML 结构非预期（LBNL 改了页名/列序），
    则打印清晰一行原因并返回 []（干净 no-op，不臆造数值、不崩溃）。注意直链宿主用
    eta-publications.lbl.gov（可编程访问，非 WAF 门），而非 emp.lbl.gov 主站。

    python -m ingestion.connectors.lbnl
"""
from __future__ import annotations

import io
import os
import re
import zipfile
from datetime import date, datetime, timezone
from xml.etree import ElementTree as ET

from slx.ingestion.base import Connector

# LBNL "Queued Up" 数据工作簿直链（公开、无需 key）。默认取"through 2025"版；
# 宿主 eta-publications.lbl.gov 可编程访问（非 WAF 门）。设为默认参数/环境变量，便于将来换版。
_DEFAULT_XLSX_URL = os.environ.get(
    "LBNL_QUEUES_XLSX_URL",
    "https://eta-publications.lbl.gov/sites/default/files/2026-05/lbnl_ix_queue_data_file_thru2025.xlsx",
)

_TARGET_METRIC = "power.grid_interconnection_queue"

# OOXML 命名空间。
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# 目标汇总页名（LBNL 若改页名，用"包含关键词"容错匹配，见 _pick_sheet）。
_WANT_SHEET_KEYWORDS = ("active", "capacity", "year")


def _kw_match(name: str, *keywords: str) -> bool:
    """容错匹配：name 是否（小写）包含全部关键词。镜像 epoch_ai/fhfa 的 _col 思路。"""
    low = (name or "").lower()
    return all(k.lower() in low for k in keywords)


def _to_float(s) -> float | None:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _to_year(s) -> int | None:
    """从单元格文本里抠出 4 位年份（2000-2100），容错。"""
    m = re.search(r"(20\d{2})", str(s or ""))
    if not m:
        return None
    y = int(m.group(1))
    return y if 2000 <= y <= 2100 else None


class LbnlQueuesConnector(Connector):
    source_id = "lbnl"
    connector = "ingestion.connectors.lbnl"

    def __init__(self, url: str = _DEFAULT_XLSX_URL, session=None):
        # url 作默认参数 → Connector() 可无参实例化；便于换新版直链或注入本地缓存。
        self._url = url
        self._session = session

    # ── 下载 xlsx 原始字节（requests + 小重试；User-Agent 与项目统一）──────────────────
    def _get_bytes(self, url: str) -> bytes:
        import requests
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(
                {"User-Agent": "Silicon-Index research qzjacob@gmail.com", "Accept": "*/*"}
            )
        last = None
        for i in range(3):  # 小重试循环，镜像 epoch_ai/fhfa
            try:
                r = self._session.get(url, timeout=120)
                r.raise_for_status()
                return r.content
            except Exception as e:  # noqa: BLE001
                last = e
                import time
                time.sleep(0.8 * (i + 1))
        raise last

    # ── 手工解析 xlsx（不依赖 openpyxl）：读 sharedStrings + 目标 sheet ─────────────────
    @staticmethod
    def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
        if "xl/sharedStrings.xml" not in zf.namelist():
            return []
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        out: list[str] = []
        for si in root.iter(_NS + "si"):
            out.append("".join(t.text or "" for t in si.iter(_NS + "t")))
        return out

    @staticmethod
    def _pick_sheet_path(zf: zipfile.ZipFile) -> str | None:
        """按页名容错匹配目标汇总页，返回其 worksheet XML 的 zip 内路径。"""
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        name2rid = {s.get("name"): s.get(_RNS + "id") for s in wb.iter(_NS + "sheet")}
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rid2tgt = {rel.get("Id"): rel.get("Target") for rel in rels}
        # 优先精确关键词组合（active+capacity+year）；否则退而求其次匹配 active+capacity。
        chosen = None
        for name in name2rid:
            if _kw_match(name, *_WANT_SHEET_KEYWORDS):
                chosen = name
                break
        if chosen is None:
            for name in name2rid:
                if _kw_match(name, "active", "capacity"):
                    chosen = name
                    break
        if chosen is None:
            return None
        tgt = rid2tgt.get(name2rid[chosen])
        if not tgt:
            return None
        return tgt.lstrip("/") if tgt.startswith("/") else "xl/" + tgt

    @staticmethod
    def _cell_text(c, sst: list[str]) -> str | None:
        t = c.get("t")
        v = c.find(_NS + "v")
        if v is None:
            ist = c.find(_NS + "is")  # inline string
            if ist is not None:
                return "".join(x.text or "" for x in ist.iter(_NS + "t"))
            return None
        if t == "s":  # 共享字符串索引
            try:
                return sst[int(v.text)]
            except (ValueError, IndexError):
                return None
        return v.text

    def _parse_active_by_year(self, raw: bytes, now) -> list[dict]:
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except Exception as e:  # noqa: BLE001  非法/非 zip（xlsx）字节
            print(f"[lbnl] 提示：下载内容非有效 xlsx（{type(e).__name__}: {e}），本轮跳过、返回空。")
            return []

        sst = self._shared_strings(zf)
        sheet_path = self._pick_sheet_path(zf)
        if not sheet_path or sheet_path not in zf.namelist():
            print("[lbnl] 告警：未在工作簿中定位到 'Active Capacity by Year' 汇总页"
                  "（LBNL 可能改了页名/结构），返回空（不臆造）。")
            return []

        try:
            sh = ET.fromstring(zf.read(sheet_path))
        except Exception as e:  # noqa: BLE001
            print(f"[lbnl] 告警：目标页 XML 解析失败（{type(e).__name__}: {e}），返回空。")
            return []

        # 逐行读，定位表头（含 'Year' 与 'Capacity' 关键词的那一行），随后按列号取值。
        # 该页布局：A=Year, B=Category, C=Capacity (GW)。用表头关键词容错锁定列字母，避免硬编码。
        col_year = col_cap = None
        header_seen = False
        gw_by_year: dict[int, float] = {}

        def _col_letter(ref: str) -> str:
            m = re.match(r"([A-Z]+)\d+", ref or "")
            return m.group(1) if m else ""

        for row in sh.iter(_NS + "row"):
            cells = {}
            for c in row.iter(_NS + "c"):
                ref = c.get("r")
                if not ref:
                    continue
                cells[_col_letter(ref)] = self._cell_text(c, sst)

            if not header_seen:
                # 找表头：某列文本含 'year'，另一列文本含 'capacity'（大小写不敏感）。
                yl = next((col for col, val in cells.items()
                           if val and "year" == str(val).strip().lower()), None)
                cl = next((col for col, val in cells.items()
                           if val and "capacity" in str(val).lower()), None)
                if yl and cl:
                    col_year, col_cap = yl, cl
                    header_seen = True
                continue

            # 数据行：year 列可解析出年份、capacity 列可解析出数值 → 按年累加。
            yr = _to_year(cells.get(col_year))
            cap = _to_float(cells.get(col_cap))
            if yr is None or cap is None or cap < 0:
                continue
            gw_by_year[yr] = gw_by_year.get(yr, 0.0) + cap

        if not gw_by_year:
            print("[lbnl] 提示：定位到汇总页但未解析出任何 (年份, 容量GW) 行"
                  "（LBNL 可能改了列序/口径），返回空（不臆造）。")
            return []

        out: list[dict] = []
        for yr in sorted(gw_by_year):
            out.append({
                "metric_key": _TARGET_METRIC,
                "source_id": "lbnl",
                "value": round(gw_by_year[yr], 4),   # 年末活跃排队总容量（两类 Category 求和）
                "unit": "GW",
                "valid_time": date(yr, 12, 31),      # 年度点取年末
                "knowledge_time": now,               # 工作簿仅"最新一版"，无独立 vintage 快照
            })
        print(f"[lbnl] power.grid_interconnection_queue：{len(out)} 个年度点，"
              f"{out[0]['valid_time'].year} → {out[-1]['valid_time'].year}"
              f"（{out[0]['value']} → {out[-1]['value']} GW）。")
        return out

    def fetch(self) -> list[dict]:
        now = datetime.now(timezone.utc)

        # 网络/端点不可用（如主站 WAF 403）→ 打印清晰一行原因并返回 []（干净 no-op）。
        try:
            raw = self._get_bytes(self._url)
        except Exception as e:  # noqa: BLE001
            print(f"[lbnl] 提示：下载 Queued Up 工作簿失败（{type(e).__name__}: {e}），本轮跳过、返回空。"
                  f" 链接：{self._url}（如已发新版，设 LBNL_QUEUES_XLSX_URL 覆盖）")
            return []

        return self._parse_active_by_year(raw, now)


if __name__ == "__main__":
    run_id = LbnlQueuesConnector().run()
    print(f"✓ lbnl（并网排队约束）已写入，ingest_run_id={run_id}")
