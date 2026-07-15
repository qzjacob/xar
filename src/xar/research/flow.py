"""资金流计算引擎(零 LLM,纯统计)—— 宏观大类 → 风格 → 主题 → 个股的统一刻度。

流水:providers/massive.pull_etf_prices 落 prices → 本模块从 prices 读序列,算
OBV 累积/美元成交额/动量/风格对相对强弱/risk-on 综合,连同空头持仓(Massive,
arm-if-available)、期权 P/C、13F 机构持仓Δ、主题聚合净分,统一经 altstore 写
alt_signals(flow.* 命名空间;market/style 行身份编进 theme 列 etf:*/pair:*,
见 ontology/flow.py)→ |z| 越阈的新期信号幂等同步 kg_events(flow_signal)。

读接口 flow_snapshot(scope) 供 /api/andy/flow、Genny flow 块与 Chathy
capital_flow 工具共用;as_of 经 altstore.series 的 observed_at 谓词实现 PIT。

统计口径与 thesis_signals 一致(可审计,拒绝黑箱):z 按 (latest-mean)/std,
clip ±3;写库侧的滚动 z 用 20 日增量对 120 日历史;分数类信号归一 [-1,1]。
"""
from __future__ import annotations

import json
import statistics
from datetime import date, timedelta

from ..logging import get_logger
from ..ontology.flow import (
    ASSET_ETFS,
    FLOW_BY_KEY,
    RISK_OFF_TICKERS,
    RISK_ON_TICKERS,
    STYLE_PAIRS,
    etf_theme,
    pair_theme,
)
from ..storage import altstore, db
from .thesis_signals import _zscore

log = get_logger("xar.flow")

_SOURCE_ORDER = ("massive", "fmp", "yahoo", "polygon", "futu")  # per-ticker 单一源,不混
_HIST = 120        # 滚动 z 的历史窗(交易日)
_SHORT = 20        # 短窗(OBV 增量/成交额均值/比值斜率)
_MOM = 63          # 动量窗(一季度)
_BACKFILL = 90     # 每轮回写的日行数(幂等 upsert;首轮即给足读端 z/火花线历史)
_SI_SLICE = 25     # 每轮空头持仓拉取的公司数(游标轮转)
_PC_COMPANIES = 12  # 个股期权 P/C 覆盖数
_THEME_QUANT_CAP = 20  # 主题聚合中做量价计算的美股成员上限


# ── 序列数学 ──────────────────────────────────────────────────────────────────
def _roll_z(series: list[float], i: int, window: int = _HIST) -> float | None:
    """series[i] 对其前 window 个值的 z(clip ±3);历史不足 30 点不计。"""
    hist = series[max(0, i - window):i]
    if len(hist) < 30:
        return None
    mean = statistics.fmean(hist)
    stdev = statistics.pstdev(hist)
    if stdev == 0:
        return 0.0
    return max(-3.0, min(3.0, (series[i] - mean) / stdev))


def _bars(ticker: str, days: int = 460) -> list[dict]:
    """单一源日线(升序 d/close/volume)。源按优先序取首个行数≥60 的,避免混源。"""
    counts = {r["source"]: r["n"] for r in db.query(
        "SELECT source, count(*) n FROM prices WHERE ticker=%s AND d >= %s GROUP BY source",
        (ticker, date.today() - timedelta(days=days)))}
    src = next((s for s in _SOURCE_ORDER if counts.get(s, 0) >= 60), None)
    if src is None and counts:
        src = max(counts, key=lambda s: counts[s])
    if src is None:
        return []
    return db.query(
        "SELECT d, close, volume FROM prices WHERE ticker=%s AND source=%s AND d >= %s "
        "AND close IS NOT NULL ORDER BY d ASC",
        (ticker, src, date.today() - timedelta(days=days)))


def _obv_delta(bars: list[dict]) -> list[float]:
    """OBV 累积序列的 _SHORT 日增量(与 bars[_SHORT:] 对齐)。"""
    obv = [0.0]
    for i in range(1, len(bars)):
        v = float(bars[i]["volume"] or 0)
        sign = (bars[i]["close"] > bars[i - 1]["close"]) - (bars[i]["close"] < bars[i - 1]["close"])
        obv.append(obv[-1] + sign * v)
    return [obv[i] - obv[i - _SHORT] for i in range(_SHORT, len(obv))]


def _dollar_ma(bars: list[dict]) -> list[float]:
    """_SHORT 日均美元成交额(与 bars[_SHORT-1:] 对齐)。"""
    dv = [float(b["close"]) * float(b["volume"] or 0) for b in bars]
    return [statistics.fmean(dv[i - _SHORT + 1:i + 1]) for i in range(_SHORT - 1, len(dv))]


def _tail_signals(bars: list[dict]) -> dict[str, list[tuple[date, float]]]:
    """一个 ticker 的三条日频信号尾巴(最后 _BACKFILL 行):obv_z / dollar_vol_z / mom_63d。"""
    out: dict[str, list[tuple[date, float]]] = {"flow.obv_z": [], "flow.dollar_vol_z": [],
                                                "flow.mom_63d": []}
    n = len(bars)
    if n < _SHORT + 40:
        return out
    od = _obv_delta(bars)                      # 对齐 bars[_SHORT + j]
    dm = _dollar_ma(bars)                      # 对齐 bars[_SHORT - 1 + j]
    for j in range(max(0, len(od) - _BACKFILL), len(od)):
        z = _roll_z(od, j)
        if z is not None:
            out["flow.obv_z"].append((bars[_SHORT + j]["d"], round(z, 2)))
    for j in range(max(0, len(dm) - _BACKFILL), len(dm)):
        z = _roll_z(dm, j)
        if z is not None:
            out["flow.dollar_vol_z"].append((bars[_SHORT - 1 + j]["d"], round(z, 2)))
    for i in range(max(_MOM, n - _BACKFILL), n):
        mom = float(bars[i]["close"]) / float(bars[i - _MOM]["close"]) - 1.0
        out["flow.mom_63d"].append((bars[i]["d"], round(mom, 4)))
    return out


def _pair_tail(long_bars: list[dict], short_bars: list[dict] | None) -> list[tuple[date, float]]:
    """风格对 log 比值的 _SHORT 日变化 z 尾巴(单腿=log 价格自身)。"""
    import math

    if short_bars is None:
        lr = [(b["d"], math.log(float(b["close"]))) for b in long_bars]
    else:
        by_d = {b["d"]: float(b["close"]) for b in short_bars}
        lr = [(b["d"], math.log(float(b["close"]) / by_d[b["d"]]))
              for b in long_bars if by_d.get(b["d"])]
    if len(lr) < _SHORT + 40:
        return []
    chg = [lr[i][1] - lr[i - _SHORT][1] for i in range(_SHORT, len(lr))]
    out = []
    for j in range(max(0, len(chg) - _BACKFILL), len(chg)):
        z = _roll_z(chg, j)
        if z is not None:
            out.append((lr[_SHORT + j][0], round(z, 2)))
    return out


# ── 写库(统一经 altstore;theme 列编码身份)────────────────────────────────────
def _put(key: str, period_end: date, value: float, *, company_id: str | None = None,
         theme: str | None = None, meta: dict | None = None, source: str = "flow") -> None:
    spec = FLOW_BY_KEY[key]
    altstore.upsert_signal(key, period_end=period_end, value=float(value),
                           company_id=company_id, theme=theme, unit=spec.unit,
                           source=source, meta=meta)


# ── run_daily 各分部 ──────────────────────────────────────────────────────────
def _etf_stage() -> dict:
    """资产篮 + 风格对 + risk-on 综合(全部 market/style 行,尾巴回写幂等)。"""
    bars_by_t: dict[str, list[dict]] = {}

    def bars(t: str) -> list[dict]:
        if t not in bars_by_t:
            bars_by_t[t] = _bars(t)
        return bars_by_t[t]

    rows = 0
    mom_z_by_t: dict[str, dict[date, float]] = {}   # risk-on 用:ticker → {d: mom 的滚动 z}
    for e in ASSET_ETFS:
        tail = _tail_signals(bars(e.ticker))
        meta = {"ticker": e.ticker, "asset_class": e.asset_class}
        for key, pts in tail.items():
            for d, v in pts:
                _put(key, d, v, theme=etf_theme(e.ticker), meta=meta)
                rows += 1
        # 动量的滚动 z(risk-on 构成;与写库的 raw mom 分开,只在内存)
        b = bars(e.ticker)
        if len(b) >= _MOM + 40:
            mom = [float(b[i]["close"]) / float(b[i - _MOM]["close"]) - 1.0
                   for i in range(_MOM, len(b))]
            zs = {}
            for j in range(max(0, len(mom) - _BACKFILL), len(mom)):
                z = _roll_z(mom, j)
                if z is not None:
                    zs[b[_MOM + j]["d"]] = z
            mom_z_by_t[e.ticker] = zs

    for p in STYLE_PAIRS:
        tail = _pair_tail(bars(p.long), bars(p.short) if p.short else None)
        meta = {"pair": p.key, "long": p.long, "short": p.short}
        for d, v in tail:
            _put("flow.style_ratio_z", d, v, theme=pair_theme(p.key), meta=meta)
            rows += 1

    # risk-on 综合:两篮动量 z 均值之差 /3 → [-1,1](按日对齐,两侧都要 ≥2 名有值)
    dates: set[date] = set()
    for t in (*RISK_ON_TICKERS, *RISK_OFF_TICKERS):
        dates |= set(mom_z_by_t.get(t, ()))
    n_ro = 0
    for d in sorted(dates):
        on = [mom_z_by_t[t][d] for t in RISK_ON_TICKERS if d in mom_z_by_t.get(t, {})]
        off = [mom_z_by_t[t][d] for t in RISK_OFF_TICKERS if d in mom_z_by_t.get(t, {})]
        if len(on) < 2 or len(off) < 2:
            continue
        score = max(-1.0, min(1.0, (statistics.fmean(on) - statistics.fmean(off)) / 3.0))
        _put("flow.risk_on_composite", d, round(score, 3),
             meta={"on": len(on), "off": len(off)})
        n_ro += 1
    return {"rows": rows, "risk_on_days": n_ro, "tickers": len(bars_by_t)}


def _options_universe() -> list[tuple[str, str]]:
    """(company_id, 期权 ticker) —— 核心美股覆盖(EARNINGS_UNIVERSE 绑定,确定序)。"""
    from ..ontology.altdata import bindings

    return sorted((cid, b.options_ticker) for cid, b in bindings().items() if b.options_ticker)


def _pc_stage() -> dict:
    """期权 Put/Call:市场级 SPY + 个股(核心覆盖前 _PC_COMPANIES 家)。"""
    from ..providers import massive

    if not massive.available():
        return {"skipped": "no MASSIVE_API_KEY"}
    today = date.today()
    out = {"market": 0, "companies": 0}
    snap = massive.pc_snapshot("SPY")
    if snap:
        _put("flow.pc_ratio", today, snap["pc"], theme=etf_theme("SPY"),
             meta={"basis": snap["basis"], "ticker": "SPY"}, source="massive")
        out["market"] = 1
    for cid, tkr in _options_universe()[:_PC_COMPANIES]:
        snap = massive.pc_snapshot(tkr)
        if snap:
            _put("flow.pc_ratio", today, snap["pc"], company_id=cid,
                 meta={"basis": snap["basis"], "ticker": tkr}, source="massive")
            out["companies"] += 1
    return out


def _short_interest_stage() -> dict:
    """空头持仓(双周)—— 核心覆盖游标轮转切片;未 entitle 全空 → 上层显示未接入。"""
    from ..providers import massive
    from ..storage.kvstate import get_state, save_state

    if not massive.available():
        return {"skipped": "no MASSIVE_API_KEY"}
    uni = _options_universe()
    if not uni:
        return {"skipped": "no US options universe"}
    off = int(get_state("cursor").get("flow_si", 0)) % len(uni)
    todo = (uni + uni)[off:off + min(_SI_SLICE, len(uni))]
    rows = 0
    for cid, tkr in todo:
        for r in massive.short_interest(tkr):
            pe = date.fromisoformat(str(r["settlement_date"])[:10])
            _put("flow.short_interest", pe, r["short_interest"], company_id=cid,
                 meta={"ticker": tkr, "avg_daily_volume": r.get("avg_daily_volume")},
                 source="massive")
            rows += 1
            if r.get("days_to_cover") is not None:
                _put("flow.days_to_cover", pe, float(r["days_to_cover"]), company_id=cid,
                     meta={"ticker": tkr}, source="massive")
    cur = get_state("cursor")
    cur["flow_si"] = (off + len(todo)) % len(uni)
    save_state("cursor", cur)
    return {"companies": len(todo), "rows": rows} if rows else {
        "companies": len(todo), "rows": 0, "note": "no data — endpoint not entitled?"}


def _holdings_stage() -> dict:
    """13F 机构持仓季度Δ(全部有 ≥2 季数据的公司;历史逐季回写,幂等)。"""
    rows = db.query(
        "SELECT company_id, as_of, sum(value_usd) v FROM holdings "
        "WHERE value_usd IS NOT NULL GROUP BY company_id, as_of ORDER BY company_id, as_of")
    by_c: dict[str, list] = {}
    for r in rows:
        by_c.setdefault(r["company_id"], []).append(r)
    n = 0
    for cid, seq in by_c.items():
        for prev, cur in zip(seq, seq[1:]):
            if not prev["v"] or float(prev["v"]) == 0:
                continue
            delta = (float(cur["v"]) / float(prev["v"]) - 1.0) * 100.0
            _put("flow.inst_own_delta", cur["as_of"], round(delta, 2), company_id=cid,
                 meta={"value_usd": float(cur["v"])}, source="holdings")
            n += 1
    return {"companies": len(by_c), "rows": n}


def _futu_z(company_id: str, *, as_of: date | None = None) -> float | None:
    stats = _zscore(altstore.series("alt.futu_main_capital_flow", company_id=company_id,
                                    as_of=_pit(as_of)), 10)
    return stats["z"] if stats else None


def _company_quant(cid: str, ticker: str) -> dict | None:
    """美股成员的量价三信号(写库 = 当日一行;主题聚合复用返回值)。"""
    bars = _bars(ticker)
    tail = _tail_signals(bars)
    latest: dict[str, float] = {}
    for key, pts in tail.items():
        if pts:
            d, v = pts[-1]
            _put(key, d, v, company_id=cid, meta={"ticker": ticker})
            latest[key] = v
    return latest or None


def _theme_stage() -> dict:
    """主题聚合净分:富途主力 z ⊕ 美股成员量价 ⊕ 空头持仓Δ(可用分量再加权)。"""
    from ..ingestion.registry import COMPANIES, THEMES
    from ..ontology.earnings_events import EARNINGS_UNIVERSE

    prio = {cid: i for i, cid in enumerate(EARNINGS_UNIVERSE)}
    quant_cache: dict[str, dict | None] = {}
    today = date.today()
    out: dict[str, float] = {}
    for tid in THEMES:
        members = [c for c in COMPANIES if tid in (c.get("themes") or ())]
        futu_zs, quant_scores, si_zs = [], [], []
        us = [(c["id"], next((t for t in c.get("tickers", []) if "." not in t), None))
              for c in members]
        us = [(cid, t) for cid, t in us if t]
        us.sort(key=lambda x: prio.get(x[0], 9999))
        for cid, tkr in us[:_THEME_QUANT_CAP]:
            if cid not in quant_cache:
                quant_cache[cid] = _company_quant(cid, tkr)
            q = quant_cache[cid]
            if q and "flow.obv_z" in q:
                quant_scores.append(q["flow.obv_z"] / 3.0)
        for c in members[:60]:
            z = _futu_z(c["id"])
            if z is not None:
                futu_zs.append(z / 3.0)
            st = _zscore(altstore.series("flow.short_interest", company_id=c["id"]), 4)
            if st is not None:
                si_zs.append(-st["z"] / 3.0)   # 空头增 = 资金面转空 → 取负
        comps = [(0.5, quant_scores), (0.3, futu_zs), (0.2, si_zs)]
        wsum = sum(w for w, xs in comps if xs)
        if wsum == 0:
            continue
        score = sum(w * statistics.fmean(xs) for w, xs in comps if xs) / wsum
        score = max(-1.0, min(1.0, score))
        _put("flow.theme_net_score", today, round(score, 3), theme=tid,
             meta={"quant_n": len(quant_scores), "futu_n": len(futu_zs), "si_n": len(si_zs)})
        out[tid] = round(score, 3)
    return {"themes": out}


# ── 语义流桥接:阈值信号 → kg_events(flow_signal) ───────────────────────────────
def sync_flow_events(*, z_threshold: float = 2.0, score_threshold: float = 0.6) -> dict:
    """新期 flow 信号越阈 → kg_events(幂等 dedup)。公司行挂公司/主题;主题净分挂主题;
    market 行(etf:*)company/theme 双空,只进无过滤事件流与 Andy 面板。"""
    from ..ingestion.registry import company_by_id

    keys = ("flow.obv_z", "flow.mom_63d", "flow.theme_net_score")
    # 21 天期末窗:公司日线来自 daily 管线,可能滞后一两周(ETF 走 massive 是新鲜的);
    # dedup_key 保证放宽窗口不会复插。
    rows = db.query(
        "SELECT signal_key, company_id, theme, period_end, value, meta FROM alt_signals "
        "WHERE signal_key = ANY(%s) AND period_end >= %s "
        "AND observed_at >= now() - interval '3 days'",
        (list(keys), date.today() - timedelta(days=21)))
    inserted = skipped = 0
    for r in rows:
        key, val = r["signal_key"], float(r["value"])
        spec = FLOW_BY_KEY[key]
        is_score = key == "flow.theme_net_score"
        is_market = bool(r["theme"] and ":" in r["theme"])
        thr = score_threshold if is_score else (z_threshold + 0.5 if is_market else z_threshold)
        # mom_63d 存的是 raw 比率:强势 tape 下 25%/季太常见(真机 72 条刷屏),
        # 事件门槛取 |值|≥50%/季 —— 只让真正极端的动量进语义流。
        if key == "flow.mom_63d":
            thr = 0.50
        if abs(val) < thr:
            continue
        ident = r["company_id"] or r["theme"] or "mkt"
        dedup = f"flow:{key}:{ident}:{r['period_end']}"
        if db.query("SELECT 1 FROM kg_events WHERE dedup_key=%s", (dedup,)):
            skipped += 1
            continue
        pol = "neutral"
        if spec.good_when == "rising":
            pol = "positive" if val > 0 else "negative"
        meta = r["meta"] if isinstance(r["meta"], dict) else json.loads(r["meta"] or "{}")
        label = (r["company_id"] and (company_by_id(r["company_id"]) or {}).get("name")) \
            or meta.get("ticker") or r["theme"]
        theme = None if is_market else (
            r["theme"] or ((company_by_id(r["company_id"]) or {}).get("themes") or [None])[0])
        db.execute(
            "INSERT INTO kg_events(company_id, event_type, event_date, polarity, summary, "
            "narrative, attrs, confidence, license_tag, dedup_key, theme, time_orientation) "
            "VALUES (%s,'flow_signal',%s,%s,%s,%s,%s::jsonb,0.85,'alt',%s,%s,"
            "'backward_looking') ON CONFLICT (dedup_key) DO NOTHING",
            (r["company_id"], r["period_end"], pol,
             f"资金流信号:{label} {spec.name_cn} = {val:+.2f}(期末 {r['period_end']})",
             spec.rationale_zh[:200],
             json.dumps({"signal_key": key, "value": val, **meta}, ensure_ascii=False,
                        default=str),
             dedup, theme))
        inserted += 1
    out = {"inserted": inserted, "skipped": skipped}
    log.info("flow events: %s", out)
    return out


def run_daily() -> dict:
    """glm_worker "flow" 源入口(日频,零 LLM):取数 → 全量计算 → 事件同步。"""
    from ..providers import massive

    out: dict = {"prices": massive.pull_etf_prices()}
    out["etf"] = _etf_stage()
    out["pc"] = _pc_stage()
    out["short_interest"] = _short_interest_stage()
    out["holdings"] = _holdings_stage()
    out["themes"] = _theme_stage()
    out["events"] = sync_flow_events()
    log.info("flow run_daily: %s", {k: v for k, v in out.items() if k != "themes"})
    return out


# ── 读接口(API / Genny 块 / Chathy 工具共用;仅读库,不外呼)───────────────────
def _pit(as_of: date | None) -> date | None:
    """observed_at 是 timestamptz,直接比 date 会截到当日 0 点、把当日写入全滤掉。
    as_of 语义 = "截至当日日终可知" → 用次日 0 点作排他上界。"""
    return (as_of + timedelta(days=1)) if as_of else None


def _series(key: str, *, company_id: str | None = None, theme: str | None = None,
            as_of: date | None = None, limit: int = 60) -> list[dict]:
    rows = altstore.series(key, company_id=company_id, theme=theme, as_of=_pit(as_of),
                           limit=limit)
    return [{"d": str(r["period_end"]), "v": float(r["value"])} for r in reversed(rows)]


def _latest(key: str, *, company_id: str | None = None, theme: str | None = None,
            as_of: date | None = None) -> dict | None:
    rows = altstore.series(key, company_id=company_id, theme=theme, as_of=_pit(as_of), limit=1)
    if not rows:
        return None
    return {"d": str(rows[0]["period_end"]), "v": float(rows[0]["value"]),
            "meta": rows[0]["meta"] if isinstance(rows[0]["meta"], dict) else {}}


def _market_snapshot(as_of: date | None) -> dict:
    assets = []
    for e in ASSET_ETFS:
        th = etf_theme(e.ticker)
        obv = _latest("flow.obv_z", theme=th, as_of=as_of)
        dv = _latest("flow.dollar_vol_z", theme=th, as_of=as_of)
        mom_rows = altstore.series("flow.mom_63d", theme=th, as_of=_pit(as_of), limit=_HIST)
        mom_stats = _zscore(mom_rows, 10)
        parts = [x for x in ((obv or {}).get("v"), (mom_stats or {}).get("z")) if x is not None]
        composite = round(statistics.fmean(p / 3.0 for p in parts), 2) if parts else None
        assets.append({
            "ticker": e.ticker, "label": e.label, "label_cn": e.label_cn,
            "asset_class": e.asset_class,
            "obv_z": (obv or {}).get("v"), "dollar_vol_z": (dv or {}).get("v"),
            "mom_63d": mom_stats["latest"] if mom_stats else None,
            "mom_z": mom_stats["z"] if mom_stats else None,
            "composite": composite, "as_of": (obv or {}).get("d"),
            "spark": _series("flow.mom_63d", theme=th, as_of=as_of, limit=40),
        })
    styles = [{
        "pair": p.key, "label": p.label, "label_cn": p.label_cn,
        "rationale_zh": p.rationale_zh,
        "z": (_latest("flow.style_ratio_z", theme=pair_theme(p.key), as_of=as_of) or {}).get("v"),
        "series": _series("flow.style_ratio_z", theme=pair_theme(p.key), as_of=as_of),
    } for p in STYLE_PAIRS]
    pc = _latest("flow.pc_ratio", theme=etf_theme("SPY"), as_of=as_of)
    return {
        "assets": assets, "styles": styles,
        "risk_on": {"value": (_latest("flow.risk_on_composite", as_of=as_of) or {}).get("v"),
                    "series": _series("flow.risk_on_composite", as_of=as_of)},
        "pc": {"value": (pc or {}).get("v"), "basis": (pc or {}).get("meta", {}).get("basis"),
               "series": _series("flow.pc_ratio", theme=etf_theme("SPY"), as_of=as_of, limit=40)},
    }


def _theme_snapshot(theme: str, as_of: date | None) -> dict:
    from ..ingestion.registry import COMPANIES, THEMES

    members = [c for c in COMPANIES if theme in (c.get("themes") or ())]
    movers = []
    for c in members:
        cid = c["id"]
        row = {"company_id": cid, "name": c["name"],
               "ticker": (c.get("tickers") or [None])[0]}
        z = _futu_z(cid, as_of=as_of)
        if z is not None:
            row["futu_flow_z"] = z
        obv = _latest("flow.obv_z", company_id=cid, as_of=as_of)
        if obv:
            row["obv_z"] = obv["v"]
        si = _zscore(altstore.series("flow.short_interest", company_id=cid, as_of=_pit(as_of)), 4)
        if si:
            row["short_interest_z"] = si["z"]
        if len(row) > 3:
            row["score"] = round(statistics.fmean(
                v / 3.0 for k, v in row.items()
                if k in ("futu_flow_z", "obv_z")) if any(
                k in row for k in ("futu_flow_z", "obv_z")) else 0.0, 2)
            movers.append(row)
    movers.sort(key=lambda r: -abs(r.get("score") or 0))
    return {
        "theme": theme, "name_cn": (THEMES.get(theme) or {}).get("nameCn"),
        "net_score": (_latest("flow.theme_net_score", theme=theme, as_of=as_of) or {}).get("v"),
        "series": _series("flow.theme_net_score", theme=theme, as_of=as_of),
        "movers": movers[:10],
    }


def _company_snapshot(company_id: str, as_of: date | None) -> dict:
    out: dict = {"company_id": company_id}
    for key in ("flow.obv_z", "flow.dollar_vol_z", "flow.pc_ratio", "flow.inst_own_delta",
                "flow.short_interest", "flow.days_to_cover"):
        latest = _latest(key, company_id=company_id, as_of=as_of)
        if latest:
            spec = FLOW_BY_KEY[key]
            out[key.removeprefix("flow.")] = {
                "value": latest["v"], "period_end": latest["d"], "unit": spec.unit,
                "name_cn": spec.name_cn}
    mom = _zscore(altstore.series("flow.mom_63d", company_id=company_id, as_of=_pit(as_of)), 10)
    if mom:
        out["mom_63d"] = {"value": mom["latest"], "z": mom["z"], "period_end": mom["period_end"],
                          "unit": "ratio", "name_cn": FLOW_BY_KEY["flow.mom_63d"].name_cn}
    si = _zscore(altstore.series("flow.short_interest", company_id=company_id,
                                 as_of=_pit(as_of)), 4)
    if si:
        out.setdefault("short_interest", {})["z"] = si["z"]
    fz = _futu_z(company_id, as_of=as_of)
    if fz is not None:
        out["futu_flow"] = {
            "z": fz, "name_cn": "富途主力资金净流入",
            "series": _series("alt.futu_main_capital_flow", company_id=company_id,
                              as_of=as_of, limit=30)}
    return out


def flow_snapshot(scope: str = "market", *, theme: str | None = None,
                  company_id: str | None = None, as_of: date | None = None) -> dict:
    """统一读接口。market=大类/风格/情绪;theme=主题净分+成员榜;company=个股资金面。"""
    if scope == "theme" and theme:
        return _theme_snapshot(theme, as_of)
    if scope == "company" and company_id:
        return _company_snapshot(company_id, as_of)
    return _market_snapshot(as_of)
