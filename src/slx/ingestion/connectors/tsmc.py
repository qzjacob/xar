"""TSMC 月度净营收 —— 半导体"咽喉吞吐"代理（公开 HTML，无需 key，A_official）。

台积电（TSMC）在其投资人关系页公开每月**净营收**（新台币 NT$ 千元），本连接器尽力解析
该月度营收表，换算为美元后写入（registry sources 标 source_id=tsmc）：
  supply.semiconductor_chokepoint ← TSMC 月度净营收（series_id=monthly_revenue，月度）。

为何是咽喉代理（口径 / 理论锚 A6）：
  先进芯片必经 ASML（EUV 唯一供应商）→ 台积电（主导 2/3nm 先进制程）→ 英伟达 的窄通道。
  台积电以其**领先制程份额**成为算力供给物理上界的单点咽喉；其月度营收吞吐即该咽喉的高频代理。
  （registry caveat：月营收含**非 AI 需求**，且单颗芯片成本/良率为私有数据、不可得。）

来源与直链：
  https://investor.tsmc.com/english/monthly-revenue  （投资人关系"Monthly Revenue"页，NT$ 千元）
  该页由 TSMC 公开发布；HTML 由前端渲染、且常置于 Cloudflare 之后，结构会漂移。
  本连接器以 stdlib（re + html.parser，venv 无 bs4/lxml）**防御式**解析月度营收表，
  任一环节（网络 / 挑战页 / 表结构 / 数值）异常即打印清晰一行原因、降级为 []（不臆造、不崩）。

口径声明（审讯纪律）：
  - 单位换算 ≠ 臆造数据：源值为 NT$（千元）。本连接器用一个**清楚标注的固定 FX 常量**
    (_NTD_PER_USD) 把 NT$ 折成 USD，仅作单位换算的近似（TSMC 未在该表逐月给 USD 值）。
    该常量是"近似的口径约定"，不是伪造的数值序列——真实序列全部来自 TSMC 公布的 NT$ 营收本身。
    可用环境变量 SILICON_TSMC_NTD_PER_USD 覆盖为更贴近某期的汇率；换算口径务必与下游一致。
  - value 单位固定 unit="USD"（与 registry metric.unit 对齐）。源表 NT$ 千元 → 元：×1000；
    再 ÷ _NTD_PER_USD 得 USD。
  - valid_time = 该营收所属**月份的月末日期**（月度点取月末，口径与 epoch_ai/fhfa 一致）。
  - 双时态：该页仅呈现"最新一版"月营收，不暴露 vintage 快照；故 knowledge_time=本次摄取时刻、
    vintage_date=哨兵（无独立 vintage）。TSMC 极少修订已公布月营收，修订风险低。
  - 解析容错：以"年 + 月 + 营收数值"三元组为最小单元；表头/列序漂移时靠数值形态与年月识别兜底。
    识别不到任何 (年,月,营收) 三元组即返回 []（可能是被挑战页拦截或前端改版），并打印原因。

    python -m ingestion.connectors.tsmc
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone
from html.parser import HTMLParser

from slx.ingestion.base import Connector

# TSMC 投资人关系"Monthly Revenue"页（公开 HTML，NT$ 千元）。设为默认参数便于换链/注入本地缓存。
_MONTHLY_REVENUE_URL = os.environ.get(
    "SILICON_TSMC_MONTHLY_REVENUE_URL",
    "https://investor.tsmc.com/english/monthly-revenue",
)

_TARGET_METRIC = "supply.semiconductor_chokepoint"
_SERIES_ID = "monthly_revenue"  # 与 registry sources 声明一致（仅注释用途）

# ── 固定 FX 常量（NT$ / USD）——清楚标注为近似的单位换算约定，不是伪造的数值序列 ──────────────
# 台币兑美元长期在 ~28–33 区间波动；此处取 ~32 作为口径约定的中值近似。可用环境变量覆盖。
# 注意：这是把源表 NT$ 折算 USD 的**单位换算**，真实营收序列全部来自 TSMC 公布的 NT$ 值本身。
_NTD_PER_USD = float(os.environ.get("SILICON_TSMC_NTD_PER_USD", "32.0"))

# 源表以 NT$ 千元（thousands）计。→ 元：×1000；→ USD：÷ _NTD_PER_USD。
_NTD_THOUSANDS_TO_USD = 1000.0 / _NTD_PER_USD

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}


def _col(fieldnames: list[str], *keywords: str) -> str | None:
    """容错列名匹配（镜像 epoch_ai._col）：返回第一个包含全部关键词（小写）的列名。"""
    for f in fieldnames:
        low = f.lower()
        if all(k.lower() in low for k in keywords):
            return f
    return None


def _to_float(s) -> float | None:
    """把 '1,234,567'、'$123.4'、全角逗号等清洗为 float；失败返回 None。"""
    if s is None:
        return None
    txt = str(s).replace(",", "").replace("，", "").replace("$", "").replace("NT", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", txt)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _month_end(year: int, month: int) -> date:
    """给定年月 → 该月月末日期（valid_time 口径与 epoch_ai/fhfa 一致：月度点取月末）。"""
    nm = date(year + (month == 12), (month % 12) + 1, 1)
    return date.fromordinal(nm.toordinal() - 1)


def _parse_month_token(tok: str) -> int | None:
    """把 '2024/05'、'2024-05'、'May 2024'、'May'、'05' 等中的月份解析为 1..12。"""
    t = (tok or "").strip().lower()
    if not t:
        return None
    # 数字型 月份（可能夹在 年/月 里，取 1..12 的那段）
    for m in re.finditer(r"\d{1,2}", t):
        v = int(m.group(0))
        if 1 <= v <= 12 and not (len(m.group(0)) == 4):
            # 优先英文月名（下方处理）；数字仅作兜底，避免把"年份中的位数"误当月份。
            pass
    # 英文月名
    for name, num in _MONTHS.items():
        if name in t:
            return num
    # 纯数字兜底（如 '05' 或 '2024/05' 的末段）
    nums = re.findall(r"\d{1,2}", t)
    for n in nums:
        v = int(n)
        if 1 <= v <= 12:
            return v
    return None


def _parse_year_token(tok: str) -> int | None:
    m = re.search(r"(19|20)\d{2}", tok or "")
    return int(m.group(0)) if m else None


class _TableParser(HTMLParser):
    """stdlib HTML 表格提取器：把每个 <table> 拆成二维单元格文本矩阵（venv 无 bs4/lxml）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._cur_table: list[list[str]] | None = None
        self._cur_row: list[str] | None = None
        self._cur_cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._cur_table = []
        elif tag == "tr" and self._cur_table is not None:
            self._cur_row = []
        elif tag in ("td", "th") and self._cur_row is not None:
            self._cur_cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cur_cell is not None:
            self._cur_row.append(" ".join("".join(self._cur_cell).split()))
            self._cur_cell = None
        elif tag == "tr" and self._cur_row is not None:
            if self._cur_row:
                self._cur_table.append(self._cur_row)
            self._cur_row = None
        elif tag == "table" and self._cur_table is not None:
            if self._cur_table:
                self.tables.append(self._cur_table)
            self._cur_table = None

    def handle_data(self, data):
        if self._cur_cell is not None:
            self._cur_cell.append(data)


class TsmcConnector(Connector):
    source_id = "tsmc"
    connector = "ingestion.connectors.tsmc"

    def __init__(self, url: str = _MONTHLY_REVENUE_URL, session=None):
        # url 作默认参数 → Connector() 可无参实例化；便于将来替换直链或注入本地缓存 HTML。
        self._url = url
        self._session = session

    def _get_html(self, url: str) -> str:
        import requests
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": "Silicon-Index research qzjacob@gmail.com"})
        last = None
        for i in range(3):  # 小重试循环，镜像 epoch_ai/fhfa
            try:
                r = self._session.get(url, timeout=40)
                r.raise_for_status()
                return r.text
            except Exception as e:  # noqa: BLE001
                last = e
                import time
                time.sleep(0.8 * (i + 1))
        raise last

    def _rows_from_table(self, table: list[list[str]], now) -> list[dict]:
        """从单个表格矩阵尽力抽取 (年, 月, 营收NT$千元) → 观测行。识别不到则空。"""
        if not table:
            return []
        header = table[0]
        c_year = _col(header, "year")
        c_month = _col(header, "month")
        # 营收列：优先"含 revenue"的列；否则回退最后一个"看起来是数值"的列。
        c_rev = _col(header, "net", "revenue") or _col(header, "revenue")

        out: list[dict] = []
        hdr_low = [h.lower() for h in header]
        for r in table[1:]:
            if not r:
                continue
            cells = {header[i]: r[i] for i in range(min(len(header), len(r)))}

            # 年 / 月：优先命中的列名；否则扫全行 token 兜底。
            yr = _parse_year_token(cells.get(c_year, "")) if c_year else None
            mo = _parse_month_token(cells.get(c_month, "")) if c_month else None
            joined = " ".join(r)
            if yr is None:
                yr = _parse_year_token(joined)
            if mo is None:
                mo = _parse_month_token(joined)

            # 营收：命中列优先；否则取该行中"最大的数值型 token"（月营收远大于同/环比 %）。
            rev = _to_float(cells.get(c_rev)) if c_rev else None
            if rev is None:
                nums = [
                    _to_float(x) for x in r
                    if _to_float(x) is not None and re.search(r"\d{4,}", x.replace(",", ""))
                ]
                nums = [n for n in nums if n is not None and n > 0]
                rev = max(nums) if nums else None

            if not (yr and mo and 1 <= mo <= 12) or rev is None or rev <= 0:
                continue

            usd = rev * _NTD_THOUSANDS_TO_USD  # NT$ 千元 → USD（清楚标注的单位换算）
            out.append({
                "metric_key": _TARGET_METRIC,
                "source_id": "tsmc",
                "value": round(usd, 2),
                "unit": "USD",                        # 与 registry metric.unit 对齐
                "valid_time": _month_end(yr, mo),     # 月度点取月末
                "knowledge_time": now,                # 该页仅"最新一版"，无 vintage 快照
            })
        # 去重：同 (年,月) 保最后一个（表可能重复呈现）。
        dedup: dict[date, dict] = {}
        for o in out:
            dedup[o["valid_time"]] = o
        return list(dedup.values())

    def fetch(self) -> list[dict]:
        now = datetime.now(timezone.utc)

        # 网络 / 挑战页 不可用 → 打印清晰一行原因并返回 []（干净 no-op，不臆造、不崩）。
        try:
            html = self._get_html(self._url)
        except Exception as e:  # noqa: BLE001
            print(f"[tsmc] 提示：下载月营收页失败（{type(e).__name__}: {e}），本轮跳过、返回空。"
                  f" 链接：{self._url}")
            return []

        low = html.lower()
        # Cloudflare / JS 挑战页识别：常见于 investor.tsmc.com（前端渲染 + 反爬）。
        if "just a moment" in low or "cf-challenge" in low or "challenge-platform" in low:
            print("[tsmc] 提示：月营收页返回 Cloudflare/JS 挑战页（非真实表格 HTML），"
                  "本环境无法直取；返回空（不臆造）。可改用已渲染 HTML 缓存或代理后重试。")
            return []

        # stdlib 解析所有 <table>，尽力从中抽取月营收三元组。
        try:
            parser = _TableParser()
            parser.feed(html)
            tables = parser.tables
        except Exception as e:  # noqa: BLE001
            print(f"[tsmc] 告警：HTML 表格解析异常（{type(e).__name__}: {e}），返回空（不臆造）。")
            return []

        if not tables:
            print("[tsmc] 提示：页面未解析到任何 <table>（可能前端异步渲染/结构改版），"
                  "返回空（不臆造）。")
            return []

        best: list[dict] = []
        for tbl in tables:
            rows = self._rows_from_table(tbl, now)
            if len(rows) > len(best):
                best = rows

        if not best:
            print("[tsmc] 提示：解析到表格但未识别出 (年,月,营收) 月营收行"
                  "（可能表结构漂移或被挑战页替换），返回空（不臆造）。")
            return []

        best.sort(key=lambda o: o["valid_time"])
        print(f"[tsmc] supply.semiconductor_chokepoint（月营收，NT$→USD@{_NTD_PER_USD}）："
              f"{len(best)} 个月度点，{best[0]['valid_time']} → {best[-1]['valid_time']}。")
        return best


if __name__ == "__main__":
    run_id = TsmcConnector().run()
    print(f"✓ tsmc（月营收咽喉代理）已写入，ingest_run_id={run_id}")
