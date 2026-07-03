"""BEA（美国经济分析局）—— 知识产权产品（IPP）投资对实际 GDP 增长的贡献（NIPA 会计分解，A_official，需 key）。

经 BEA 官方 REST API 直取 NIPA 表 T10502（= 印刷表 1.5.2《Contributions to Percent Change in
Real Gross Domestic Product / 对实际 GDP 增长率的贡献》），产出（registry sources 标 source_id=bea）：
  macro.ai_capex_gdp_contribution ← 该表【知识产权产品 Intellectual property products】行的季度贡献
      （单位：贡献的百分点 percentage points of real GDP growth；口径见下）。

series 对齐：registry 的 series_id = gdp_contribution_ipp，quarterly，unit = pct_of_gdp_growth。
  BEA 里 IPP 投资 = 软件 + 研发（R&D）+ 娱乐/文学/艺术原创；其中【软件】与部分 R&D 正是 AI/数字
  资本开支（capex）在国民账户里的落点。故本行是 "AI-相关无形资本投资对 GDP 增长贡献" 的最接近官方代理。

口径声明（审讯纪律 / 口径 caveats）：
  - 这是一个【会计分解（accounting decomposition）】而非【因果贡献】：NIPA 把实际 GDP 增长率按支出
    构成分拆成各分项的加性贡献（各分项贡献之和 ≈ 实际 GDP 增长率）。它回答"IPP 投资这一项在算术上
    贡献了增长率的多少个百分点"，**不**回答"若无 AI capex 增长会低多少"（无反事实、无因果、无乘数）。
  - IPP ⊋ AI：IPP 含全部软件 + 全部 R&D + 原创作品，AI 只是其中一部分；本连接器给出的是 IPP 全口径
    贡献（上界代理），非纯 AI。想收窄到 AI 需另接更细分的软件/半导体投资分解（当前 NIPA 未单列）。
  - 单位：贡献本身就是"百分点"（percentage-points of the real GDP growth rate），非份额、非增速。
    映射 unit = "pct_of_gdp_growth"（与 registry 一致）。BEA 该表 CL_UNIT 通常回 "Percentage points"，
    UNIT_MULT=0；本连接器不依赖乘数（贡献表天然为绝对百分点，不做 10^UNIT_MULT 缩放）。
  - valid_time = 季度末（quarter-end；如 2024Q1 → 2024-03-31），与 epoch_ai/fhfa 的"期末"口径一致。
  - vintage：BEA 会**修订**历史（advance→second→third→年度基准修订），是 vintage-aware 源；但本连接器
    走 GetData 只取"最新一版"、不逐版展开 vintage，故 knowledge_time=本次摄取时刻、vintage_date=哨兵。
    需 as-of 复刻历史修订者，应另接 BEA 的 vintage/GetData(历史发布) 或 ALFRED 风格快照。

依赖 BEA_API_KEY（免费注册 https://apps.bea.gov/API/signup/）。缺 key / 网络或数据不可用 / 返回形状异常时：
  打印清晰一行原因并返回 []（干净 no-op），绝不硬编码假数、绝不崩溃。已对照线上真实响应包络：
  成功 → BEAAPI.Results.Data[]（含 LineDescription/TimePeriod/DataValue/CL_UNIT/UNIT_MULT）；
  出错 → BEAAPI.Results.Error{APIErrorCode,APIErrorDescription}（如无效 key 返回 code=1）。

    python -m ingestion.connectors.bea
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone

from slx.ingestion.base import Connector

# BEA REST 数据端点（公开、需免费 UserID）。设为默认参数，便于将来换链/注入测试桩。
_BEA_API_URL = "https://apps.bea.gov/api/data/"
# NIPA 表 T10502 = 印刷表 1.5.2《对实际 GDP 增长率的贡献》。IPP 行即在此表非住宅固定投资下。
_TABLE_NAME = "T10502"

_TARGET_METRIC = "macro.ai_capex_gdp_contribution"

# 目标行的容错匹配关键词（小写子串全含）：BEA 该行 LineDescription 通常为
# "Intellectual property products"。用关键词而非精确字符串，抗 BEA 措辞/缩进变动。
_IPP_KEYWORDS = ("intellectual", "property", "products")

# 拉取年份范围：'ALL' 让 BEA 回全历史（该表始于 1947 附近），一次成图；失败再降级。
_YEARS = "ALL"


def _field(row: dict, *keywords: str) -> str | None:
    """容错字段名匹配：返回 row 里第一个键名包含全部关键词（小写）的**值**。

    BEA 的 JSON 键（TableName/LineDescription/TimePeriod/DataValue/CL_UNIT/UNIT_MULT）大小写稳定，
    但用容错匹配可抗大小写/前后缀变动（镜像 epoch_ai._col 的思路，落到 dict 键上）。
    """
    for k in row.keys():
        low = k.lower()
        if all(w.lower() in low for w in keywords):
            return row.get(k)
    return None


def _line_desc_contains(row: dict, keywords: tuple[str, ...]) -> bool:
    desc = _field(row, "linedescription") or _field(row, "line", "desc") or ""
    low = str(desc).strip().lower()
    return all(k in low for k in keywords)


def _to_float(s) -> float | None:
    # BEA DataValue 以字符串给出，可能带千分位逗号；缺失/N/A 记为 None（跳过，不臆造）。
    try:
        t = str(s).replace(",", "").strip()
        if t == "" or t.upper() in {"N/A", "NA", "(NA)", "(D)", "---", "..."}:
            return None
        return float(t)
    except (ValueError, AttributeError):
        return None


def _quarter_end_from_timeperiod(tp: str) -> date | None:
    """BEA 季度 TimePeriod 形如 '2024Q1' → 该季度末日期。非季度格式返回 None（跳过）。"""
    s = (tp or "").strip().upper()
    if len(s) < 6 or "Q" not in s:
        return None
    try:
        year_str, q_str = s.split("Q", 1)
        yr = int(year_str)
        q = int(q_str)
    except ValueError:
        return None
    ends = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    if q not in ends:
        return None
    m, d = ends[q]
    return date(yr, m, d)


class BeaConnector(Connector):
    source_id = "bea"
    connector = "ingestion.connectors.bea"

    def __init__(self, api_key: str | None = None, url: str = _BEA_API_URL,
                 table_name: str = _TABLE_NAME, session=None):
        # 全部带默认 → Connector() 可无参实例化。key 缺省从环境读取。
        self._api_key = (api_key or os.environ.get("BEA_API_KEY", "")).strip()
        self._url = url
        self._table_name = table_name
        self._session = session

    def _get_json(self, params: dict) -> dict:
        import requests
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": "Silicon-Index research qzjacob@gmail.com"})
        last = None
        for i in range(3):  # 小重试循环，镜像 epoch_ai/fhfa
            try:
                r = self._session.get(self._url, params=params, timeout=60)
                r.raise_for_status()
                return r.json()
            except Exception as e:  # noqa: BLE001
                last = e
                import time
                time.sleep(0.8 * (i + 1))
        raise last

    def fetch(self) -> list[dict]:
        now = datetime.now(timezone.utc)

        # 缺 key → 清晰一行原因 + 申请地址，返回 []（不写库、不臆造、不崩）。
        if not self._api_key:
            print("[bea] 缺 BEA_API_KEY——本轮跳过、返回空。"
                  "免费申请：https://apps.bea.gov/API/signup/，然后写入 .env 的 BEA_API_KEY。")
            return []

        params = {
            "UserID": self._api_key,
            "method": "GetData",
            "datasetname": "NIPA",
            "TableName": self._table_name,   # T10502 = 1.5.2 对实际 GDP 增长率的贡献
            "Frequency": "Q",                # 季度
            "Year": _YEARS,                  # 'ALL' 全历史
            "ResultFormat": "JSON",
        }

        # 网络/端点不可用 → 打印原因并返回 []（干净 no-op）。
        try:
            payload = self._get_json(params)
        except Exception as e:  # noqa: BLE001
            print(f"[bea] 提示：请求 BEA API 失败（{type(e).__name__}: {e}），本轮跳过、返回空。"
                  f" 端点：{self._url}?...TableName={self._table_name}")
            return []

        # 解析 BEA 响应包络：BEAAPI.Results.{Data|Error}。形状异常一律降级（不臆造）。
        if not isinstance(payload, dict):
            print(f"[bea] 告警：BEA 返回非 JSON 对象（{type(payload).__name__}），跳过、返回空。")
            return []
        beaapi = payload.get("BEAAPI") or payload.get("BEAAPI".lower()) or {}
        results = (beaapi or {}).get("Results") or {}
        # BEA 偶将 Results 包成 list（多 method 批处理时）；取第一个 dict。
        if isinstance(results, list):
            results = next((x for x in results if isinstance(x, dict)), {})

        err = results.get("Error") if isinstance(results, dict) else None
        if err:
            # 典型：无效/过期 key（APIErrorCode=1）。打印 BEA 自带的原因，返回 []。
            code = _field(err, "code") if isinstance(err, dict) else None
            desc = _field(err, "description") if isinstance(err, dict) else err
            print(f"[bea] BEA API 返回错误（code={code}）：{desc}。本轮跳过、返回空。")
            return []

        data = results.get("Data") if isinstance(results, dict) else None
        if not isinstance(data, list) or not data:
            print(f"[bea] 告警：BEA 返回无 Data 数组（形状异常或空表），实得 keys={list(results.keys()) if isinstance(results, dict) else results}；"
                  f"本轮跳过、返回空（不臆造）。")
            return []

        # 定位【知识产权产品 IPP】行，逐季度产出贡献（百分点）。
        out: list[dict] = []
        seen: set[date] = set()
        for r in data:
            if not isinstance(r, dict):
                continue
            if not _line_desc_contains(r, _IPP_KEYWORDS):
                continue  # 只保留 IPP 行；其余分项忽略
            tp = _field(r, "timeperiod") or _field(r, "time", "period")
            qe = _quarter_end_from_timeperiod(str(tp) if tp is not None else "")
            if qe is None:
                continue  # 非季度期（如年度汇总行）跳过
            val = _to_float(_field(r, "datavalue") or _field(r, "data", "value"))
            if val is None:
                continue  # 缺失/被抑制值跳过，不臆造
            if qe in seen:
                # 同一季度多行（理论上不该，稳妥起见去重，保留首见）。
                continue
            seen.add(qe)
            out.append({
                "metric_key": _TARGET_METRIC,
                "source_id": "bea",
                "value": val,                       # IPP 投资对实际 GDP 增长的贡献（百分点）
                "unit": "pct_of_gdp_growth",        # 与 registry series gdp_contribution_ipp 一致
                "valid_time": qe,                   # 季度末
                "knowledge_time": now,              # GetData 只取最新版，无逐版 vintage
            })

        if not out:
            print("[bea] 提示：NIPA T10502 未匹配到【Intellectual property products】季度行"
                  "（可能 BEA 改了行措辞/表号），本轮返回空（不臆造）。")
            return []

        out.sort(key=lambda x: x["valid_time"])
        print(f"[bea] macro.ai_capex_gdp_contribution（IPP 投资对实际 GDP 增长贡献，会计分解）："
              f"{len(out)} 个季度点，{out[0]['valid_time']} → {out[-1]['valid_time']}。")
        return out


if __name__ == "__main__":
    run_id = BeaConnector().run()
    print(f"✓ bea（IPP 对 GDP 增长贡献，NIPA 1.5.2）已写入，ingest_run_id={run_id}")
