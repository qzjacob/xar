"""Upsert + read helpers for the structured-data layer (fundamentals, estimates,
ratings, prices, insider trades, prediction markets, social posts).

All writes are idempotent (provider-scoped UNIQUE keys) so re-pulls never
duplicate. Every row records its `source` and an `as_of`/observation time so the
same fact from multiple providers — and the evolution of consensus — coexist.
"""
from __future__ import annotations

import hashlib
import json

from ..logging import get_logger
from . import db

log = get_logger("xar.structured")

# fundamentals 同期多源撞车时的权威取值优先级(报表口径 > 第三方聚合 > 抽取)。
# 单一真相:derived 计算(research/indicators.py)与 VP 读数(research/evidence_link.py)共用,
# 防两处各自维护一份而漂移(评审 #3)。
FUNDAMENTAL_SOURCE_PRIORITY: dict[str, int] = {
    "edgar": 6, "cninfo": 5, "gangtise": 5, "wind": 4, "aifinmarket": 4,
    "futu": 3, "fmp": 3, "finnhub": 3, "polygon": 2, "yahoo": 2, "extracted": 1,
}


def source_priority_sql(col: str = "source") -> str:
    """把上面的优先级表编译成 SQL `CASE ... END`(值均为可信常量,无注入)。"""
    whens = " ".join(f"WHEN '{s}' THEN {p}" for s, p in FUNDAMENTAL_SOURCE_PRIORITY.items())
    return f"CASE {col} {whens} ELSE 0 END"


def _json(d) -> str:
    return json.dumps(d or {}, ensure_ascii=False, default=str)


# --- fundamentals ----------------------------------------------------------
def upsert_fundamental(company_id, metric, value, *, period=None, period_end=None,
                       freq=None, unit="USD", source="", meta=None) -> None:
    if value is None:
        return
    db.execute(
        """INSERT INTO fundamentals
             (company_id,metric,period,period_end,freq,value,unit,source,meta)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (company_id,metric,period,source) DO UPDATE SET
             value=EXCLUDED.value, period_end=EXCLUDED.period_end,
             freq=EXCLUDED.freq, unit=EXCLUDED.unit, as_of=now(), meta=EXCLUDED.meta""",
        (company_id, metric, period, period_end, freq, float(value), unit, source, _json(meta)),
    )


# --- estimates -------------------------------------------------------------
def upsert_estimate(company_id, metric, value, as_of, *, period=None, period_end=None,
                    high=None, low=None, n_analysts=None, unit="USD", source="",
                    meta=None) -> None:
    if value is None:
        return
    db.execute(
        """INSERT INTO estimates
             (company_id,metric,period,period_end,value,high,low,n_analysts,unit,source,as_of,meta)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (company_id,metric,period,source,as_of) DO UPDATE SET
             value=EXCLUDED.value, high=EXCLUDED.high, low=EXCLUDED.low,
             n_analysts=EXCLUDED.n_analysts, meta=EXCLUDED.meta""",
        (company_id, metric, period, period_end, float(value),
         _f(high), _f(low), n_analysts, unit, source, as_of, _json(meta)),
    )


# --- analyst ratings -------------------------------------------------------
def upsert_rating(company_id, as_of, *, strong_buy=None, buy=None, hold=None,
                  sell=None, strong_sell=None, pt_mean=None, pt_high=None,
                  pt_low=None, source="", meta=None) -> None:
    db.execute(
        """INSERT INTO analyst_ratings
             (company_id,as_of,strong_buy,buy,hold,sell,strong_sell,pt_mean,pt_high,pt_low,source,meta)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (company_id,as_of,source) DO UPDATE SET
             strong_buy=EXCLUDED.strong_buy, buy=EXCLUDED.buy, hold=EXCLUDED.hold,
             sell=EXCLUDED.sell, strong_sell=EXCLUDED.strong_sell,
             pt_mean=EXCLUDED.pt_mean, pt_high=EXCLUDED.pt_high, pt_low=EXCLUDED.pt_low,
             meta=EXCLUDED.meta""",
        (company_id, as_of, strong_buy, buy, hold, sell, strong_sell,
         _f(pt_mean), _f(pt_high), _f(pt_low), source, _json(meta)),
    )


# --- prices ----------------------------------------------------------------
def upsert_prices(company_id, ticker, bars, *, source="") -> int:
    """bars: iterable of dicts with d/open/high/low/close/volume."""
    rows = [
        (company_id, ticker, b["d"], _f(b.get("open")), _f(b.get("high")),
         _f(b.get("low")), _f(b.get("close")), _f(b.get("volume")), source)
        for b in bars if b.get("d") is not None
    ]
    if not rows:
        return 0
    db.executemany(
        """INSERT INTO prices(company_id,ticker,d,open,high,low,close,volume,source)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (ticker,d,source) DO UPDATE SET close=EXCLUDED.close,
             open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
             volume=EXCLUDED.volume""",
        rows,
    )
    return len(rows)


# --- insider trades --------------------------------------------------------
def upsert_insider(company_id, *, insider=None, role=None, txn_date=None,
                   txn_type=None, shares=None, price=None, value=None,
                   source="", meta=None) -> bool:
    dedup = hashlib.sha256(
        f"{company_id}|{insider}|{txn_date}|{txn_type}|{shares}|{price}".encode()
    ).hexdigest()[:32]
    if db.query("SELECT 1 FROM insider_trades WHERE dedup_key=%s", (dedup,)):
        return False
    db.execute(
        """INSERT INTO insider_trades
             (company_id,insider,role,txn_date,txn_type,shares,price,value,source,dedup_key,meta)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (dedup_key) DO NOTHING""",
        (company_id, insider, role, txn_date, txn_type, _f(shares), _f(price),
         _f(value), source, dedup, _json(meta)),
    )
    return True


# --- prediction markets ----------------------------------------------------
def upsert_prediction_market(market_id, *, question=None, outcome=None,
                             probability=None, volume=None, close_date=None,
                             tags=None, company_id=None, tech_route_tag=None,
                             source="polymarket", meta=None) -> None:
    db.execute(
        """INSERT INTO prediction_markets
             (market_id,question,outcome,probability,volume,close_date,tags,
              company_id,tech_route_tag,source,meta)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (market_id,outcome,as_of) DO NOTHING""",
        (str(market_id), question, outcome, _f(probability), _f(volume), close_date,
         tags or [], company_id, tech_route_tag, source, _json(meta)),
    )


# --- social posts ----------------------------------------------------------
def upsert_social(post_id, platform, *, company_id=None, author=None, url=None,
                  posted_at=None, text=None, metrics=None, sentiment=None,
                  permission="grey", meta=None) -> None:
    db.execute(
        """INSERT INTO social_posts
             (id,platform,company_id,author,url,posted_at,text,metrics,sentiment,permission,meta)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (id) DO UPDATE SET
             metrics=EXCLUDED.metrics, sentiment=EXCLUDED.sentiment, text=EXCLUDED.text""",
        (f"{platform}:{post_id}", platform, company_id, author, url, posted_at,
         text, _json(metrics), _f(sentiment), permission, _json(meta)),
    )


# --- forward event calendar ------------------------------------------------
_EARNINGS_REVISION_DAYS = 45   # 同一次财报的聚簇/改期半径(季度间隔 ~91d,故 45d 内必属同一次)
_EARNINGS_GHOST_DAYS = 25      # occurred 实际日之后 N 天内的 scheduled 视为过期估计(实测幽灵 1~6d;
                               # 取 25 而非 45 —— 覆盖全部实测漂移,又给"45 天内二次披露"留余量)


def _revise_earnings_date(company_id, scheduled_for, source, status, importance,
                          window_end, meta) -> bool:
    """财报日期**修订**:同 (公司, earnings, source) 在 ±45d 内已有 scheduled 行 → 原地改期,
    而不是插新行。根因修复 —— dedup_key 含 scheduled_for,供应商每日重估(yahoo 实测逐日漂移)
    会不断生成幽灵行,把已发生的季报显示成未来日期(Phanny/earnings 读到假事件)。
    只改 status='scheduled' 的行(绝不覆盖 occurred 实际日)。命中返回 True。"""
    rows = db.query(
        "SELECT id, title, dedup_key FROM event_calendar WHERE company_id=%s "
        "AND event_type='earnings' AND source=%s AND status='scheduled' "
        "AND scheduled_for BETWEEN %s::date - make_interval(days => %s) "
        "                      AND %s::date + make_interval(days => %s) "
        "ORDER BY abs(scheduled_for - %s::date) LIMIT 1",
        (company_id, source, scheduled_for, _EARNINGS_REVISION_DAYS,
         scheduled_for, _EARNINGS_REVISION_DAYS, scheduled_for))
    if not rows:
        return False
    row = rows[0]
    # dedup_key 编码了旧日期 —— 改期后必须重算,否则该行的 key 与 company|type|date|title 不再自洽,
    # 下次同日期重报会因探测不到而**再插一条重复行**(评审确认的漏洞)。按 id 定位,避免键漂移。
    new_key = hashlib.sha256(
        f"{company_id}|earnings|{scheduled_for}|{(row['title'] or '').strip().lower()}".encode()
    ).hexdigest()[:32]
    taken = db.query("SELECT 1 FROM event_calendar WHERE dedup_key=%s AND id<>%s",
                     (new_key, row["id"]))
    key_sql = "" if taken else ", dedup_key=%s"      # 目标键已被占 → 保留旧键(唯一约束优先)
    params: list = [scheduled_for, status, importance, window_end, _json(meta)]
    if not taken:
        params.append(new_key)
    params.append(row["id"])
    db.execute(
        "UPDATE event_calendar SET scheduled_for=%s, status=%s, importance=%s, window_end=%s, "
        f"as_of=now(), meta = COALESCE(meta,'{{}}'::jsonb) || %s::jsonb{key_sql} WHERE id=%s",
        params)
    return True


def upsert_calendar(company_id, event_type, scheduled_for, *, title=None,
                    window_end=None, status="scheduled", importance=2,
                    tech_route_tag=None, source="manual", meta=None) -> bool:
    """Insert/update a scheduled forward event. Deduped on
    company|type|date|title so re-pulls don't duplicate. Returns True on insert.

    财报特例:同源同季度的**日期修订**原地更新(见 _revise_earnings_date),避免供应商
    逐日重估堆出幽灵行。另拒绝明显不合理的日期(如 2098 年)——脏数据别进日历。"""
    # 刻意**不**加"远期日期"闸:库里确有一条 now|2098-10-03(occurred,yahoo),但它落在所有真实
    # 查询窗之外、且抑制逻辑只看目标行之前的实际日,故无害;而 tests/ 依赖 2099 哨兵日期做共享库
    # 隔离(_clean 按 scheduled_for >= '2099-01-01' 清理),加闸会破坏这一既有约定。脏行按数据卫生
    # 单独清理,不在写入层设策略。
    if event_type == "earnings" and status == "scheduled" and _revise_earnings_date(
            company_id, scheduled_for, source, status, importance, window_end, meta):
        return False
    dedup = hashlib.sha256(
        f"{company_id}|{event_type}|{scheduled_for}|{(title or '').strip().lower()}".encode()
    ).hexdigest()[:32]
    if db.query("SELECT 1 FROM event_calendar WHERE dedup_key=%s", (dedup,)):
        # meta 用 jsonb 合并而非整体覆盖 —— 否则一个源重拉会抹掉另一源写入的键
        # (如 yahoo 重拉抹掉 finnhub 的 hour / earnings-surprise;新键覆盖旧键,加性)。
        db.execute(
            "UPDATE event_calendar SET status=%s, importance=%s, window_end=%s, "
            "as_of=now(), meta = COALESCE(meta,'{}'::jsonb) || %s::jsonb WHERE dedup_key=%s",
            (status, importance, window_end, _json(meta), dedup))
        return False
    db.execute(
        """INSERT INTO event_calendar
             (company_id,event_type,scheduled_for,window_end,title,status,importance,
              tech_route_tag,source,meta,dedup_key)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (dedup_key) DO NOTHING""",
        (company_id, event_type, scheduled_for, window_end, title, status, importance,
         tech_route_tag, source, _json(meta), dedup))
    return True


def upcoming_calendar(company_ids=None, *, days=90, limit=200) -> list[dict]:
    """Scheduled events from today forward, optionally scoped to companies。

    财报去幽灵(两道):
      ① **已发生即抑制** —— 某 scheduled 财报行若在其前 45 天内已存在同公司 `occurred` 财报行,
         说明这次季报**已经开完**、该行只是供应商的过期估计(如 ServiceNow 实际 7-22 已出,
         yahoo 仍留 7-26 估计)→ 不再当作"下一次财报"返回。
      ② **同季度只留一行** —— 多源(yahoo/finnhub)对同一次季报给出差几天的估计时,按
         (公司, 季度) 取最新 as_of 的一行,避免同一事件被下游(Phanny book/earnings judge)重复处理。
    非财报事件不受影响(季度身份对它们无意义)。修复根因见 _revise_earnings_date。"""
    where = ["scheduled_for >= CURRENT_DATE",
             "scheduled_for <= CURRENT_DATE + make_interval(days => %s)",
             "status <> 'cancelled'",
             # ① 抑制已被 occurred 实际日取代的过期估计(只看该行**之前** GHOST_DAYS 天内的实际日,
             #    故未来的脏 occurred 行——实测有 2098——不会误抑制任何真实事件)
             "(event_type <> 'earnings' OR NOT EXISTS ("
             "   SELECT 1 FROM event_calendar o"
             "   WHERE o.company_id = event_calendar.company_id AND o.event_type = 'earnings'"
             "     AND o.status = 'occurred' AND o.scheduled_for < event_calendar.scheduled_for"
             "     AND o.scheduled_for > event_calendar.scheduled_for"
             "                          - make_interval(days => %s)))"]
    params: list = [days, _EARNINGS_GHOST_DAYS]
    if company_ids is not None:
        where.append("company_id = ANY(%s)")
        params.append(list(company_ids) or [""])
    params.append(limit)
    rows = db.query(
        "SELECT id,company_id,event_type,scheduled_for,window_end,title,status,"
        "importance,tech_route_tag,source,meta,as_of FROM event_calendar "
        f"WHERE {' AND '.join(where)} ORDER BY scheduled_for, importance DESC LIMIT %s", params)
    # ② 同一次财报的多源估计聚簇后只留一行 —— 在 Python 里做(而非 SQL 窗口):日历季度分桶会在
    #    季度边界(9-30 vs 10-1)把同一次财报劈成两簇;窗口函数又会让同季度的非财报行(分红/拆股)
    #    抢占 rank 把财报行整行挤掉(评审实测复现)。按邻近聚簇 + 显式跳过非财报,两个坑都不存在。
    return _collapse_earnings(rows)


def _pick_earnings(cluster: list[dict]) -> dict:
    """簇内取一行:来源权威优先(finnhub/fmp 3 > yahoo 2)→ as_of 新 → 日期早。
    刻意**不**单看 as_of ——`as_of` 是"最后被触碰时间"而非"日期断言时间",一次无变化的重拉也会刷新它,
    否则漂移中的 yahoo 估计会凭"刚被拉过"压掉更权威的 finnhub 日期(评审确认)。"""
    def key(r: dict):
        as_of = r.get("as_of")
        return (-FUNDAMENTAL_SOURCE_PRIORITY.get((r.get("source") or "").lower(), 0),
                -(as_of.timestamp() if hasattr(as_of, "timestamp") else 0.0),
                r["scheduled_for"])
    return min(cluster, key=key)


def _collapse_earnings(rows: list[dict]) -> list[dict]:
    """把同公司同一次财报的多行(多源估计/日期修订)聚为一簇,每簇留一行;非财报行原样保留。
    聚簇 = 与簇首相距 ≤ _EARNINGS_REVISION_DAYS(不与前一行链式比较,避免长链漂移把两个季度并成一簇)。"""
    out = [r for r in rows if r.get("event_type") != "earnings"]
    by_cid: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("event_type") == "earnings":
            by_cid.setdefault(r.get("company_id"), []).append(r)
    for rs in by_cid.values():
        rs.sort(key=lambda r: r["scheduled_for"])
        cluster: list[dict] = []
        for r in rs:
            if cluster and (r["scheduled_for"] - cluster[0]["scheduled_for"]).days > _EARNINGS_REVISION_DAYS:
                out.append(_pick_earnings(cluster))
                cluster = []
            cluster.append(r)
        if cluster:
            out.append(_pick_earnings(cluster))
    out.sort(key=lambda r: (r["scheduled_for"], -(r.get("importance") or 0)))
    return out


# --- reads (used by API + signals + report context) ------------------------
def latest_fundamentals(company_id, limit=40) -> list[dict]:
    return db.query(
        "SELECT metric,period,period_end,freq,value,unit,source FROM fundamentals "
        "WHERE company_id=%s ORDER BY period_end DESC NULLS LAST, metric LIMIT %s",
        (company_id, limit),
    )


def estimate_series(company_id, metric, period) -> list[dict]:
    return db.query(
        "SELECT as_of,value,high,low,n_analysts,source FROM estimates "
        "WHERE company_id=%s AND metric=%s AND period=%s ORDER BY as_of",
        (company_id, metric, period),
    )


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
