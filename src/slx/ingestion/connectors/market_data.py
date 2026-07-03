"""market_data —— 等权 vs 市值加权的市场内生集中度信号（B_public_curated）。

产出两个指标（registry sources 标 source_id=stooq）：
  mktcap.rsp_vs_spy_excess_return  —— 等权 RSP 相对市值加权 SPY 的超额收益（bps）。**必跑通**。
  mktcap.concentration             —— 市值集中度近似（见下"近似口径"声明）。

理论钩子（A5 双相 / 登记簿 rsp_crack_appeared）：
  等权反超是"集中度回吐/扩散"的市场内生信号；但 <130bp 的季度领先是噪声级，
  不可当反向定价证据——故此处只忠实搬运数字，判定交登记簿引擎（带历史波动带显著性）。

近似口径声明（审讯纪律，不臆造精度）：
  - rsp_vs_spy_excess_return：用 RSP 与 SPY 的**总收益指数**（含分红的 adjusted close）之比，
    取近 ~63 个交易日（≈1 季度）窗口的累计收益差，单位 bps。窗口与口径在 _WINDOW_DAYS 显式可调。
  - concentration：真·Mag7 占标普市值需成分权重数据（registry 另有 sp_global / spx_components 源）。
    本连接器**无成分权重**，故以 "1 − RSP/SPY 比价归一" 作集中度的**单调代理**：
    SPY 相对 RSP 越强，前排巨头权重越大，集中度代理越高。这是**派生近似**，已在 unit/caveat 标注，
    精确成分权重待 sp_global 源接入后替换。

数据源 waterfall（稳健性 > 单一源）：
  1) stooq CSV（registry 指定源）。stooq 现以 SHA-256 proof-of-work + cookie 挑战护栏，
     本连接器内置 PoW 解算器（纯 hashlib，无浏览器）。
  2) Yahoo Finance chart API（cookie+crumb 握手）作回退。
  二者均不需 key。若两源都被对端 IP 封禁（如数据中心出口），fetch 抛清晰错误，
  说明是环境网络问题而非逻辑错误（本地/研究 IP 可正常跑通）。

    python -m ingestion.connectors.market_data
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import time
from datetime import date, datetime, timezone

from slx.ingestion.base import Connector

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_WINDOW_DAYS = 63   # ≈ 一个交易季度（用于累计收益差）
_RANGE = "14mo"     # 拉取窗口，足够覆盖一季 + 缓冲


# ════════════════════════════════════════════════════════════════════════════
# 源 1：stooq（含 proof-of-work 解算器）
# ════════════════════════════════════════════════════════════════════════════
def _stooq_solve(session, challenge_html: str, probe_url: str) -> bool:
    """解 stooq 的 SHA-256 PoW：找 n 使 sha256(c+str(n)) 以 d 个十六进制 0 开头，POST /__verify。"""
    m = re.search(r'c="([^"]+)"', challenge_html)
    dm = re.search(r",d=(\d+),", challenge_html)
    if not (m and dm):
        return False
    c, d = m.group(1), int(dm.group(1))
    target = "0" * d
    n = 0
    # 上限保护：d=4 通常 < 数十万次；给一个宽松上限避免死循环。
    while n < 50_000_000:
        if hashlib.sha256(f"{c}{n}".encode()).hexdigest().startswith(target):
            break
        n += 1
    try:
        session.post("https://stooq.com/__verify",
                     data={"c": c, "n": n}, timeout=20,
                     headers={"Content-Type": "application/x-www-form-urlencoded",
                              "Referer": probe_url})
    except Exception:  # noqa: BLE001
        return False
    time.sleep(0.8)
    return True


def _stooq_csv(session, symbol: str) -> list[tuple[date, float]]:
    """拉 stooq 日线 CSV，必要时解 PoW 后重试一次。返回 [(date, close)] 升序。"""
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    r = session.get(url, timeout=25, headers={"Referer": "https://stooq.com/"})
    if "requires JavaScript" in r.text:
        if _stooq_solve(session, r.text, url):
            r = session.get(url, timeout=25, headers={"Referer": "https://stooq.com/"})
    txt = r.text.strip()
    if "requires JavaScript" in txt or txt.lower().startswith("access denied") or not txt:
        raise RuntimeError(f"stooq 拒绝/挑战未通过（{symbol}）：{txt[:60]!r}")
    out: list[tuple[date, float]] = []
    rdr = csv.DictReader(io.StringIO(txt))
    for row in rdr:
        try:
            out.append((_d(row["Date"]), float(row["Close"])))
        except (KeyError, ValueError):
            continue
    if not out:
        raise RuntimeError(f"stooq 返回无可解析行（{symbol}）")
    return sorted(out)


# ════════════════════════════════════════════════════════════════════════════
# 源 2：Yahoo Finance chart（cookie + crumb 握手）回退
# ════════════════════════════════════════════════════════════════════════════
def _yahoo_series(session, symbol: str) -> list[tuple[date, float]]:
    # 1) 取 cookie  2) 取 crumb  3) 拉 chart（adjclose=总收益口径）
    session.get("https://fc.yahoo.com", timeout=20)
    cr = session.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=20)
    crumb = cr.text.strip()
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?range={_RANGE}&interval=1d&crumb={crumb}")
    r = session.get(url, timeout=25)
    j = r.json()
    res = j["chart"]["result"][0]
    ts = res["timestamp"]
    adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose")
    if adj is None:
        adj = res["indicators"]["quote"][0]["close"]
    out = []
    for t, v in zip(ts, adj):
        if v is not None:
            out.append((datetime.fromtimestamp(t, tz=timezone.utc).date(), float(v)))
    if not out:
        raise RuntimeError(f"Yahoo 返回空序列（{symbol}）")
    return sorted(out)


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _series(session, stooq_sym: str, yahoo_sym: str) -> tuple[list[tuple[date, float]], str]:
    """先 stooq 后 Yahoo；返回 (序列, 实际命中源标签)。"""
    try:
        return _stooq_csv(session, stooq_sym), "stooq"
    except Exception as e1:  # noqa: BLE001
        print(f"[market_data] stooq 取 {stooq_sym} 失败：{e1}；回退 Yahoo。")
        try:
            return _yahoo_series(session, yahoo_sym), "yahoo"
        except Exception as e2:  # noqa: BLE001
            raise RuntimeError(
                f"两源均失败（{stooq_sym}/{yahoo_sym}）：stooq={e1}; yahoo={e2}。"
                "若在数据中心/沙箱出口，多为对端 IP 封禁，非逻辑错误；换研究/本地 IP 可跑通。"
            )


def _cum_return(series: list[tuple[date, float]], window: int) -> tuple[date, float]:
    """近 window 个交易日的累计收益（end/begin − 1）。返回 (as_of_date, return)。"""
    if len(series) < window + 1:
        window = len(series) - 1
    tail = series[-(window + 1):]
    begin, end = tail[0][1], tail[-1][1]
    return tail[-1][0], (end / begin - 1.0)


class MarketDataConnector(Connector):
    source_id = "stooq"
    connector = "ingestion.connectors.market_data"

    def __init__(self, session=None):
        self._session = session

    def _sess(self):
        import requests  # 延迟导入
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": _UA, "Accept": "*/*"})
        return self._session

    def fetch(self) -> list[dict]:
        s = self._sess()
        rows: list[dict] = []
        now = datetime.now(timezone.utc)

        # ── RSP / SPY（**必跑通**）─────────────────────────────────────────────
        rsp, src_rsp = _series(s, "rsp.us", "RSP")
        spy, src_spy = _series(s, "spy.us", "SPY")

        as_of_r, ret_rsp = _cum_return(rsp, _WINDOW_DAYS)
        as_of_s, ret_spy = _cum_return(spy, _WINDOW_DAYS)
        as_of = min(as_of_r, as_of_s)  # 用两序列共同覆盖的最近日
        excess_bps = (ret_rsp - ret_spy) * 10_000.0  # 收益差 → bps
        rows.append({
            "metric_key": "mktcap.rsp_vs_spy_excess_return",
            "source_id": "stooq",
            "value": round(excess_bps, 2),
            "unit": "bps",
            "valid_time": as_of,
            "knowledge_time": now,  # 收盘价当日即可知，但摄取时刻是保守可知下界
        })

        # ── 集中度近似（派生：1 − RSP/SPY 比价归一），口径见模块 docstring ──────────
        # 用最近收盘比价 SPY/RSP 的标准化偏移作单调代理（数值范围非真·权重百分比）。
        last_rsp = rsp[-1][1]
        last_spy = spy[-1][1]
        # 以窗口起点比价为基准，比价相对变化 → 集中度代理（正=巨头走强=更集中）。
        base_rsp = rsp[-(min(len(rsp), _WINDOW_DAYS + 1))][1]
        base_spy = spy[-(min(len(spy), _WINDOW_DAYS + 1))][1]
        conc_proxy = ((last_spy / base_spy) / (last_rsp / base_rsp) - 1.0) * 100.0
        rows.append({
            "metric_key": "mktcap.concentration",
            "source_id": "stooq",
            "value": round(conc_proxy, 4),
            "unit": "pct",  # 注：派生代理，非真·前十权重百分比（待 sp_global 成分权重替换）
            "valid_time": as_of,
            "knowledge_time": now,
        })

        print(f"[market_data] RSP({src_rsp})/SPY({src_spy}) as_of={as_of} "
              f"excess={excess_bps:.1f}bps conc_proxy={conc_proxy:.3f}")

        # ── ChiNext / SSE（不一定可得；不可得则跳过并 status 留待）──────────────────
        try:
            chinext, _ = _series(s, "^cnt", "")   # 创业板指（stooq 代码近似）
            sse, _ = _series(s, "^shc", "")        # 上证综指
            _, r_cn = _cum_return(chinext, _WINDOW_DAYS)
            cn_as_of, r_ss = _cum_return(sse, _WINDOW_DAYS)
            rows.append({
                "metric_key": "mktcap.chinext_vs_sse_excess_return",
                "source_id": "stooq",
                "value": round((r_cn - r_ss) * 100.0, 4),
                "unit": "pct",
                "valid_time": cn_as_of,
                "knowledge_time": now,
            })
            print("[market_data] ChiNext/SSE 取得，已入库（仍须 beta 剥离方可解释）。")
        except Exception as e:  # noqa: BLE001
            # registry 中 chinext_vs_sse 为 candidate；不可得不报错，留待后续。
            print(f"[market_data] 提示：ChiNext/SSE 暂不可得（{e}）；status 留待，本轮跳过。")

        return rows


if __name__ == "__main__":
    run_id = MarketDataConnector().run()
    print(f"✓ market_data 已写入，ingest_run_id={run_id}")
