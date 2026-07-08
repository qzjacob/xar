"""季报事件交易引擎 —— dossier 组装、LLM 裁决、盘后回验、校准。

ET-P1 批次(本文件第一批,零 LLM):财报反应收益 / beat 习惯 / 历史波动 / 观察窗刷新。
后续批次(ET-P2 dossier / ET-P3 verdict / ET-P4 outcomes)在同文件追加。

美股专属;价格取本地 prices 优先(catalyst_returns._series 兜底 yfinance)。
"""
from __future__ import annotations

from datetime import date, timedelta

from ..logging import get_logger
from ..storage import db, structured

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
    from ..storage import kvstate

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


# ── ET-P2:季报 dossier 组装器(零 LLM)─────────────────────────────────────────────
_GUIDANCE_EVENTS = ("guidance_change", "capex_guidance", "guidance_update", "earnings")


def _revision_drift(cid: str, metric: str, period: str = "0q", days: int = 90) -> dict | None:
    """近 days 天一致预期修订漂移:最早 vs 最新 as_of 的均值变化(%)。"""
    rows = structured.estimate_series(cid, metric, period)
    if len(rows) < 2:
        return None
    cutoff = date.today() - timedelta(days=days)
    recent = [r for r in rows if r["as_of"] and r["as_of"] >= cutoff] or rows
    first, last = recent[0], recent[-1]
    v0, v1 = first.get("value"), last.get("value")
    if not v0 or v1 is None:
        return None
    return {"metric": metric, "period": period, "from": float(v0), "to": float(v1),
            "drift_pct": round((v1 / v0 - 1) * 100, 2) if v0 else None,
            "from_as_of": str(first["as_of"]), "to_as_of": str(last["as_of"]),
            "n_analysts": last.get("n_analysts")}


def _implied_series_for(cid: str, event_date) -> list[dict]:
    """本事件的 implied move 快照序列(按 meta.earnings_date 过滤),最新在前。"""
    from ..storage.altstore import series

    rows = series("alt.options_implied_move", company_id=cid, limit=30)
    return [r for r in rows if (r.get("meta") or {}).get("earnings_date") == str(event_date)]


def dossier_earnings(cid: str, event: dict) -> dict | None:
    """季报 360° dossier。返回 {text, known_ids, panel, as_of, event_date, n_facts} 或 None。
    11 节;每节独立 try/except,单节失败不沉整包(thesis.dossier 同款)。接地 id 汇入 known_ids。"""
    from ..ingestion.registry import company_by_id

    c = company_by_id(cid)
    if c is None:
        return None
    known: set[str] = set()
    parts: list[str] = []
    panel: dict = {}
    event_date = event.get("scheduled_for")
    session = (event.get("meta") or {}).get("session")
    cal_id = event.get("id")
    days_to = (event_date - date.today()).days if event_date else None

    # 1. 事件头
    hdr = {"company": c["name"], "ticker": (_ticker(cid) or ""), "event_date": str(event_date),
           "session": session or "unknown", "days_to": days_to}
    panel["event"] = hdr
    if cal_id:
        known.add(f"calendar:{cal_id}")
    parts.append(f"## 财报事件\n[calendar:{cal_id}] {c['name']} ({_ticker(cid)}) 财报日 {event_date} "
                 f"场次={session or '未知'} 距今 {days_to} 天")

    def _sect(fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            log.warning("earnings dossier %s section: %s", cid, str(e)[:120])

    # 2. 预期设定(一致预期 + 90 天修订漂移)
    def _consensus():
        est = db.query("SELECT metric, period, value, as_of FROM estimates WHERE company_id=%s "
                       "AND period IN ('0q','+1q','0y') ORDER BY as_of DESC LIMIT 8", (cid,))
        drift = [d for d in (_revision_drift(cid, m) for m in ("revenue", "eps_diluted")) if d]
        panel["consensus"] = {"latest": [dict(r) for r in est], "revision_drift": drift}
        if est or drift:
            lines = [f"[estimate:{cid}:{r['metric']}] {r['metric']} {r['period']} = {r['value']}"
                     for r in est]
            known.update(f"estimate:{cid}:{r['metric']}" for r in est)
            lines += [f"修订漂移 {d['metric']}: {d['from']}→{d['to']} ({d['drift_pct']:+}%) "
                      f"[{d['from_as_of']}→{d['to_as_of']}]" for d in drift]
            parts.append("## 预期设定(一致预期 + 90 天修订)\n" + "\n".join(lines))
    _sect(_consensus)

    # 3. beat 习惯
    def _beat():
        bs = beat_stats(cid)
        panel["beat_habit"] = bs
        if bs["n"]:
            parts.append(f"## beat 习惯\nbeat 率 {bs['beat_rate']} · 连续 beat {bs['streak']} 季 · "
                         f"平均 |surprise| {bs['avg_abs_surprise_pct']}% · 近 {bs['n']} 季 "
                         + ", ".join(f"{r['date']}:{r['surprise_pct']:+}%" for r in bs["rows"][:6]))
    _sect(_beat)

    # 4. guidance 习惯(前瞻事件 + 兑现率)
    def _guidance():
        rows = db.query(
            "SELECT event_type, polarity, resolution, count(*) n FROM kg_events "
            "WHERE company_id=%s AND event_type = ANY(%s) AND time_orientation='forward_looking' "
            "GROUP BY 1,2,3", (cid, list(_GUIDANCE_EVENTS)))
        hit = sum(r["n"] for r in rows if r["resolution"] == "hit")
        miss = sum(r["n"] for r in rows if r["resolution"] == "miss")
        panel["guidance_habit"] = {"rows": [dict(r) for r in rows], "hit": hit, "miss": miss}
        if rows:
            parts.append(f"## guidance 习惯\n前瞻指引兑现 hit={hit} miss={miss} · "
                         + ", ".join(f"{r['event_type']}({r['polarity']}):{r['n']}" for r in rows[:6]))
    _sect(_guidance)

    # 5. 评级动量(最近 2 快照 + PT vs 现价)
    def _ratings():
        rows = db.query("SELECT as_of, strong_buy, buy, hold, sell, strong_sell, pt_mean "
                        "FROM analyst_ratings WHERE company_id=%s ORDER BY as_of DESC LIMIT 2", (cid,))
        panel["ratings"] = [dict(r) for r in rows]
        if rows:
            for r in rows:
                known.add(f"ratings:{r['as_of']}")
            cur = rows[0]
            spot = db.query("SELECT close FROM prices WHERE company_id=%s ORDER BY d DESC LIMIT 1", (cid,))
            pt_gap = None
            if cur["pt_mean"] and spot:
                pt_gap = round((float(cur["pt_mean"]) / float(spot[0]["close"]) - 1) * 100, 1)
            parts.append(f"## 评级动量\n[ratings:{cur['as_of']}] buy={cur['buy']} hold={cur['hold']} "
                         f"sell={cur['sell']} PT均值={cur['pt_mean']}"
                         + (f" (距现价 {pt_gap:+}%)" if pt_gap is not None else ""))
    _sect(_ratings)

    # 6. implied vs 历史波动
    def _implied():
        ser = _implied_series_for(cid, event_date)
        hm = hist_move_stats(cid)
        panel["implied_move"] = {"series": [{"period_end": str(r["period_end"]), "value": r["value"],
                                             "meta": r["meta"]} for r in ser], "hist": hm}
        if ser:
            latest = ser[0]
            known.add(f"alt:alt.options_implied_move:{latest['period_end']}")
            imp = float(latest["value"]) * 100
            runup = None
            if len(ser) > 1:
                runup = round((float(ser[0]["value"]) - float(ser[-1]["value"])) * 100, 2)
            line = (f"[alt:alt.options_implied_move:{latest['period_end']}] 期权隐含波动 "
                    f"{imp:.1f}% (IV={latest['meta'].get('atm_iv')})")
            if hm["n"]:
                line += f" · 历史财报日平均实际 {hm['avg_abs_move_pct']}%(最大 {hm['max_abs_move_pct']}%)"
            if runup is not None:
                line += f" · 窗内 IV run-up {runup:+}pp"
            parts.append("## implied vs 历史波动\n" + line)
    _sect(_implied)

    # 7. 情绪 14d
    def _sentiment():
        sp = db.query("SELECT avg(sentiment) a, count(*) n FROM social_posts "
                      "WHERE company_id=%s AND posted_at >= now() - interval '14 days' "
                      "AND sentiment IS NOT NULL", (cid,))
        sf = db.query("SELECT polarity, count(*) n FROM semantic_facts WHERE company_id=%s "
                      "AND COALESCE(as_of, observed_at::date) >= CURRENT_DATE - 14 GROUP BY 1", (cid,))
        panel["sentiment"] = {"social_avg": (sp[0]["a"] if sp else None),
                              "social_n": (sp[0]["n"] if sp else 0),
                              "fact_polarity": {r["polarity"]: r["n"] for r in sf}}
        if (sp and sp[0]["n"]) or sf:
            avg = sp[0]["a"] if sp and sp[0]["a"] is not None else None
            parts.append(f"## 情绪(14 天)\n社媒均值={round(float(avg),3) if avg is not None else '—'} "
                         f"(n={sp[0]['n'] if sp else 0}) · 语义事实极性 "
                         + ", ".join(f"{r['polarity']}:{r['n']}" for r in sf))
    _sect(_sentiment)

    # 8. alt 快照
    def _alt():
        from .thesis_signals import signal_snapshot

        snap = signal_snapshot(cid)
        panel["alt_signals"] = snap
        if snap:
            parts.append("## alt 信号快照\n" + "\n".join(
                f"· {s['name_cn']}: z={s.get('z')} 贡献={s['contribution']}" for s in snap[:8]))
    _sect(_alt)

    # 9. 论点状态
    def _thesis():
        from . import thesis as thesis_mod
        from .thesis_health import health_v3

        t = thesis_mod.latest(cid)
        h = health_v3(cid)
        panel["thesis"] = {"stance": (t or {}).get("stance"), "conviction": (t or {}).get("conviction"),
                           "health": (h or {}).get("overall") if h else None,
                           "debates": [{"key": d.get("key"), "status": d.get("status"),
                                        "lean": d.get("lean_now")} for d in (h or {}).get("debates", [])]}
        if t:
            line = (f"论点 stance={t['stance']} conviction={t['conviction']}/5 "
                    f"one_liner={str(t.get('one_liner') or '')[:80]}")
            if h:
                line += f" · 健康={h.get('overall')}"
                for d in (h.get("debates") or [])[:3]:
                    line += f" · 争论[{d.get('key')}]={d.get('status')}(lean {d.get('lean_now')})"
            parts.append("## 长期论点状态\n" + line)
    _sect(_thesis)

    # 10. 价格语境
    def _price():
        pr = db.query("SELECT d, close FROM prices WHERE company_id=%s ORDER BY d DESC LIMIT 65", (cid,))
        panel["price"] = {"last": (float(pr[0]["close"]) if pr else None), "n": len(pr)}
        if len(pr) >= 21:
            known.add(f"price:{cid}:recent")
            last, ref = float(pr[0]["close"]), float(pr[min(20, len(pr) - 1)]["close"])
            ret20 = round((last / ref - 1) * 100, 2)
            parts.append(f"## 价格语境\n[price:{cid}:recent] 最新收盘 {last} · 近 20 交易日 {ret20:+}%")
    _sect(_price)

    # 11. 覆盖缺口(诚实声明)
    gaps = []
    if not panel.get("implied_move", {}).get("series"):
        gaps.append("无期权隐含波动数据(尚未进入观察窗或期权链缺失)")
    if not panel.get("ratings"):
        gaps.append("无卖方评级快照")
    if not (panel.get("beat_habit") or {}).get("n"):
        gaps.append("无历史 beat/surprise 数据")
    gaps.append("无买方持仓变动(13F 季度滞后)、无逐家卖方明细")
    panel["coverage_gaps"] = gaps
    parts.append("## 覆盖缺口(诚实声明)\n" + "\n".join(f"- {g}" for g in gaps))

    return {"text": "\n\n".join(parts), "known_ids": known, "panel": panel,
            "as_of": date.today().isoformat(), "event_date": str(event_date), "n_facts": len(known)}
