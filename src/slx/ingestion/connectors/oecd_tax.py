"""OECD Tax Database —— 再分配强度代理之"顶端税率腿"（SDMX-JSON REST，无需 key，A_official）。

直取 OECD 公开 SDMX-JSON REST API（sdmx.oecd.org/public/rest），搬运
美国**顶端法定个人所得税率**（Table I.7 top statutory personal income tax rates）作为
再分配强度（redistribution_intensity）的一条分量腿。产出（registry source_id=oecd_tax）：
  proxy.legitimacy.redistribution_intensity  ← OECD Table I.7 美国顶端边际税率（%，年度）。

复合口径声明（审讯纪律）：
  - proxy.legitimacy.redistribution_intensity 是**复合指标**：除本连接器的"OECD 顶端税率腿"外，
    还由 fred 的 transfers_gdp（转移支付/GDP）腿共同喂入；下游派生层做加权合成。
    本连接器只搬运 OECD 顶端税率这条可得分量，不在此做合成、不臆造权重。
  - 顶端税率以法定（statutory）顶档边际税率计，含/不含次中央政府附加视 OECD 表口径而定
    （Table I.7 为"中央+次中央组合后的综合顶端税率"）。这是法律名义税率，非有效税率，
    用作再分配**意图/制度强度**的代理腿，而非实际再分配落地额。
  - value=顶端边际税率（百分数，如 37.0 表示 37%）；unit=index_composite（与 registry metric
    口径一致：该 metric 为无量纲复合指数，本腿以原始百分数携带，合成层再做标准化）。
  - valid_time=该年年末（YYYY-12-31）；annual，geo=US。

端点契约（SDMX dataflow id 会漂移——故把请求 URL 设为可覆盖的默认参数）：
  GET https://sdmx.oecd.org/public/rest/data/<AGENCY,DATAFLOW,VERSION>/<KEY>?...&format=jsondata
  典型如 Table I.7（顶端个税率）的 dataflow。OECD 重构 dataflow 命名频繁，
  默认 URL 仅为**最佳努力尝试**；若返回结构非预期（找不到 USA 维度或 TIME_PERIOD），
  打印一行清晰原因并降级返回 []（绝不臆造数值）——与 epoch_ai 的推理价格骨架同纪律。
  可通过 OecdTaxConnector(url=...) 注入届时正确的 SDMX query URL 覆盖默认值。

无需 API key。网络/结构不可达时清晰报因并空跑（clean no-op）。

    python -m ingestion.connectors.oecd_tax
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from slx.ingestion.base import Connector

# 默认尝试 URL：OECD SDMX-JSON REST，Table I.7 顶端法定个税率，美国年度。
# 说明：dataflow id（此处 DSD_TAX_PIT/.. 仅为占位最佳猜测）会随 OECD 重构漂移；
# format=jsondata 返回 SDMX-JSON。维度 KEY 用 'USA' 锁美国；空段=全维度。
# 若该 dataflow 命名失效，请用 OecdTaxConnector(url=<新 query URL>) 注入正确直链。
_DEFAULT_URL = (
    "https://sdmx.oecd.org/public/rest/data/"
    "OECD.CTP.TPS,DSD_TAX_PIT@DF_TAX_PIT,/USA..PIT_TOP_RATE"
    "?dimensionAtObservation=AllDimensions&format=jsondata"
)

_US_CODES = {"USA", "US", "UNITED STATES", "UNITED STATES OF AMERICA"}


def _to_float(s) -> float | None:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, AttributeError, TypeError):
        return None


def _year_to_date(s: str) -> date | None:
    """SDMX TIME_PERIOD（年度通常为 'YYYY'，亦容错 'YYYY-12'/'YYYY-01-01'）→ 该年年末。"""
    s = (s or "").strip()
    if not s:
        return None
    head = s.split("-")[0]
    try:
        y = int(head)
    except ValueError:
        return None
    if not (1900 <= y <= 2100):
        return None
    return date(y, 12, 31)


def _looks_us(value_id: str, value_name: str) -> bool:
    return (value_id or "").upper() in _US_CODES or (value_name or "").upper() in _US_CODES


class OecdTaxConnector(Connector):
    source_id = "oecd_tax"
    connector = "ingestion.connectors.oecd_tax"

    def __init__(self, url: str | None = None):
        # url 设为默认参数：dataflow id 漂移时可注入正确 SDMX query 覆盖，无需改码。
        self._url = url or _DEFAULT_URL

    def _get_json(self, url: str, *, retries: int = 3, timeout: int = 40):
        import requests
        last = None
        for i in range(retries):
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
    #     —— 各维度的取值表，观测键 "i:j:k:..." 的每段是对应维度 values 的下标。
    #   data.dataSets[0].observations = { "i:j:k:...": [value, ...], ... }
    # 我们定位 TIME_PERIOD 维与 国别维（REF_AREA/COUNTRY/LOCATION），筛美国行，按年取顶端税率。
    def _parse_sdmx(self, j, now) -> list[dict]:
        data = j.get("data", j) if isinstance(j, dict) else None
        if not isinstance(data, dict):
            print("[oecd_tax] 提示：SDMX-JSON 顶层结构非预期（无 data 节点），降级空跑。")
            return []

        structure = data.get("structure") or {}
        dims_node = (structure.get("dimensions") or {}).get("observation")
        datasets = data.get("dataSets") or []
        if not dims_node or not datasets:
            print("[oecd_tax] 提示：SDMX-JSON 缺 structure.dimensions.observation 或 dataSets，"
                  "dataflow 可能已漂移；降级空跑（不臆造）。可注入正确 url 覆盖 _DEFAULT_URL。")
            return []

        # 定位时间维与国别维在 observation 维度序列中的位置（下标即观测键的段位）。
        time_pos = country_pos = None
        for pos, dim in enumerate(dims_node):
            did = (dim.get("id") or "").upper()
            if did in ("TIME_PERIOD", "TIME"):
                time_pos = pos
            if did in ("REF_AREA", "COUNTRY", "LOCATION", "COU"):
                country_pos = pos
        if time_pos is None:
            print("[oecd_tax] 提示：未在维度中找到 TIME_PERIOD，结构非预期，降级空跑。")
            return []

        time_values = dims_node[time_pos].get("values") or []
        country_values = dims_node[country_pos].get("values") if country_pos is not None else None

        observations = (datasets[0] or {}).get("observations") or {}
        if not observations:
            print("[oecd_tax] 提示：dataSets[0].observations 为空，无观测；降级空跑。")
            return []

        # 按年聚合：若多观测落同年（例如不同附加口径段），取该年最大顶端税率作前沿包络。
        rate_by_year: dict[date, float] = {}
        for key, val in observations.items():
            idx = key.split(":")
            # 国别维存在时筛美国；不存在则信任 KEY 已锁 USA（默认 URL 已 .USA. 过滤）。
            if country_pos is not None and country_values:
                try:
                    cv = country_values[int(idx[country_pos])]
                except (IndexError, ValueError):
                    continue
                if not _looks_us(cv.get("id", ""), cv.get("name", "")):
                    continue
            try:
                tv = time_values[int(idx[time_pos])]
            except (IndexError, ValueError):
                continue
            d = _year_to_date(tv.get("id") or tv.get("name") or "")
            if d is None:
                continue
            # observations 值为数组，首元为观测值（其后为 attribute 下标）。
            rate = _to_float(val[0]) if isinstance(val, (list, tuple)) and val else _to_float(val)
            if rate is None:
                continue
            if rate > rate_by_year.get(d, float("-inf")):
                rate_by_year[d] = rate

        if not rate_by_year:
            print("[oecd_tax] 提示：未筛出任何美国年度顶端税率观测（维度编码或 dataflow 已变）；"
                  "降级空跑，不臆造。")
            return []

        out: list[dict] = []
        for d, rate in sorted(rate_by_year.items()):
            out.append({
                "metric_key": "proxy.legitimacy.redistribution_intensity",
                "source_id": "oecd_tax",
                "value": round(rate, 4),  # 顶端边际税率（%）；复合指数的"顶端税率腿"，合成层再标准化
                "unit": "index_composite",
                "valid_time": d,           # 年末
                "knowledge_time": now,
            })
        print(f"[oecd_tax] redistribution_intensity（顶端税率腿）解析 {len(out)} 个年度点。")
        return out

    def fetch(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        try:
            j = self._get_json(self._url)
        except Exception as e:  # noqa: BLE001
            print(f"[oecd_tax] 提示：OECD SDMX-JSON 取数失败（{e}）；"
                  "dataflow id 漂移或网络不可达，降级空跑（clean no-op，不臆造数值）。"
                  " 可用 OecdTaxConnector(url=<正确 SDMX query>) 注入覆盖默认 URL。")
            return []
        return self._parse_sdmx(j, now)


if __name__ == "__main__":
    run_id = OecdTaxConnector().run()
    print(f"✓ oecd_tax（顶端税率腿）已写入，ingest_run_id={run_id}")
