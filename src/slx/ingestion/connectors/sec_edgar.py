"""SEC EDGAR companyconcept —— 超大规模厂商季度 capex（真值：会计事实，A_official）。

拉 Microsoft / Alphabet / Amazon / Meta 的资本支出（us-gaap），聚合为
  metric_key = capex.hyperscaler_capex
  source_id  = sec_edgar
每季一行：valid_time = 日历季度末，knowledge_time = filing 日（filed，即"市场何时能知道"）。

铁律落地（前视防护）：knowledge_time 必须取 SEC 实际 filing 日，而非摄取时刻——
否则把"2026 年 4 月才申报的 Q1 数"伪装成季度末当天就已知，制造前视偏差。

设计要点 / 审讯点：
  1) capex 的 GAAP 标签不止一个：现金流量表多用 PaymentsToAcquirePropertyPlantAndEquipment；
     Amazon 等用 PaymentsToAcquireProductiveAssets。逐标签尝试、合并去重（按区间）。
  2) 季度值口径不齐：MSFT/GOOGL/META 直接发布单季度（~3 个月跨度）事实；Amazon 现金流量表
     仅发布**财年累计 YTD**。故 fetch 内做两步：
        a) 优先取单季度事实（区间 80–100 天）；
        b) YTD 缺口处用"同财年相邻 YTD 相减"还原单季度（A2 守恒式差分，非凭空插值）。
  3) 跨公司对齐到**日历季度**：把每个单季度事实按其 end 落入的日历季度归并（如财季末 9/30→CY?Q3），
     四家齐备才求和入库——口径不全的季度留待后续摄取补齐（不写半截聚合）。
  4) 同一区间可能多版 filing（原始 + 修订）：聚合需单值，取 filing **最早**一版作"首次可知"。
  5) capex 不区分 AI / 非 AI（registry caveat 已声明）；入库口径为"公司总 capex"，AI 占比拆分属派生。

无需 API key，但 data.sec.gov **强制**自报 User-Agent（否则 403）。
从 SEC_EDGAR_USER_AGENT 读取，缺失则回退到一个可识别的研究用 UA 并打印提示。

    python -m ingestion.connectors.sec_edgar
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timezone

from slx.ingestion.base import Connector

# CIK 必须 10 位零填充（companyconcept 路径要求）。
HYPERSCALERS = {
    "MSFT":  "0000789019",  # Microsoft
    "GOOGL": "0001652044",  # Alphabet
    "AMZN":  "0001018724",  # Amazon
    "META":  "0001326801",  # Meta Platforms
}

# capex 的 GAAP 标签候选（逐一尝试、合并）：发行人/年度间会切换标签。
CAPEX_TAGS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
]

_BASE = "https://data.sec.gov/api/xbrl/companyconcept"
_Q_MIN_DAYS = 80   # 单季度跨度下界（闰季/财年错位 ~89–91 天）
_Q_MAX_DAYS = 100  # 单季度跨度上界
_YTD_MAX_DAYS = 370  # 财年累计上界（用于 YTD 差分还原季度）


def _user_agent() -> str:
    ua = os.environ.get("SEC_EDGAR_USER_AGENT", "").strip()
    if not ua:
        # 不致命——给一个可识别、可联系的回退 UA，但明确告警（SEC 礼仪要求真实联系方式）。
        ua = "Silicon-Index research (set SEC_EDGAR_USER_AGENT) noreply@example.com"
        print("[sec_edgar] 警告：未设置 SEC_EDGAR_USER_AGENT，使用占位 UA；"
              "请在 .env 配置真实 'Name email' 以遵守 SEC 访问礼仪。")
    return ua


def _parse_d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _cal_quarter(end: date) -> tuple[int, int]:
    """把季度末日期归并到日历季度键 (year, quarter)。财季末 ~每季最后一天。"""
    return (end.year, (end.month - 1) // 3 + 1)


def _quarter_end(year: int, q: int) -> date:
    return {1: date(year, 3, 31), 2: date(year, 6, 30),
            3: date(year, 9, 30), 4: date(year, 12, 31)}[q]


class SecEdgarCapexConnector(Connector):
    source_id = "sec_edgar"
    connector = "ingestion.connectors.sec_edgar"

    def __init__(self, session=None, request_pause: float = 0.20):
        # SEC 软上限约 10 req/s；连接器内串行 + 小停顿，礼貌且稳健。
        self._session = session
        self._pause = request_pause

    # ── HTTP（超时 / 重试 / User-Agent）──────────────────────────────────────
    def _get_json(self, url: str, *, retries: int = 4, timeout: int = 25):
        import requests  # 延迟导入：模块导入不依赖第三方库

        if self._session is None:
            self._session = requests.Session()
        headers = {
            "User-Agent": _user_agent(),
            "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov",
        }
        last_exc = None
        for attempt in range(retries):
            try:
                r = self._session.get(url, headers=headers, timeout=timeout)
                if r.status_code == 404:
                    return None  # 该发行人未用此标签——交由调用方尝试下一标签
                if r.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))  # 限流退避
                    continue
                r.raise_for_status()
                return r.json()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                time.sleep(0.8 * (attempt + 1))
        if last_exc:
            raise last_exc
        return None

    # ── 取一家所有 USD 事实（合并所有候选标签，按 (start,end) 去重，保留最早 filing）──
    def _raw_facts(self, cik: str) -> list[dict]:
        merged: dict[tuple[date, date], dict] = {}
        for tag in CAPEX_TAGS:
            data = self._get_json(f"{_BASE}/CIK{cik}/us-gaap/{tag}.json")
            time.sleep(self._pause)
            if not data:
                continue
            for f in data.get("units", {}).get("USD", []):
                start, end, val, filed = f.get("start"), f.get("end"), f.get("val"), f.get("filed")
                if not (start and end and val is not None and filed):
                    continue
                try:
                    d0, d1, fd = _parse_d(start), _parse_d(end), _parse_d(filed)
                except ValueError:
                    continue
                key = (d0, d1)
                prev = merged.get(key)
                if prev is None or fd < prev["filed"]:  # 保留首次申报
                    merged[key] = {"start": d0, "end": d1, "val": float(val), "filed": fd}
        return sorted(merged.values(), key=lambda x: (x["start"], x["end"]))

    # ── 把一家事实还原为"单季度"读数 {(cal_year,cal_q): {"val","filed"}} ────────────
    def _quarterly(self, facts: list[dict]) -> dict[tuple[int, int], dict]:
        out: dict[tuple[int, int], dict] = {}
        # (a) 直接的单季度事实（跨度 ~3 个月）
        for f in facts:
            span = (f["end"] - f["start"]).days
            if _Q_MIN_DAYS <= span <= _Q_MAX_DAYS:
                ck = _cal_quarter(f["end"])
                if ck not in out:
                    out[ck] = {"val": f["val"], "filed": f["filed"]}
        # (b) YTD 差分还原缺失季度：按 start（财年起点）分组，相邻 YTD 相减
        ytd = [f for f in facts if (f["end"] - f["start"]).days > _Q_MAX_DAYS
               and (f["end"] - f["start"]).days <= _YTD_MAX_DAYS]
        by_fy: dict[date, list[dict]] = {}
        for f in ytd:
            by_fy.setdefault(f["start"], []).append(f)
        for fy_start, group in by_fy.items():
            group.sort(key=lambda x: x["end"])
            prev_end = None
            prev_val = 0.0
            for f in group:
                ck = _cal_quarter(f["end"])
                if ck in out:
                    prev_end, prev_val = f["end"], f["val"]
                    continue
                # 仅当与上一 YTD 相邻约一个季度时才差分（避免跳季造成错误归并）
                if prev_end is None:
                    span = (f["end"] - f["start"]).days
                    if _Q_MIN_DAYS <= span <= _Q_MAX_DAYS:  # 首段本身即一季
                        out[ck] = {"val": f["val"], "filed": f["filed"]}
                else:
                    gap = (f["end"] - prev_end).days
                    if _Q_MIN_DAYS <= gap <= _Q_MAX_DAYS:
                        out[ck] = {"val": f["val"] - prev_val, "filed": f["filed"]}
                prev_end, prev_val = f["end"], f["val"]
        return out

    # ── 聚合：四家按日历季度求和──────────────────────────────────────────────
    def fetch(self) -> list[dict]:
        # 结构：{(year,q): {ticker: {"val","filed"}}}
        by_q: dict[tuple[int, int], dict[str, dict]] = {}
        any_data = False

        for ticker, cik in HYPERSCALERS.items():
            facts = self._raw_facts(cik)
            if not facts:
                print(f"[sec_edgar] 提示：{ticker}(CIK{cik}) 未取到任何 capex 事实（标签缺失或网络）。")
                continue
            any_data = True
            for ck, rec in self._quarterly(facts).items():
                by_q.setdefault(ck, {})[ticker] = rec

        if not any_data:
            raise RuntimeError(
                "[sec_edgar] 四家发行人均未取到数据——疑似网络不可达或 UA 被拒。"
                "请检查 SEC_EDGAR_USER_AGENT 与到 data.sec.gov 的连通性。"
            )

        rows: list[dict] = []
        for (year, q), slot in sorted(by_q.items()):
            # 只在"四家都齐备该季度"时入聚合行——否则口径不全，留待后续摄取补齐。
            if len(slot) < len(HYPERSCALERS):
                continue
            total = sum(s["val"] for s in slot.values())
            # knowledge_time = 该季最后一家 filing 的日期（全部可知后市场才掌握聚合量）。
            kt = max(s["filed"] for s in slot.values())
            rows.append({
                "metric_key": "capex.hyperscaler_capex",
                "source_id": "sec_edgar",
                "value": total,
                "unit": "USD",
                "valid_time": _quarter_end(year, q),
                "knowledge_time": datetime(kt.year, kt.month, kt.day, tzinfo=timezone.utc),
            })

        if not rows:
            print("[sec_edgar] 提示：尚无任一日历季度集齐四家读数，无聚合行写入（数据将随时间补齐）。")
        return rows


if __name__ == "__main__":
    run_id = SecEdgarCapexConnector().run()
    print(f"✓ sec_edgar capex 已写入，ingest_run_id={run_id}")
