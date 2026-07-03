"""V-Dem（Varieties of Democracy）—— 政权/选举稳定的"民主指数"腿（B_public_curated，文件型发布）。

下载 V-Dem 的版本化国别-年（Country-Year）数据集（registry sources 标 source_id=vdem）：
  proxy.legitimacy.regime_stability  ← V-Dem-CY-Core：民主指数（0..1），按 (国家, 年末) 贡献一条
                                       政权稳定信号（焦点国家=美国 + 一条全球均值）。

口径声明（审讯纪律）：
  - regime_stability 是【合成承重墙】，由本连接器（V-Dem 腿，source_id=vdem）与 acled 腿
    （抗议事件，source_id=acled）共同喂养——本文件**只写 V-Dem 腿**，下游再做合成；二者
    source_id 不同，故不会互相覆盖（observation 主键含 source_id）。
  - value = 选定的 V-Dem 民主指数（默认 v2x_polyarchy，电子民主/多头政体指数，0..1）；
    备用 v2x_libdem（自由民主指数）。指数本身非"稳定性"——它是体制开放度的水平量，
    稳定性需下游对水平做结构变化/方差检测（见 registry caveat："稳定性需自建合成"）。
  - valid_time = 该观测年的【年末】date(year, 12, 31)；knowledge_time = 摄取时刻（V-Dem 文件型
    发布无逐版 vintage 直链，故 vintage_aware=false，与 registry 一致）。
  - 焦点国家只取美国（避免把上百国全量灌入这条"代理腿"）；另产出一条全球均值
    （geo='GLOBAL_MEAN'，country_name 维度不入库，仅以 valid_time 区分）作为全局基线。
    口径=对当年所有有值国家做**简单算术均值**（非人口/GDP 加权——刻意保守，避免隐含权重假设）。

数据获取的现实性（与 epoch_ai 的 inference-price 骨架同构地"诚实降级"）：
  - V-Dem 正式发布是【ZIP 包】（内含 CSV/RDS/STATA + codebook），藏在带版本号的落地页后，
    URL 随版本漂移（…/country-year-v-dem-core-v15/），且文件很大（数十 MB），无稳定的裸 CSV 直链。
  - 故把下载 URL 设为默认参数 _DEFAULT_URL（可被 VDEM_CY_CORE_URL 环境变量覆盖）。运行时尝试下载：
      * 若 URL 为 None / 取不到 / 不是可解析的 CSV-或-含-CSV-的-ZIP → 打印清晰原因并返回 []（干净 no-op），
        **绝不臆造任何数值**。
      * 能拿到 CSV（直接或从 ZIP 内提取）则用容错列名匹配（_col，镜像 epoch_ai）解析。
  - 镜像 epoch_ai：requests + 小重试循环 + User-Agent "Silicon-Index research qzjacob@gmail.com"。

    python -m ingestion.connectors.vdem
"""
from __future__ import annotations

import csv
import io
import os
import zipfile
from collections import defaultdict
from datetime import date, datetime, timezone

from slx.ingestion.base import Connector

# V-Dem CY-Core 落地页（版本会漂移；这里给当前已知版本号，便于将来手工更新或用环境变量覆盖）。
# 注意：这是【落地页/可能触发 ZIP 下载】的 URL，不是稳定裸 CSV 直链——故下方解析对 ZIP 与 CSV 都容错。
# 真实下载常需走该页的 "download will start automatically" 跳转；若该 URL 当前不直接返回数据，
# 连接器会诚实降级到 []（不臆造）。可用 VDEM_CY_CORE_URL 指向你已下好的本地/镜像直链。
_DEFAULT_URL = "https://v-dem.net/data/the-v-dem-dataset/country-year-v-dem-core-v15/"

# 选用的民主指数（0..1）。v2x_polyarchy=电子民主/多头政体指数（最常用）；v2x_libdem=自由民主指数。
_PRIMARY_INDEX = "v2x_polyarchy"
_FALLBACK_INDEX = "v2x_libdem"

# 焦点国家（V-Dem country_name 口径）。只取美国一国 + 一条全球均值，避免把上百国全量灌入代理腿。
_US_NAMES = {"United States of America", "United States", "USA", "US"}


def _col(fieldnames: list[str], *keywords: str) -> str | None:
    """容错列名匹配：返回第一个包含全部关键词（小写）的列名（镜像 epoch_ai）。"""
    for f in fieldnames:
        low = f.lower()
        if all(k.lower() in low for k in keywords):
            return f
    return None


def _to_float(s) -> float | None:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, AttributeError, TypeError):
        return None


def _to_year(s) -> int | None:
    try:
        return int(float(str(s).strip()))  # 容忍 "2020" / "2020.0"
    except (ValueError, TypeError):
        return None


class VdemConnector(Connector):
    source_id = "vdem"
    connector = "ingestion.connectors.vdem"

    def __init__(self, url: str | None = None, index_col: str | None = None, session=None):
        # URL 默认走环境变量 → 模块默认；index_col 允许切换主指数。均有默认 → 可无参实例化。
        self._url = url or os.environ.get("VDEM_CY_CORE_URL", "").strip() or _DEFAULT_URL
        self._index_col = index_col or _PRIMARY_INDEX
        self._session = session

    # ── 下载并解析为 (fieldnames, rows)；对裸 CSV 与含-CSV-的-ZIP 都容错 ───────────────
    def _get_csv(self, url: str) -> tuple[list[str], list[dict]] | None:
        import requests
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(
                {"User-Agent": "Silicon-Index research qzjacob@gmail.com"}
            )
        last = None
        for i in range(3):
            try:
                # V-Dem 文件可能很大 → 流式下载 + 较长超时；stream 后用 .content 取字节。
                r = self._session.get(url, timeout=90, allow_redirects=True)
                r.raise_for_status()
                content = r.content
                ctype = (r.headers.get("Content-Type") or "").lower()

                # 情形 A：返回的是 ZIP（V-Dem 正式发布形态）→ 提取内部第一个 .csv。
                if content[:2] == b"PK" or "zip" in ctype:
                    return self._csv_from_zip(content)

                # 情形 B：返回的像 CSV/纯文本 → 直接解析。但要防止把 HTML 落地页误当 CSV。
                text = content.decode("utf-8", errors="replace")
                head = text.lstrip()[:512].lower()
                if head.startswith("<!doctype") or head.startswith("<html") or "<head" in head:
                    print(
                        "[vdem] 告警：URL 返回的是 HTML 落地页而非数据文件"
                        "（V-Dem 下载常需经该页跳转/手动触发 ZIP）。"
                        "请把已下载的 CY-Core ZIP/CSV 直链写入环境变量 VDEM_CY_CORE_URL 后重试；本轮降级为 no-op。"
                    )
                    return None
                rdr = csv.DictReader(io.StringIO(text))
                return (rdr.fieldnames or []), list(rdr)
            except Exception as e:  # noqa: BLE001
                last = e
                import time
                time.sleep(0.8 * (i + 1))
        # 重试耗尽：把原因抛给上层打印（不抛出、不臆造）。
        raise last if last else RuntimeError("unknown download error")

    @staticmethod
    def _csv_from_zip(content: bytes) -> tuple[list[str], list[dict]] | None:
        try:
            zf = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile:
            print("[vdem] 告警：下载内容疑似 ZIP 但无法解压；降级为 no-op。")
            return None
        # 优先含 'CY' 与 'Core' 的 CSV；否则取第一个 .csv。
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            print("[vdem] 告警：ZIP 内未找到 .csv（V-Dem 包可能只含 RDS/STATA）；"
                  "请解出 CSV 后用 VDEM_CY_CORE_URL 指向它。降级为 no-op。")
            return None
        pick = next(
            (n for n in csv_names if "cy" in n.lower() and "core" in n.lower()),
            csv_names[0],
        )
        with zf.open(pick) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace").read()
        rdr = csv.DictReader(io.StringIO(text))
        print(f"[vdem] 从 ZIP 提取 CSV：{pick}")
        return (rdr.fieldnames or []), list(rdr)

    def fetch(self) -> list[dict]:
        now = datetime.now(timezone.utc)

        if not self._url:
            print("[vdem] 提示：未配置下载 URL（VDEM_CY_CORE_URL 为空且无默认）；本轮跳过，不写库。")
            return []

        # 尝试下载 + 解析；任何失败 → 清晰单行原因 + 返回 []（绝不臆造）。
        try:
            parsed = self._get_csv(self._url)
        except Exception as e:  # noqa: BLE001
            print(f"[vdem] 提示：下载/解析 V-Dem CY-Core 失败（{type(e).__name__}: {e}）；"
                  f"URL={self._url}。本轮降级为 no-op，不写库、不臆造数值。")
            return []
        if parsed is None:
            return []  # 具体原因已在 _get_csv 内打印
        fields, rows = parsed

        # 容错匹配关键列：country_name / year / 选定指数列。
        c_country = _col(fields, "country", "name") or _col(fields, "country")
        c_year = _col(fields, "year")
        c_index = _col(fields, self._index_col) or _col(fields, _PRIMARY_INDEX) \
            or _col(fields, _FALLBACK_INDEX)
        if not (c_country and c_year and c_index):
            print(f"[vdem] 告警：CY-Core CSV 缺关键列（country/year/{self._index_col}）；"
                  f"实际列样本={fields[:8]}…。降级为 no-op。")
            return []
        if c_index != self._index_col:
            print(f"[vdem] 提示：未找到精确列 '{self._index_col}'，回退使用列 '{c_index}'。")

        out: list[dict] = []
        # 全球均值：按年聚合所有有值国家（简单算术均值，口径见 docstring）。
        sum_by_year: dict[int, float] = defaultdict(float)
        n_by_year: dict[int, int] = defaultdict(int)
        us_seen = 0

        for r in rows:
            yr = _to_year(r.get(c_year))
            val = _to_float(r.get(c_index))
            if yr is None or val is None:
                continue  # V-Dem 早期/未编码年份用空值，跳过
            sum_by_year[yr] += val
            n_by_year[yr] += 1

            country = (r.get(c_country) or "").strip()
            if country in _US_NAMES:
                ve = date(yr, 12, 31)  # 年末
                out.append({
                    "metric_key": "proxy.legitimacy.regime_stability",
                    "source_id": "vdem",
                    "value": round(val, 6),
                    "unit": "index_composite",
                    "valid_time": ve,
                    "knowledge_time": now,
                })
                us_seen += 1

        # 全球均值腿：每年一条（与美国腿共享 metric_key/valid_time，但同一 (国家,年) 不冲突，
        # 因为美国与全球均值落在不同 valid_time? —— 否：同一年末日期会与美国行同主键冲突。
        # 故全球均值改用年中 date(yr,7,1) 区分 valid_time，避免与"美国-年末"撞主键）。
        for yr in sorted(sum_by_year):
            n = n_by_year[yr]
            if n <= 0:
                continue
            out.append({
                "metric_key": "proxy.legitimacy.regime_stability",
                "source_id": "vdem",
                "value": round(sum_by_year[yr] / n, 6),
                "unit": "index_composite",
                "valid_time": date(yr, 7, 1),  # 年中=全球均值口径，与美国-年末区分主键
                "knowledge_time": now,
            })

        print(f"[vdem] regime_stability（V-Dem 腿）：美国 {us_seen} 年点 + "
              f"全球均值 {len(sum_by_year)} 年点，使用指数列 '{c_index}'。")
        if not out:
            print("[vdem] 告警：解析成功但无可用观测（country/year/index 均为空？）；返回 []。")
        return out


if __name__ == "__main__":
    run_id = VdemConnector().run()
    print(f"✓ vdem（regime_stability · V-Dem 腿）已写入，ingest_run_id={run_id}")
