"""OECD.AI —— "风投流向 AI 占比"的 OECD 腿（SDMX-JSON REST，无需 key，B_public_curated）。

直取 OECD 公开 SDMX-JSON REST API（sdmx.oecd.org/public/rest），搬运 OECD.AI Policy
Observatory 的 **AI 风险投资占比**（AI venture capital / AI investment share）——即某季度流向
AI 相关初创的创投美元占全部创投的百分比。产出（registry source_id=oecd_ai，series
ai_investment_share）：
  capital.vc_flows_to_ai  ← OECD.AI 口径下 AI 占创投比例（value=AI 份额 %，quarterly，valid_time=季度末）。

区间口径声明（审讯纪律 —— 本连接器只是一条腿，不是完整指标）：
  - capital.vc_flows_to_ai 在 registry 中以 **pct_range（区间）** 入库，而非点估。它由两条**互不
    调和**的腿共同喂入：PitchBook ai_share_of_vc（约 52.5%）与本连接器 OECD.AI ai_investment_share
    （约 61%）。两者统计口径不同（PitchBook 按其交易库口径；OECD.AI 按其抓取的初创名单 + 交易样本
    口径），**故不可平均、不可择一点估**。
  - 本连接器**只搬运 OECD 这一条腿的原始份额 %**（value = AI 份额百分数，如 61.0 表示 61%），
    单位沿用 registry 的 unit=pct_range 以与 metric 契约一致。**派生层（派生 metric / derived
    asset）** 再把 PitchBook 腿与本 OECD 腿对齐，取 (min, max) 构造区间（下界=共同"过半"），
    本处**不做合成、不臆造 min/max、不在此拼区间**。
  - 若届时能从 OECD 观测里同时拿到该季度的高/低估计，则以 value_low/value_high 携带（可选）；
    否则只写主 value，区间构造交给派生层。

端点契约（SDMX dataflow id 会漂移——故把请求 URL 设为可覆盖的默认参数）：
  GET https://sdmx.oecd.org/public/rest/data/<AGENCY,DATAFLOW,VERSION>/<KEY>?...&format=jsondata
  OECD.AI 的 AI 投资/风投数据集在 OECD 数据门户重构后 dataflow 命名频繁变动，且部分 OECD.AI
  指标经由其自有 dashboard 呈现、未必在 public SDMX REST 暴露稳定 dataflow。默认 URL 仅为
  **最佳努力尝试**（DSD 名为占位猜测）；若返回结构非预期（找不到 TIME_PERIOD、或无 AI 投资份额
  观测），打印一行清晰原因并降级返回 []（clean no-op，绝不臆造数值）——与 epoch_ai 的推理价格
  骨架、oecd_tax 的税率腿同纪律。可用 OecdAiConnector(url=...) 注入届时正确的 SDMX query URL 覆盖。

无需 API key。网络/结构不可达时清晰报因并空跑。

    python -m ingestion.connectors.oecd_ai
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from slx.ingestion.base import Connector

# 默认尝试 URL：OECD SDMX-JSON REST，OECD.AI AI 投资/风投份额，全球（TOTAL），季度。
# 说明：dataflow id（此处 DSD_AI/.. 仅为占位最佳猜测）会随 OECD/OECD.AI 门户重构漂移；
# format=jsondata 返回 SDMX-JSON。KEY 段留空=全维度，靠解析时按"AI 份额观测"筛选；
# dimensionAtObservation=AllDimensions 让观测键把各维度下标拼进 "i:j:k:..."。
# 若该 dataflow 命名失效/未暴露，请用 OecdAiConnector(url=<新 query URL>) 注入正确直链。
_DEFAULT_URL = (
    "https://sdmx.oecd.org/public/rest/data/"
    "OECD.STI.PIE,DSD_AI@DF_AI_INVEST,/.AI_VC_SHARE"
    "?dimensionAtObservation=AllDimensions&format=jsondata"
)

_TARGET_METRIC = "capital.vc_flows_to_ai"
_UNIT = "pct_range"  # 与 registry metric 契约一致（本腿以原始百分数携带，派生层再拼 (min,max)）

# 容错识别"AI 投资份额"类度量的关键词（用于在维度/度量取值里做包含匹配）。
_SHARE_KEYWORDS = ("share", "pct", "percent", "proportion")
_AI_KEYWORDS = ("ai_vc", "ai_invest", "vc_share", "venture", "ai investment", "ai vc")


def _to_float(s) -> float | None:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, AttributeError, TypeError):
        return None


def _contains_any(text: str, keywords) -> bool:
    low = (text or "").lower()
    return any(k.lower() in low for k in keywords)


def _quarter_end_from_period(s: str) -> date | None:
    """SDMX TIME_PERIOD → 季度末日期。容错以下形态：
       'YYYY-Q1'/'YYYY-Q4'、'2024Q3'、'YYYY-MM'（按所在季取季末）、'YYYY'（记为年末 12-31）。"""
    s = (s or "").strip().upper().replace(" ", "")
    if not s:
        return None

    def qe(y: int, q: int) -> date | None:
        if not (1 <= q <= 4):
            return None
        return {1: date(y, 3, 31), 2: date(y, 6, 30),
                3: date(y, 9, 30), 4: date(y, 12, 31)}[q]

    # 形如 'YYYY-Q3' 或 'YYYYQ3'
    if "Q" in s:
        head, _, tail = s.partition("Q")
        head = head.rstrip("-")
        try:
            y = int(head)
            q = int(tail)
        except ValueError:
            return None
        if not (1900 <= y <= 2100):
            return None
        return qe(y, q)

    # 形如 'YYYY-MM' → 按月份归季
    if "-" in s:
        parts = s.split("-")
        try:
            y = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 12
        except ValueError:
            return None
        if not (1900 <= y <= 2100) or not (1 <= m <= 12):
            return None
        return qe(y, (m - 1) // 3 + 1)

    # 纯 'YYYY' → 年末（annual 回退，仍归入季度末口径的 12-31）
    try:
        y = int(s)
    except ValueError:
        return None
    if not (1900 <= y <= 2100):
        return None
    return date(y, 12, 31)


class OecdAiConnector(Connector):
    source_id = "oecd_ai"
    connector = "ingestion.connectors.oecd_ai"

    def __init__(self, url: str | None = None):
        # url 设为默认参数：dataflow id 漂移时可注入正确 SDMX query 覆盖，无需改码。
        self._url = url or _DEFAULT_URL

    def _get_json(self, url: str, *, retries: int = 3, timeout: int = 40):
        import requests
        last = None
        for i in range(retries):  # 小重试循环，镜像 epoch_ai / oecd_tax
            try:
                r = requests.get(url, timeout=timeout, headers={
                    "User-Agent": "Silicon-Index research qzjacob@gmail.com",
                    # SDMX-JSON 需显式 Accept；OECD 同时识别 ?format=jsondata。
                    "Accept": "application/vnd.sdmx.data+json, application/json",
                })
                r.raise_for_status()
                return r.json()
            except Exception as e:  # noqa: BLE001
                last = e
                import time
                time.sleep(0.8 * (i + 1))
        raise last

    # ── SDMX-JSON 解析：dataSets[].observations + structure.dimensions ────────────
    # SDMX-JSON（dimensionAtObservation=AllDimensions）形态：
    #   data.structure.dimensions.observation = [ {id, values:[{id,name},...]}, ... ]
    #     —— 各维度取值表，观测键 "i:j:k:..." 每段是对应维度 values 的下标。
    #   data.dataSets[0].observations = { "i:j:k:...": [value, ...], ... }
    # 我们定位 TIME_PERIOD 维；若存在"度量/指标"维（MEASURE/INDICATOR）则筛"AI 投资份额"取值，
    # 逐季取 AI 份额 %；结构非预期则清晰报因、降级空跑（不臆造）。
    def _parse_sdmx(self, j, now) -> list[dict]:
        data = j.get("data", j) if isinstance(j, dict) else None
        if not isinstance(data, dict):
            print("[oecd_ai] 提示：SDMX-JSON 顶层结构非预期（无 data 节点），降级空跑。")
            return []

        structure = data.get("structure") or {}
        dims_node = (structure.get("dimensions") or {}).get("observation")
        datasets = data.get("dataSets") or []
        if not dims_node or not datasets:
            print("[oecd_ai] 提示：SDMX-JSON 缺 structure.dimensions.observation 或 dataSets，"
                  "dataflow 可能已漂移或未在 public SDMX 暴露；降级空跑（不臆造）。"
                  " 可注入正确 url 覆盖 _DEFAULT_URL。")
            return []

        # 定位时间维、以及可选的"度量/指标"维（用于筛 AI 投资份额观测）。
        time_pos = measure_pos = None
        for pos, dim in enumerate(dims_node):
            did = (dim.get("id") or "").upper()
            if did in ("TIME_PERIOD", "TIME"):
                time_pos = pos
            if did in ("MEASURE", "INDICATOR", "VARIABLE", "SERIES"):
                measure_pos = pos
        if time_pos is None:
            print("[oecd_ai] 提示：未在维度中找到 TIME_PERIOD，结构非预期，降级空跑。")
            return []

        time_values = dims_node[time_pos].get("values") or []
        measure_values = dims_node[measure_pos].get("values") if measure_pos is not None else None

        # 若有度量维，预先标出哪些度量下标属于"AI 投资份额"（按 id/name 关键词容错命中）。
        ai_measure_idx: set[int] | None = None
        if measure_values:
            ai_measure_idx = set()
            for mi, mv in enumerate(measure_values):
                mid = mv.get("id", "")
                mname = mv.get("name", "")
                if _contains_any(mid, _AI_KEYWORDS) or _contains_any(mname, _AI_KEYWORDS) or (
                    _contains_any(mid, _SHARE_KEYWORDS) or _contains_any(mname, _SHARE_KEYWORDS)
                ):
                    ai_measure_idx.add(mi)
            if not ai_measure_idx:
                # 度量维存在但无一命中 AI/份额 → 不臆造，改为不按度量维过滤（信任 KEY 已锁定）。
                ai_measure_idx = None

        observations = (datasets[0] or {}).get("observations") or {}
        if not observations:
            print("[oecd_ai] 提示：dataSets[0].observations 为空，无观测；降级空跑。")
            return []

        # 按季聚合：同季若多观测（不同附加口径段），取该季最大份额作前沿包络（口径见 docstring）。
        share_by_quarter: dict[date, float] = {}
        for key, val in observations.items():
            idx = key.split(":")
            # 有度量维且已识别 AI 份额度量时，只保留命中的观测。
            if ai_measure_idx is not None and measure_pos is not None:
                try:
                    mi = int(idx[measure_pos])
                except (IndexError, ValueError):
                    continue
                if mi not in ai_measure_idx:
                    continue
            try:
                tv = time_values[int(idx[time_pos])]
            except (IndexError, ValueError):
                continue
            d = _quarter_end_from_period(tv.get("id") or tv.get("name") or "")
            if d is None:
                continue
            # observations 值为数组，首元为观测值（其后为 attribute 下标）。
            share = _to_float(val[0]) if isinstance(val, (list, tuple)) and val else _to_float(val)
            if share is None:
                continue
            # 若数据以 0..1 比例表示（而非 0..100 百分数），归一到百分数。
            if 0.0 < share <= 1.0:
                share *= 100.0
            if not (0.0 < share <= 100.0):
                continue
            if share > share_by_quarter.get(d, float("-inf")):
                share_by_quarter[d] = share

        if not share_by_quarter:
            print("[oecd_ai] 提示：未筛出任何 AI 投资份额季度观测（度量编码/维度或 dataflow 已变）；"
                  "降级空跑，不臆造。可注入正确 url 覆盖 _DEFAULT_URL。")
            return []

        out: list[dict] = []
        for d, share in sorted(share_by_quarter.items()):
            out.append({
                "metric_key": _TARGET_METRIC,
                "source_id": "oecd_ai",
                "value": round(share, 4),  # AI 占创投比例（%）；本腿单值，派生层再与 PitchBook 腿拼 (min,max)
                "unit": _UNIT,             # pct_range（与 registry 契约一致）
                "valid_time": d,           # 季度末
                "knowledge_time": now,     # OECD.AI 仅呈现最新一版，无独立 vintage 快照
            })
        print(f"[oecd_ai] capital.vc_flows_to_ai（OECD 腿）解析 {len(out)} 个季度点，"
              f"{out[0]['valid_time']} → {out[-1]['valid_time']}（派生层再与 PitchBook 腿构造区间）。")
        return out

    def fetch(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        try:
            j = self._get_json(self._url)
        except Exception as e:  # noqa: BLE001
            print(f"[oecd_ai] 提示：OECD SDMX-JSON 取数失败（{type(e).__name__}: {e}）；"
                  "dataflow id 漂移、未在 public SDMX 暴露或网络不可达，降级空跑（clean no-op，不臆造数值）。"
                  " 可用 OecdAiConnector(url=<正确 SDMX query>) 注入覆盖默认 URL。")
            return []
        return self._parse_sdmx(j, now)


if __name__ == "__main__":
    run_id = OecdAiConnector().run()
    print(f"✓ oecd_ai（AI 占创投比例 OECD 腿）已写入，ingest_run_id={run_id}")
