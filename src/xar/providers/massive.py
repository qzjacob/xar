"""Massive(massive.com,Polygon 兼容)连接器 —— 资金流数据面(XAR 侧薄客户端)。

与 Fenny 期权栈的 fcn/marketdata/massive.py 同源同 host,但两套栈刻意不跨 import:
这里只做资金流需要的三件事,全部经 base.get_json(礼貌限速/重试/**绝不泄 key**,
失败返 None 不上抛)——单名失败不沉整轮,无 key 整体跳过(arm-if-available):

  1. pull_etf_prices — 大类/风格 ETF 日线 → prices(source='massive')
     (FMP 低档位 402 gate ETF,ETF 日线必须走这里;company_id=NULL 合法)。
  2. short_interest  — FINRA 双周空头持仓(/stocks/v1/short-interest,Polygon 兼容;
     未 entitle 时该端点返 4xx → None → 上层降级显示"未接入")。
  3. pc_snapshot     — 期权链快照聚合 Put/Call(近月窗口;volume 缺失退回 OI)。

写库职责在 research/flow.py(信号统一经 altstore);本模块只取数与解析。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ..config import get_settings
from ..storage import structured
from .base import get_json, log

_BASE = "https://api.massive.com"
_HOST = "api.massive.com"


def available() -> bool:
    return bool(get_settings().massive_api_key)


def _get(path: str, params: dict | None = None):
    """Bearer 认证 GET;任何失败(4xx/网络)→ None(get_json 已记日志且不含 key)。"""
    headers = {"Authorization": f"Bearer {get_settings().massive_api_key}"}
    return get_json(f"{_BASE}/{path}", params=params, headers=headers, host=_HOST)


# ── 1) ETF 日线 → prices ───────────────────────────────────────────────────────
def pull_etf_prices(tickers: tuple[str, ...] | None = None, days: int = 400) -> dict:
    """flow 宇宙(资产篮 ∪ 风格对两腿)日线聚合入库。返回 {tickers, bars, failed[]}。"""
    if not available():
        return {"skipped": "no MASSIVE_API_KEY"}
    from ..ontology.flow import FLOW_ETF_UNIVERSE

    end = date.today()
    start = end - timedelta(days=days)
    ok, total, failed = 0, 0, []
    for t in tickers or FLOW_ETF_UNIVERSE:
        js = _get(f"v2/aggs/ticker/{t}/range/1/day/{start}/{end}",
                  {"adjusted": "true", "sort": "asc", "limit": 50000})
        rows = (js or {}).get("results") or []
        if not rows:
            failed.append(t)
            continue
        bars = [{"d": datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc).date(),
                 "open": r.get("o"), "high": r.get("h"), "low": r.get("l"),
                 "close": r.get("c"), "volume": r.get("v")} for r in rows]
        total += structured.upsert_prices(None, t, bars, source="massive")
        ok += 1
    out = {"tickers": ok, "bars": total}
    if failed:
        out["failed"] = failed
    return out


# ── 2) 空头持仓(FINRA 双周,arm-if-available)─────────────────────────────────
def short_interest(ticker: str, limit: int = 12) -> list[dict]:
    """按 settlement_date 倒序的空头持仓行(短历史随行返回,z 计算即刻可用)。
    未 entitle / 端点不存在 → [](上层据此显示"未接入")。"""
    js = _get("stocks/v1/short-interest",
              {"ticker": ticker, "limit": limit, "sort": "settlement_date.desc"})
    out = []
    for r in (js or {}).get("results") or []:
        d = r.get("settlement_date")
        si = r.get("short_interest")
        if d is None or si is None:
            continue
        adv = r.get("avg_daily_volume")
        dtc = r.get("days_to_cover")
        if dtc is None and adv:
            dtc = float(si) / float(adv)
        out.append({"settlement_date": d, "short_interest": float(si),
                    "avg_daily_volume": adv, "days_to_cover": dtc})
    return out


# ── 3) 期权 Put/Call 快照 ──────────────────────────────────────────────────────
def pc_snapshot(ticker: str, window_days: int = 30) -> dict | None:
    """近月 ±10% 平值带期权链的 Put/Call 比。volume 口径优先(日内活跃度,两侧都要有量
    才可信),退回 open_interest 口径;两者皆缺 → None。

    两条采样纪律(真机捕获):① 必须加 strike 括号——不加时结果按期权符号排序,
    call 整块在 put 前,首页样本几乎全 call,P/C 失真到 0.05;拿不到现货价就放弃,
    绝不退回无括号采样。② 必须跟 next_url 分页——SPY 平值带 × 30 天窗仍超单页
    250 张,单页会把 put 块截掉。"""
    today = date.today()
    first = _get(f"v3/snapshot/options/{ticker}", {"limit": 1})
    spot = (((first or {}).get("results") or [{}])[0].get("underlying_asset") or {}).get("price")
    if not spot:
        return None
    params = {"expiration_date.gte": today.isoformat(),
              "expiration_date.lte": (today + timedelta(days=window_days)).isoformat(),
              "strike_price.gte": round(0.9 * float(spot), 2),
              "strike_price.lte": round(1.1 * float(spot), 2),
              "limit": 250}
    vol = {"put": 0.0, "call": 0.0}
    oi = {"put": 0.0, "call": 0.0}
    n = 0
    js = _get(f"v3/snapshot/options/{ticker}", params)
    for _page in range(6):                       # 分页帽:6 × 250 = 1500 张,平值带足够
        rows = (js or {}).get("results") or []
        n += len(rows)
        for r in rows:
            ctype = (r.get("details") or {}).get("contract_type")
            if ctype not in ("put", "call"):
                continue
            v = (r.get("day") or {}).get("volume")
            if v:
                vol[ctype] += float(v)
            o = r.get("open_interest")
            if o:
                oi[ctype] += float(o)
        nxt = (js or {}).get("next_url")
        if not nxt:
            break
        headers = {"Authorization": f"Bearer {get_settings().massive_api_key}"}
        js = get_json(nxt, headers=headers, host=_HOST)
    for basis, agg in (("volume", vol), ("oi", oi)):
        if agg["call"] > 0 and agg["put"] > 0:   # 两侧都有数据才选该口径(单边0=数据残缺)
            return {"ticker": ticker, "pc": round(agg["put"] / agg["call"], 4),
                    "basis": basis, "contracts": n}
    if n:
        log.warning("massive pc_snapshot %s: %d contracts but one-sided volume/OI", ticker, n)
    return None
