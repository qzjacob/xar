"""FRED / ALFRED —— 带 vintage 的宏观真值（A_official）。

产出（registry sources 标 source_id=fred, vintage_aware=true）：
  labor.labor_share   ← FRED series PRS85006173（非农劳动报酬份额，季度）
  macro.fed_funds_rate ← FRED series FEDFUNDS（联邦基金有效利率，月度）
  + AM 宏观外环 38 序列（rates/inflation/growth/liquidity/credit/fiscal/fx_commodity/
    sentiment 八族,见 SERIES 表;vintage 窗统一截到 2015,老观测只会"晚知"不"早知"）

为什么必须带 vintage（前视防护命门）：
  这两个序列**会被修订**。回测/登记簿判定只能用"那天能知道的值"。ALFRED 暴露每个
  发布快照（vintage）；本连接器用 fredapi 的 get_series_all_releases，把同一 valid_time
  的多版发布展开为多行：
    valid_time     = 观测期日期（economic date）
    knowledge_time = 该版发布日（realtime_start，即"何时得知"）
    vintage_date   = 同上发布日（ALFRED 口径，哪一版）
  于是 PointInTimeContext 能精确复刻"as_of 当天看到的是哪一版数字"。

需 FRED_API_KEY（免费）。缺 key 时**不写库、清晰报错并给出申请地址**，绝不硬编码。
依赖 fredapi（已在 requirements）；延迟导入，缺库时给安装提示。

    python -m ingestion.connectors.fred_alfred
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pandas as pd  # fredapi 依赖 pandas；与现有栈一致

from slx.ingestion.base import Connector

# (metric_key, series_id, unit, realtime_start) —— series_id 与 registry/metrics/*.yml 完全一致。
# realtime_start：vintage 窗下界（None=全历史）。AM 宏观族统一截到 2015——重修订月频序列
# (CPI/GDP/非农…) 的全量 vintage 可达 10^5 行/序列;截断后 PIT 语义仍保守安全
# (老观测的 knowledge_time 被钳到窗口起点,只会"晚知"不会"早知")。
_RT = "2015-01-01"

# 日频"从不/几乎不修订"的市场序列走普通序列取数(真机捕获:ALFRED 对窗口内 >2000 个
# vintage 日期的序列直接 400——日频序列每天一个 vintage,2015 起 ≈2900 个超帽;
# SOFR/DTWEXBGS/DCOILWTICO 今天还没过帽但每天 +1,一年内必炸,一并纳入)。
# 这些序列 vintage 展开无信息;knowledge_time 合成为 valid_time 次日(市场收盘数据
# 次日可知,PIT 保守诚实)。**append-only**:已有 valid_time 的行绝不重写——若 FRED
# 事后订正历史值,原始印字保持不动(否则订正值会顶着原 knowledge_time 前视泄漏)。
_DAILY_MODE = {"DGS2", "DGS10", "DGS30", "T10Y2Y", "DFII10", "T10YIE",
               "RRPONTSYD", "VIXCLS", "SOFR", "DTWEXBGS", "DCOILWTICO"}
SERIES = [
    ("labor.labor_share", "PRS85006173", "pct", None),
    ("macro.fed_funds_rate", "FEDFUNDS", "pct", None),
    # ── rates 利率(AM 波次) ──────────────────────────────────────────────
    ("rates.ust_2y", "DGS2", "pct", _RT),
    ("rates.ust_10y", "DGS10", "pct", _RT),
    ("rates.ust_30y", "DGS30", "pct", _RT),
    ("rates.ust_2s10s_spread", "T10Y2Y", "pct", _RT),
    ("rates.ust_10y_real", "DFII10", "pct", _RT),
    ("rates.breakeven_10y", "T10YIE", "pct", _RT),
    ("rates.sofr", "SOFR", "pct", _RT),
    ("rates.mortgage_30y", "MORTGAGE30US", "pct", _RT),
    # ── inflation 通胀 ───────────────────────────────────────────────────
    ("inflation.cpi", "CPIAUCSL", "index", _RT),
    ("inflation.core_cpi", "CPILFESL", "index", _RT),
    ("inflation.core_pce", "PCEPILFE", "index", _RT),
    ("inflation.sticky_cpi_yoy", "CORESTICKM159SFRBATL", "pct", _RT),
    ("inflation.ppi", "PPIACO", "index", _RT),
    # ── growth 增长 ──────────────────────────────────────────────────────
    ("growth.real_gdp", "GDPC1", "bil_usd", _RT),
    ("growth.industrial_production", "INDPRO", "index", _RT),
    ("growth.retail_sales", "RSAFS", "mil_usd", _RT),
    ("growth.nonfarm_payrolls", "PAYEMS", "thousands", _RT),
    ("growth.unemployment_rate", "UNRATE", "pct", _RT),
    ("growth.initial_claims", "ICSA", "count", _RT),
    ("growth.housing_starts", "HOUST", "thousands", _RT),
    ("growth.job_openings", "JTSJOL", "thousands", _RT),
    ("growth.avg_hourly_earnings", "CES0500000003", "usd", _RT),
    # ── liquidity 流动性层级 ─────────────────────────────────────────────
    ("liquidity.fed_total_assets", "WALCL", "mil_usd", _RT),
    ("liquidity.on_rrp", "RRPONTSYD", "bil_usd", _RT),
    ("liquidity.tga", "WTREGEN", "bil_usd", _RT),
    ("liquidity.bank_reserves", "WRESBAL", "bil_usd", _RT),
    ("liquidity.m2", "M2SL", "bil_usd", _RT),
    # ── credit 信用条件 ──────────────────────────────────────────────────
    ("credit.hy_oas", "BAMLH0A0HYM2", "pct", _RT),
    ("credit.ig_oas", "BAMLC0A0CM", "pct", _RT),
    # NFCI 每周全序列重估:2015 窗的 (obs×vintage) 行数 87 万,远超 FRED 单请求 10 万帽
    # (fredapi 不分页,oldest-first 截断会丢最新数据,评审真机捕获)——窗收到 1 年。
    ("credit.nfci", "NFCI", "index", "2025-07-01"),
    ("credit.sloos_ci_standards", "DRTSCILM", "pct", _RT),
    # ── fiscal 财政 ──────────────────────────────────────────────────────
    ("fiscal.federal_deficit", "MTSDS133FMS", "mil_usd", _RT),
    ("fiscal.public_debt", "GFDEBTN", "mil_usd", _RT),
    # ── fx_commodity 汇率商品 ────────────────────────────────────────────
    ("fx.usd_broad", "DTWEXBGS", "index", _RT),
    ("cmdty.wti_crude", "DCOILWTICO", "usd_per_barrel", _RT),
    ("cmdty.copper", "PCOPPUSDM", "usd_per_tonne", _RT),
    # ── sentiment 情绪 ───────────────────────────────────────────────────
    ("sentiment.umich", "UMCSENT", "index", _RT),
    ("sentiment.vix", "VIXCLS", "index", _RT),
]


def _to_date(x) -> date:
    if isinstance(x, date) and not isinstance(x, datetime):
        return x
    return pd.Timestamp(x).date()


def _existing_max_valid(metric_key: str) -> date | None:
    """日频 append-only 闸的水位线（该 metric 已入库的最大 valid_time）。
    DB 不可用（离线测试/首跑无表）→ None = 全量写入。"""
    try:
        from slx.db import connect

        with connect() as conn:
            row = conn.execute(
                "SELECT max(valid_time) FROM observation "
                "WHERE metric_key=%s AND source_id='fred'", (metric_key,)).fetchone()
        v = row[0] if row else None
        return v.date() if isinstance(v, datetime) else v
    except Exception:  # noqa: BLE001
        return None


class FredAlfredConnector(Connector):
    source_id = "fred"
    connector = "ingestion.connectors.fred_alfred"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("FRED_API_KEY", "").strip()

    def _client(self):
        if not self._api_key:
            raise RuntimeError(
                "[fred_alfred] 缺 FRED_API_KEY——不写库。"
                "免费申请：https://fred.stlouisfed.org/docs/api/api_key.html，"
                "然后写入 .env 的 FRED_API_KEY。"
            )
        try:
            from fredapi import Fred
        except ImportError as e:
            raise RuntimeError(
                "[fred_alfred] 未安装 fredapi。请 `pip install fredapi`（中央 venv 统一安装）。"
            ) from e
        return Fred(api_key=self._api_key)

    def fetch(self) -> list[dict]:
        import socket
        import time

        fred = self._client()
        rows: list[dict] = []
        failed: list[str] = []
        # fredapi 用无 timeout 的 urlopen——一次挂起的响应会永久卡死工人循环(评审捕获)。
        # 进程级默认超时 + finally 复原,给 40 个请求一个硬上界。
        _old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(60)
        try:
            self._fetch_all(fred, rows, failed, time)
        finally:
            socket.setdefaulttimeout(_old_timeout)
        if failed:
            print(f"[fred_alfred] 本轮失败序列({len(failed)}): {failed}")
        if not rows:
            raise RuntimeError("[fred_alfred] 全部序列均无数据——检查 API key 与连通性。")
        return rows

    def _fetch_all(self, fred, rows: list[dict], failed: list[str], time) -> None:
        for metric_key, series_id, unit, rt in SERIES:
            # get_series_all_releases：返回长表 [realtime_start(=发布日), date(=观测期), value]。
            # 这是 ALFRED 的 vintage 全量——每个 (观测期, 发布版) 一行。
            # 单序列失败绝不沉整跑（40 序列的批量拉取,一个停更/改名的 id 不能拖垮全部）。
            n_before = len(rows)
            if series_id in _DAILY_MODE:
                try:
                    ser = fred.get_series(series_id, observation_start=rt or "2015-01-01")
                except Exception as e:  # noqa: BLE001
                    failed.append(series_id)
                    print(f"[fred_alfred] 警告：{metric_key}({series_id}) 拉取失败,跳过：{e}")
                    continue
                # append-only 闸:该 metric 已入库的最大 valid_time 之后才写。当前 vintage
                # 序列对历史日期的事后订正一律丢弃——base 的双时态 upsert 会用订正值顶着
                # 原 knowledge_time 重写历史(前视泄漏,评审捕获);原始印字必须不可变。
                existing_max = _existing_max_valid(metric_key)
                for idx, val in ser.items():
                    if val is None or pd.isna(val):
                        continue
                    valid = _to_date(idx)
                    if existing_max is not None and valid <= existing_max:
                        continue
                    release = valid + timedelta(days=1)   # 收盘数据次日可知(保守 PIT)
                    rows.append({
                        "metric_key": metric_key, "source_id": "fred",
                        "value": float(val), "unit": unit, "valid_time": valid,
                        "knowledge_time": datetime(release.year, release.month, release.day,
                                                   tzinfo=timezone.utc),
                        "vintage_date": release,
                    })
                print(f"[fred_alfred] {metric_key}({series_id}): 日频普通序列 "
                      f"{len(rows) - n_before} 新行(knowledge=次日,append-only)。")
                time.sleep(0.6)
                continue
            try:
                try:
                    df = (fred.get_series_all_releases(series_id, realtime_start=rt)
                          if rt else fred.get_series_all_releases(series_id))
                except TypeError:      # 旧版 fredapi 不接受 realtime_start —— 退回全量
                    df = fred.get_series_all_releases(series_id)
            except Exception as e:  # noqa: BLE001
                failed.append(series_id)
                print(f"[fred_alfred] 警告：{metric_key}({series_id}) 拉取失败,跳过：{e}")
                continue
            if df is None or len(df) == 0:
                print(f"[fred_alfred] 提示：{series_id} 无数据返回，跳过。")
                continue
            for _, r in df.iterrows():
                val = r.get("value")
                if val is None or pd.isna(val):
                    continue  # ALFRED 用 NaN/None 标记该版尚未发布该期
                valid = _to_date(r["date"])               # 观测期（经济日期）
                release = _to_date(r["realtime_start"])    # 发布日（何时得知）
                rows.append({
                    "metric_key": metric_key,
                    "source_id": "fred",
                    "value": float(val),
                    "unit": unit,
                    "valid_time": valid,
                    "knowledge_time": datetime(release.year, release.month, release.day,
                                               tzinfo=timezone.utc),
                    "vintage_date": release,  # ALFRED 口径：哪一版发布
                })
            print(f"[fred_alfred] {metric_key}({series_id}): 展开 {len(rows) - n_before} 个 vintage 行。")
            time.sleep(0.6)     # FRED 限速 120 req/min —— 批量拉取保持礼貌节拍


if __name__ == "__main__":
    run_id = FredAlfredConnector().run()
    print(f"✓ fred_alfred（含 vintage）已写入，ingest_run_id={run_id}")
