"""季报事件交易引擎 —— dossier 组装、LLM 裁决、盘后回验、校准。

ET-P1 批次(本文件第一批,零 LLM):财报反应收益 / beat 习惯 / 历史波动 / 观察窗刷新。
后续批次(ET-P2 dossier / ET-P3 verdict / ET-P4 outcomes)在同文件追加。

美股专属;价格取本地 prices 优先(catalyst_returns._series 兜底 yfinance)。
"""
from __future__ import annotations

from datetime import date, timedelta

from ..logging import get_logger
from ..storage import db

log = get_logger("xar.earnings")


def _ticker(cid: str) -> str | None:
    from ..ingestion.registry import company_by_id

    c = company_by_id(cid) or {}
    for t in c.get("tickers") or []:
        if "." not in t:      # 无后缀 = 美股上市
            return t
    return None


def _closes(cid: str, start: date, end: date) -> list[tuple[date, float]]:
    """[start,end] 内升序 (date, close)。本地 prices 优先,catalyst_returns._series 兜底。"""
    tkr = _ticker(cid)
    if not tkr:
        return []
    from ..backtest.catalyst_returns import _series

    rows = _series(tkr, start, end, need=2)
    out: list[tuple[date, float]] = []
    for d, c in rows:
        dd = d if isinstance(d, date) else None
        if dd is None:
            try:
                from datetime import datetime as _dt
                dd = _dt.strptime(str(d)[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
        if c is not None:
            out.append((dd, float(c)))
    return sorted(out)


def reaction_return(cid: str, event_date: date, session: str | None) -> dict | None:
    """财报反应收益(单日跳空):
    - amc(盘后)→ close(D+1)/close(D)-1,D=首个≥event_date 的交易日;
    - bmo(盘前)→ close(D)/close(D-1)-1;
    - session 缺省 → 按 amc 口径(美股多数盘后),标 inferred。
    缺价 → None。"""
    closes = _closes(cid, event_date - timedelta(days=6), event_date + timedelta(days=8))
    if len(closes) < 2:
        return None
    used = session or "amc"
    days = [d for d, _ in closes]
    px = dict(closes)
    # D = 首个 ≥ event_date 的交易日
    d0 = next((d for d in days if d >= event_date), None)
    if d0 is None:
        return None
    if used == "bmo":
        prior = [d for d in days if d < d0]
        if not prior:
            return None
        dp = prior[-1]
        reaction = px[d0] / px[dp] - 1.0
        anchor = {"d_pre": str(dp), "d_react": str(d0)}
    else:  # amc
        after = [d for d in days if d > d0]
        if not after:
            return None
        d1 = after[0]
        reaction = px[d1] / px[d0] - 1.0
        anchor = {"d_pre": str(d0), "d_react": str(d1)}
    return {"reaction_pct": round(reaction * 100, 3),
            "session": session or "inferred(amc)", **anchor}


def _occurred_earnings(cid: str, n: int) -> list[dict]:
    """近 n 次已发生财报行(带 surprise_pct 的 yahoo 行),最新在前。"""
    return db.query(
        "SELECT scheduled_for, meta FROM event_calendar "
        "WHERE company_id=%s AND event_type='earnings' AND status='occurred' "
        "AND meta ? 'surprise_pct' AND meta->>'surprise_pct' IS NOT NULL "
        "ORDER BY scheduled_for DESC LIMIT %s", (cid, n))


def beat_stats(cid: str, n: int = 8) -> dict:
    """beat 习惯:beat 率 / 连续 beat 季数 / 平均 |surprise| / 明细。"""
    rows = _occurred_earnings(cid, n)
    surprises: list[tuple[str, float]] = []
    for r in rows:
        try:
            sp = float((r["meta"] or {}).get("surprise_pct"))
        except (TypeError, ValueError):
            continue
        surprises.append((str(r["scheduled_for"]), sp))
    if not surprises:
        return {"n": 0, "beat_rate": None, "streak": 0, "avg_abs_surprise_pct": None, "rows": []}
    beats = sum(1 for _, sp in surprises if sp > 0)
    streak = 0
    for _, sp in surprises:   # 最新在前
        if sp > 0:
            streak += 1
        else:
            break
    avg_abs = sum(abs(sp) for _, sp in surprises) / len(surprises)
    return {"n": len(surprises), "beat_rate": round(beats / len(surprises), 3),
            "streak": streak, "avg_abs_surprise_pct": round(avg_abs, 3),
            "rows": [{"date": d, "surprise_pct": sp} for d, sp in surprises]}


def hist_move_stats(cid: str, n: int = 8) -> dict:
    """历史财报日 |反应| 均值/最大/明细(用 meta.session 口径)。"""
    rows = _occurred_earnings(cid, n)
    moves: list[tuple[str, float]] = []
    for r in rows:
        d = r["scheduled_for"]
        sess = (r["meta"] or {}).get("session")
        rr = reaction_return(cid, d, sess)
        if rr is not None:
            moves.append((str(d), abs(rr["reaction_pct"])))
    if not moves:
        return {"n": 0, "avg_abs_move_pct": None, "max_abs_move_pct": None, "rows": []}
    return {"n": len(moves),
            "avg_abs_move_pct": round(sum(m for _, m in moves) / len(moves), 3),
            "max_abs_move_pct": round(max(m for _, m in moves), 3),
            "rows": [{"date": d, "abs_move_pct": m} for d, m in moves]}


def refresh_window() -> dict:
    """每日刷新(worker 6h 节拍):
    ① 轮转游标全 universe yahoo.pull_calendar —— 保财报日历 + 历史 surprise 新鲜;
    ② 观察窗内名字:yahoo.pull_analyst(estimates/ratings 每日快照 → 自建修订史)+ implied_move。
    单司容错;返回统计。"""
    from ..config import get_settings
    from ..ingestion import alt as alt_ing
    from ..ontology.earnings_events import EARNINGS_UNIVERSE
    from ..providers import yahoo
    from ..storage import kvstate, structured

    s = get_settings()
    uni = list(EARNINGS_UNIVERSE)
    st = kvstate.get_state("earnings_watch")
    cur = int(st.get("cursor", 0)) % max(len(uni), 1)
    # ① 日历刷新:每轮一片(轮转),覆盖全 universe
    slice_n = max(1, len(uni) // 4)
    cal_slice = uni[cur:cur + slice_n] or uni[:slice_n]
    cal_ok = 0
    for cid in cal_slice:
        try:
            yahoo.pull_calendar(cid)
            cal_ok += 1
        except Exception as e:  # noqa: BLE001
            log.warning("earnings calendar %s: %s", cid, str(e)[:120])
    st["cursor"] = (cur + slice_n) % max(len(uni), 1)
    kvstate.save_state("earnings_watch", st)
    # ② 观察窗内名字:analyst 快照
    in_window = {r["company_id"] for r in
                 structured.upcoming_calendar(uni, days=s.earnings_watch_days, limit=200)
                 if r.get("event_type") == "earnings"}
    analyst_ok = 0
    for cid in in_window:
        try:
            yahoo.pull_analyst(cid)
            analyst_ok += 1
        except Exception as e:  # noqa: BLE001
            log.warning("earnings analyst %s: %s", cid, str(e)[:120])
    # ③ implied move 快照(provider 自己按窗口筛)
    im = alt_ing.pull_source("implied_move")
    return {"scanned": len(cal_slice), "cal_ok": cal_ok, "in_window": len(in_window),
            "analyst_ok": analyst_ok, "implied": im}
