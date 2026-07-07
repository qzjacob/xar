"""期权隐含波动(季报事件)追踪器 —— 窗口内 universe 名字的 ATM straddle 快照 → alt_signals。

写 ``alt.options_implied_move``(company-scope, daily)。value = ATM straddle 中价 / 现价
= 期权市场对本次财报隐含的股价波动幅度。
**period_end = 快照日(观察日)**,不是财报日 —— IV 是当日可观察量,经济期=观察日语义正确,
且与 alt_signals 唯一键天然不撞;meta.earnings_date 是事件锚,dossier 按它取本事件 IV run-up 序列。

免费主路 yfinance(`Ticker.option_chain`);`massive_api_key` armed 时可切 fcn 栈真 IV。
只打观察窗内(≤ earnings_watch_days)的 EARNINGS_UNIVERSE 名字,模块级 1.5s 节流,单司容错。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from ...config import get_settings
from ...storage.altstore import upsert_signal
from ..base import log

_KEY = "alt.options_implied_move"
_RATE_MIN_INTERVAL = 1.5   # 模块级节流(finnhub._paced_get 同款)
_last_call = [0.0]


def available() -> bool:
    """yfinance 可导入即可(纯依赖探测,house 惯例);无网络时 pull 逐司容错。"""
    try:
        import yfinance  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _pace() -> None:
    dt = time.monotonic() - _last_call[0]
    if dt < _RATE_MIN_INTERVAL:
        time.sleep(_RATE_MIN_INTERVAL - dt)
    _last_call[0] = time.monotonic()


def _parse_expiry(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _pick_expiry(expiries, reaction_day: date) -> str | None:
    """首个 ≥ 财报反应日的 expiry(捕获财报周的定价);无则取最后一个(最近的更长月)。"""
    valid = [(e, _parse_expiry(e)) for e in (expiries or [])]
    valid = [(e, d) for e, d in valid if d is not None]
    if not valid:
        return None
    after = [e for e, d in valid if d >= reaction_day]
    return after[0] if after else valid[-1][0]


def _mid(bid, ask, last) -> float | None:
    """bid/ask 中价;任一为零/缺失回落 lastPrice;都无 → None。"""
    try:
        b, a = float(bid or 0), float(ask or 0)
    except (TypeError, ValueError):
        b = a = 0.0
    if b > 0 and a > 0:
        return (b + a) / 2
    try:
        return float(last) if last and float(last) > 0 else None
    except (TypeError, ValueError):
        return None


def _atm_straddle(chain, spot: float) -> tuple[float, float] | None:
    """ATM(|strike-spot| 最小)call+put 的中价合计与 atm_iv;任一腿无价 → None。"""
    calls, puts = getattr(chain, "calls", None), getattr(chain, "puts", None)
    if calls is None or puts is None or getattr(calls, "empty", True) or getattr(puts, "empty", True):
        return None

    def _row_at_atm(df):
        best, bestgap = None, None
        for _, r in df.iterrows():
            try:
                gap = abs(float(r.get("strike")) - spot)
            except (TypeError, ValueError):
                continue
            if bestgap is None or gap < bestgap:
                best, bestgap = r, gap
        return best

    cr, pr = _row_at_atm(calls), _row_at_atm(puts)
    if cr is None or pr is None:
        return None
    cm = _mid(cr.get("bid"), cr.get("ask"), cr.get("lastPrice"))
    pm = _mid(pr.get("bid"), pr.get("ask"), pr.get("lastPrice"))
    if cm is None or pm is None:
        return None
    ivs = [float(x) for x in (cr.get("impliedVolatility"), pr.get("impliedVolatility"))
           if x not in (None, "") and float(x) > 0]
    atm_iv = sum(ivs) / len(ivs) if ivs else None
    return cm + pm, (atm_iv or 0.0)


def _window_names() -> list[tuple[str, str, date, str | None]]:
    """观察窗内 (cid, ticker, earnings_date, session) —— upcoming earnings ∩ EARNINGS_UNIVERSE。"""
    from ...ontology.altdata import binding_for
    from ...ontology.earnings_events import EARNINGS_UNIVERSE
    from ...storage import structured

    s = get_settings()
    rows = structured.upcoming_calendar(list(EARNINGS_UNIVERSE), days=s.earnings_watch_days, limit=200)
    out = []
    for r in rows:
        if r.get("event_type") != "earnings":
            continue
        b = binding_for(r["company_id"])
        tkr = b.options_ticker if b else None
        if not tkr:
            continue
        out.append((r["company_id"], tkr, r["scheduled_for"], (r.get("meta") or {}).get("session")))
    return out


def pull(limit: int | None = None) -> dict:
    """观察窗内 universe 名字逐个:ATM straddle 快照 → alt.options_implied_move(period_end=今天)。
    limit = 本轮最多处理公司数(None=全量)。返回统计。"""
    if not available():
        return {"skipped": "yfinance not installed"}
    from ..yahoo import _handle

    names = _window_names()
    if limit:
        names = names[:limit]
    today = date.today()
    written, skipped = 0, []
    for cid, tkr, edate, session in names:
        reaction_day = edate + timedelta(days=1) if session == "amc" else edate
        try:
            _pace()
            sym, tk = _handle(cid, None)
            if tk is None:
                skipped.append(cid)
                continue
            exp = _pick_expiry(tk.options, reaction_day)
            if not exp:
                skipped.append(cid)
                continue
            spot = None
            try:
                spot = float(tk.fast_info["last_price"])
            except Exception:  # noqa: BLE001
                spot = None
            if not spot or spot <= 0:
                skipped.append(cid)
                continue
            st = _atm_straddle(tk.option_chain(exp), spot)
            if st is None:
                skipped.append(cid)
                continue
            straddle, atm_iv = st
            upsert_signal(_KEY, company_id=cid, period_end=today, value=straddle / spot,
                          unit="ratio", source="implied_move",
                          meta={"earnings_date": str(edate), "expiry": exp, "spot": round(spot, 4),
                                "atm_iv": round(atm_iv, 4), "straddle_mid": round(straddle, 4),
                                "dte": (_parse_expiry(exp) - today).days})
            written += 1
        except Exception as e:  # noqa: BLE001 — 单司容错,不沉整轮
            log.warning("implied_move %s: %s", cid, str(e)[:120])
            skipped.append(cid)
    out = {"names": len(names), "written": written, "skipped": len(skipped)}
    log.info("implied_move: %s", out)
    return out
