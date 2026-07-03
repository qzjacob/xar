"""BLS —— 分裂 CPI + 技工工资（A_official；v1 公共 API 无需 key）。

产出（registry sources 标 source_id=bls）：
  price.split_cpi   ← CPI 分项派生"认知通缩 vs 原子/执行通胀"的同比差（pct_yoy）。
  price.trades_wage ← OES 电工/水管工工资增速（pct_yoy）。

口径与分组（审讯纪律，分组是研究判断、已透明）：
  - split_cpi：官方 CPI 分类不对应"认知 vs 执行"框架，须自建映射（registry caveat 明示）。
    认知/软件相篮子（趋通缩）：信息技术商品与服务（CPI 'Information technology, hardware and services'）。
    原子/执行相篮子（趋通胀）：服务中的人工执行项（此处取 'Services less energy services'）。
    split_cpi = YoY(原子篮子) − YoY(认知篮子)；正且扩大 = 价格双速撕裂成立。
    本连接器产出每个篮子的 YoY 与其差值（差值即 metric）。
  - trades_wage：BLS v1 时序 API 不直接给 OES 职业年薪面板（OES 是横截面年度，另有专表）。
    v1 可得的工资代理：ECI / 行业平均时薪。此处用 CES 公用事业&建筑业平均时薪同比作技工工资代理，
    并显式标注为代理（OES 电工/水管工精确口径需 OES flat files，列为待办）。

BLS v1 契约：POST https://api.bls.gov/publicAPI/v1/timeseries/data/
  body {"seriesid":[...], "startyear":"YYYY", "endyear":"YYYY"}
  返回 {"status":"REQUEST_SUCCEEDED","Results":{"series":[{"seriesID","data":[{year,period,value}]}]}}
  v1 限额：每请求 ≤25 序列、≤10 年、每日 ≤25 次（无 key）。

    python -m ingestion.connectors.bls_oes
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from slx.ingestion.base import Connector

_BLS_V1 = "https://api.bls.gov/publicAPI/v1/timeseries/data/"

# CPI 篮子映射（CU = CPI-U, 季调外 NSA 'CUUR...'）。系列号取自 BLS series 目录。
COGNITIVE_CPI = "CUUR0000SEEE"   # Information technology, hardware and services（认知/软件相代理）
ATOMIC_CPI = "CUUR0000SASLE"     # Services less energy services（人工执行相代理）

# 技工工资代理（CES 平均时薪，季调 'CES...'）。
TRADES_WAGE = "CES2000000003"    # Construction: average hourly earnings of all employees（技工代理）

_MONTH = {f"M{m:02d}": m for m in range(1, 13)}


def _period_end(year: int, period: str) -> date | None:
    """BLS period 'M01'..'M12' → 月末；'Q01'..'Q04' / 'A01' 等非月度返回 None（本连接器只用月度）。"""
    m = _MONTH.get(period)
    if not m:
        return None
    nm = date(year + (m == 12), (m % 12) + 1, 1)
    return date.fromordinal(nm.toordinal() - 1)


class BlsOesConnector(Connector):
    source_id = "bls"
    connector = "ingestion.connectors.bls_oes"

    def __init__(self, start_year: int = 2022):
        self._start = start_year
        self._end = datetime.now(timezone.utc).year

    def _query(self, series_ids: list[str]) -> dict[str, list[dict]]:
        import requests
        payload = {"seriesid": series_ids,
                   "startyear": str(self._start), "endyear": str(self._end)}
        last = None
        for i in range(3):
            try:
                r = requests.post(_BLS_V1, json=payload, timeout=40,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "Silicon-Index research"})
                r.raise_for_status()
                j = r.json()
                if j.get("status") != "REQUEST_SUCCEEDED":
                    raise RuntimeError(f"BLS 拒绝：{j.get('status')} {j.get('message')}")
                out: dict[str, list[dict]] = {}
                for s in j.get("Results", {}).get("series", []):
                    out[s["seriesID"]] = s.get("data", [])
                return out
            except Exception as e:  # noqa: BLE001
                last = e
                import time
                time.sleep(1.0 * (i + 1))
        raise last

    @staticmethod
    def _monthly_map(data: list[dict]) -> dict[tuple[int, int], float]:
        """series data → {(year, month): value}（仅月度点）。"""
        out: dict[tuple[int, int], float] = {}
        for d in data:
            try:
                m = _MONTH.get(d["period"])
                if m is None:
                    continue
                out[(int(d["year"]), m)] = float(d["value"])
            except (KeyError, ValueError):
                continue
        return out

    @classmethod
    def _yoy_rows(cls, data: list[dict], metric_key: str, now) -> list[dict]:
        """把月度水平序列转成 YoY% 行。"""
        mm = cls._monthly_map(data)
        rows = []
        for (y, m), v in sorted(mm.items()):
            prev = mm.get((y - 1, m))
            if prev is None or prev == 0:
                continue
            yoy = (v / prev - 1.0) * 100.0
            vt = _period_end(y, f"M{m:02d}")
            rows.append({
                "metric_key": metric_key, "source_id": "bls",
                "value": round(yoy, 4), "unit": "pct_yoy",
                "valid_time": vt, "knowledge_time": now,
                "_y": y, "_m": m, "_yoy": yoy,  # 临时携带，供 split 计算后剔除
            })
        return rows

    def fetch(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        data = self._query([COGNITIVE_CPI, ATOMIC_CPI, TRADES_WAGE])

        rows: list[dict] = []

        # ── price.split_cpi = YoY(原子) − YoY(认知)，逐月对齐 ─────────────────────
        cog = {(r["_y"], r["_m"]): r["_yoy"]
               for r in self._yoy_rows(data.get(COGNITIVE_CPI, []), "price.split_cpi", now)}
        ato = {(r["_y"], r["_m"]): r["_yoy"]
               for r in self._yoy_rows(data.get(ATOMIC_CPI, []), "price.split_cpi", now)}
        for key in sorted(set(cog) & set(ato)):
            y, m = key
            split = ato[key] - cog[key]  # 正=执行通胀跑赢认知通缩=撕裂
            rows.append({
                "metric_key": "price.split_cpi", "source_id": "bls",
                "value": round(split, 4), "unit": "pct_yoy",
                "valid_time": _period_end(y, f"M{m:02d}"), "knowledge_time": now,
            })

        # ── price.trades_wage = YoY(技工时薪代理)─────────────────────────────────
        for r in self._yoy_rows(data.get(TRADES_WAGE, []), "price.trades_wage", now):
            rows.append({k: v for k, v in r.items() if not k.startswith("_")})

        if not rows:
            raise RuntimeError(
                "[bls_oes] 无可产出行——BLS v1 可能限流（每日 25 次）或序列号失效，请稍后重试。"
            )
        print(f"[bls_oes] split_cpi {sum(1 for r in rows if r['metric_key']=='price.split_cpi')} 行，"
              f"trades_wage {sum(1 for r in rows if r['metric_key']=='price.trades_wage')} 行。")
        return rows


if __name__ == "__main__":
    run_id = BlsOesConnector().run()
    print(f"✓ bls_oes 已写入，ingest_run_id={run_id}")
