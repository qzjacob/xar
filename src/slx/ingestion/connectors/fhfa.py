"""FHFA 房价指数 HPI —— 位置性资产（住房）腿，喂给"位置性/可复制"剪刀差比率（公开 CSV，无需 key，A_official）。

直下 FHFA 的公开月度 HPI 主表 CSV（hpi_master.csv，公共领域、无需 API key），产出
（registry sources 标 source_id=fhfa）：
  price.positional_authenticity_inversion ← FHFA 月度【纯购买型 purchase-only】全美 HPI 指数水平。

口径与派生层说明（审讯纪律）：
  - 本指标在 registry 中是一个**比率**：positional（位置性 / 不可复制资产，如土地+住房）相对
    reproducible（可复制工业品 / 可再生产商品）的相对价格——即所谓"剪刀差/真实性倒挂"。
  - FHFA 只提供其中的**位置性腿**（住房价格指数水平）。本连接器**只写位置性腿的原始指数水平**
    （value = HPI 指数点位，单位 ratio，valid_time = 当月月末）。可复制品腿（如 PPI 工业品 / 制造
    成本指数）由**别的连接器**提供；**派生层（派生 metric / derived asset）**再把两腿对齐相除，
    形成最终的剪刀差比率。故此处单源不构成完整比率，仅是其分子侧的输入真值。
  - 选择 series：hpi_master.csv 含季度+月度多种 flavor；本连接器锁定
    frequency=monthly、hpi_flavor=purchase-only、place_id=USA（全美），与 spec 的
    series_id "house_price_index"（monthly, geo US）对齐。
  - SA vs NSA：默认取**季调** index_sa（剔除季节性，更适合做月度比率/趋势）；若该行 SA 缺失则
    回退 NSA。基期 1991-01 = 100（FHFA 口径），故为无量纲指数（unit=ratio）。
  - 双时态：FHFA 月报会**修订**历史，但 hpi_master.csv 只提供"最新一版"，不暴露 vintage 快照；
    因此 knowledge_time=本次摄取时刻、vintage_date=哨兵（无独立 vintage）。需要 as-of 复刻历史
    修订者，应另接 ALFRED 风格的 vintage 源。

已对照线上真实表头（截至构建日 2026-06）：
  hpi_master.csv 列：'hpi_type','hpi_flavor','frequency','level','place_name','place_id',
      'yr'(年),'period'(月 1-12 / 季 1-4),'index_nsa'(浮点),'index_sa'(浮点),'rstderr','note'。
  全美月度纯购买行：hpi_flavor=purchase-only, frequency=monthly, place_id=USA, place_name=United States。
若 FHFA 改表头/拆链，解析以"列名包含关键词"做容错匹配（见 _col），并打印告警、降级为 [] 不臆造。

    python -m ingestion.connectors.fhfa
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timezone

from slx.ingestion.base import Connector

# FHFA 月度 HPI 主表（公共领域 CSV，无需 key）。设为默认参数，便于将来换链/本地缓存替换。
_HPI_MASTER_URL = "https://www.fhfa.gov/hpi/download/monthly/hpi_master.csv"

_TARGET_METRIC = "price.positional_authenticity_inversion"

# 锁定全美月度纯购买行的取值（小写容错匹配）。
_WANT_FREQ = "monthly"
_WANT_FLAVOR = "purchase-only"
_US_PLACE_IDS = {"USA"}                 # FHFA 全美 place_id
_US_PLACE_NAMES = {"United States", "United States of America", "US", "USA"}


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


def _to_int(s) -> int | None:
    try:
        return int(str(s).strip())
    except (ValueError, AttributeError):
        return None


def _month_end(year: int, month: int) -> date:
    """给定年月 → 该月月末日期（valid_time 口径与 epoch_ai 一致：月度点取月末）。"""
    nm = date(year + (month == 12), (month % 12) + 1, 1)
    return date.fromordinal(nm.toordinal() - 1)


class FhfaConnector(Connector):
    source_id = "fhfa"
    connector = "ingestion.connectors.fhfa"

    def __init__(self, url: str = _HPI_MASTER_URL, session=None):
        # url 作默认参数 → Connector() 可无参实例化；便于将来替换直链或注入本地缓存。
        self._url = url
        self._session = session

    def _get_csv(self, url: str) -> tuple[list[str], list[dict]]:
        import requests
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": "Silicon-Index research qzjacob@gmail.com"})
        last = None
        for i in range(3):  # 小重试循环，镜像 epoch_ai
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
            print(f"[fhfa] 提示：下载 HPI 主表失败（{type(e).__name__}: {e}），本轮跳过、返回空。"
                  f" 链接：{self._url}")
            return []

        # 容错定位关键列；FHFA 若改表头则降级（不臆造）。
        c_freq = _col(fields, "frequency")
        c_flavor = _col(fields, "flavor") or _col(fields, "hpi_flavor")
        c_place_id = _col(fields, "place", "id")
        c_place_name = _col(fields, "place", "name")
        c_yr = _col(fields, "yr") or _col(fields, "year")
        c_period = _col(fields, "period")
        # 注意："sa" 是 "nsa" 的子串 → 不能用 _col(.., "sa") 直接命中季调列（会误中 index_nsa）。
        # 先取 NSA，再在"含 index 且非 NSA"中找 SA 列，避免子串歧义。
        c_nsa = _col(fields, "index", "nsa") or _col(fields, "index_nsa")
        c_sa = None
        for f in fields:
            low = f.lower()
            if "index" in low and "nsa" not in low and "sa" in low:
                c_sa = f
                break
        if c_sa is None:  # 退而求其次：任何不等于 NSA 的 index 列
            for f in fields:
                if "index" in f.lower() and f != c_nsa:
                    c_sa = f
                    break

        # 至少需要：年、月、以及一个指数列；否则无法构造观测行。
        if not (c_yr and c_period and (c_sa or c_nsa)):
            print(f"[fhfa] 告警：HPI 主表缺关键列（yr/period/index_*），实得表头={fields}；"
                  f"本轮跳过、返回空（不臆造）。")
            return []

        out: list[dict] = []
        for r in rows:
            # 过滤到全美月度纯购买行（任一标识列缺失则放宽该过滤，靠 place 命中兜底）。
            if c_freq and (r.get(c_freq) or "").strip().lower() != _WANT_FREQ:
                continue
            if c_flavor and (r.get(c_flavor) or "").strip().lower() != _WANT_FLAVOR:
                continue
            pid = (r.get(c_place_id) or "").strip() if c_place_id else ""
            pname = (r.get(c_place_name) or "").strip() if c_place_name else ""
            is_us = (pid in _US_PLACE_IDS) or (pname in _US_PLACE_NAMES)
            if not is_us:
                continue

            yr = _to_int(r.get(c_yr))
            mo = _to_int(r.get(c_period))
            if not yr or not mo or not (1 <= mo <= 12):
                continue

            # 优先季调 SA；缺失回退 NSA（口径见 docstring）。
            val = _to_float(r.get(c_sa)) if c_sa else None
            if val is None and c_nsa:
                val = _to_float(r.get(c_nsa))
            if val is None or val <= 0:
                continue

            out.append({
                "metric_key": _TARGET_METRIC,
                "source_id": "fhfa",
                "value": val,                       # HPI 指数水平（位置性腿；派生层再除可复制品腿成比率）
                "unit": "ratio",                    # 基期=100 的无量纲指数
                "valid_time": _month_end(yr, mo),   # 月度点取月末
                "knowledge_time": now,              # hpi_master 仅"最新一版"，无 vintage 快照
            })

        if not out:
            print("[fhfa] 提示：未匹配到全美月度纯购买 HPI 行（可能 FHFA 改了口径/标识），返回空。")
            return []

        print(f"[fhfa] price.positional_authenticity_inversion（位置性腿）："
              f"{len(out)} 个月度点，{out[0]['valid_time']} → {out[-1]['valid_time']}。")
        return out


if __name__ == "__main__":
    run_id = FhfaConnector().run()
    print(f"✓ fhfa（HPI 位置性腿）已写入，ingest_run_id={run_id}")
