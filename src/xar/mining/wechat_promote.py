"""晋升漏斗 —— 混合漏斗的自愈段:把「搜索发现出的高产号」晋升为 we-mp-rss 订阅。

发现(ingestion/wechat_discover)是脆弱的实时搜索;订阅名册(roster / wechat_accounts)是
稳定的。本模块按号聚合发现文档的 **triage 产出**(发现过多少篇、其中多少篇过了 deep_min
闸),把高信噪比的号(≥min_articles 篇且 keep_rate≥min_keep_rate)在**每日上限**内自动
晋升:请 we-mp-rss 订阅 → 拿到 feed_id → roster.register 进策展名册,此后由稳定轮询接管。
脆弱命中 → 耐久订阅的收敛。

候选状态落 `wechat_discovered` 表(gh_id 键 —— 订阅前无 feed_id,故与 feed_id 键的策展名册
wechat_accounts 分表)。晋升有阈值 + 每日上限,防垃圾号灌进名册、防打爆 we-mp-rss 会话限流。
真正的 we-mp-rss 订阅 API 路径在主机 spike 期定型;`subscribe_fn` 可注入(测试用),默认
适配器失败即 WARN 返回 None(候选保留,下轮重试),绝不炸。
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..config import get_settings
from ..logging import get_logger
from ..storage import db
from . import roster

log = get_logger("xar.wechat_promote")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _default_subscribe(gh_id: str, name: str) -> str | None:
    """真实订阅适配器:调 werss_api.subscribe(add_mp,真实端点 POST /api/v1/wx/mps)。
    发现文档的 gh_id 即 wcda fakeid,故直接组 account dict。成功返回 feed_id(MP_WXS_…),
    会话过期/失败返回 None(候选保留,下轮重试)。测试可注入 subscribe_fn 桩。"""
    from ..ingestion import werss_api
    return werss_api.subscribe({"fakeid": gh_id, "name": name})


def _name_ok(name: str) -> bool:
    """号名不含明显跨域垃圾标记(复用发现层同一 stoplist)。用负向过滤而非「必须命中主题」:
    后者会误伤纯品牌名的合法号(如「砺算科技」)。垃圾名(游戏/超市/租房…)→ 降级 HITL 待批。
    注:候选已过发现层 junk-filter + keep_rate 闸,此为晋升前的最后一道跨域护栏。"""
    from ..ingestion.wechat_discover import _is_junk_account
    return not _is_junk_account({"name": name or ""})


def _sync_candidates() -> None:
    """从 documents 聚合每个被发现号的 triage 产出,upsert 进 wechat_discovered
    (保留 first_seen / promoted_at / feed_id)。gh_id 为空的号跳过(无法订阅)。"""
    dm = float(get_settings().wechat_deep_min)
    rows = db.query(
        "SELECT meta->>'gh_id' AS gh_id, max(meta->>'account') AS name, "
        "count(*) AS seen, count(*) FILTER (WHERE triage_score >= %s) AS kept "
        "FROM documents "
        "WHERE source='wechat' AND meta->>'via'='discover' AND triaged_at IS NOT NULL "
        "AND coalesce(meta->>'gh_id','') <> '' "
        "GROUP BY meta->>'gh_id'", (dm,))
    for r in rows:
        seen, kept = int(r["seen"]), int(r["kept"])
        keep_rate = round(kept / seen, 4) if seen else 0.0
        db.execute(
            "INSERT INTO wechat_discovered (gh_id, name, articles_seen, articles_kept, keep_rate) "
            "VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (gh_id) DO UPDATE SET "
            "  name=COALESCE(EXCLUDED.name, wechat_discovered.name), "
            "  articles_seen=EXCLUDED.articles_seen, articles_kept=EXCLUDED.articles_kept, "
            "  keep_rate=EXCLUDED.keep_rate, updated_at=now()",
            (r["gh_id"], r.get("name"), seen, kept, keep_rate))


def _promoted_today() -> int:
    # promoted_at 由 _now() 以 UTC 写入 → 日界按 UTC 截断,不随 DB 会话时区漂移
    # (否则午夜前后 promote 可能计入错误日期,突破/浪费每日上限)。
    r = db.query("SELECT count(*) n FROM wechat_discovered WHERE promoted_at >= "
                 "date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'")
    return int(r[0]["n"]) if r else 0


def promote_candidates(*, dry_run: bool = False, subscribe_fn=None) -> dict:
    """跑一轮**混合晋升**:聚合 → 分档 → 每日上限内自动订阅高信噪号、其余进 HITL 待批。
    分档(均需 articles_seen>=min_articles、promote_status 非 rejected):
      · 运营方已批准(promote_status='approved') → 订阅(人已裁定,不看 keep_rate)。
      · keep_rate>=auto_keep_rate 且号名主题相关 → 自动订阅(promote_status='auto')。
      · min_keep_rate<=keep_rate<auto(或号名不相关) → 入 HITL 队列(promote_status='queued')。
    只对未晋升号操作;策展名册(手工 tier-1)不经此路。返回统计。"""
    s = get_settings()
    subscribe_fn = subscribe_fn or _default_subscribe
    _sync_candidates()

    cands = db.query(
        "SELECT gh_id, name, articles_seen, articles_kept, keep_rate, promote_status "
        "FROM wechat_discovered "
        "WHERE promoted_at IS NULL AND coalesce(promote_status,'') <> 'rejected' "
        # 运营方已批准的号:不再受阈值闸(人已裁定)——否则其 keep_rate 事后跌破 min 会静默消失、
        # 永停 approved 态不订阅(WD-13 复评#1)。其余仍需达 min_articles + min_keep_rate。
        "AND (promote_status = 'approved' OR (articles_seen >= %s AND keep_rate >= %s)) "
        "ORDER BY keep_rate DESC, articles_kept DESC",
        (s.wechat_promote_min_articles, s.wechat_promote_min_keep_rate))

    auto_line = s.wechat_promote_auto_keep_rate
    to_subscribe: list[dict] = []       # 自动订阅(高信噪+主题相关,或运营方已批准)
    to_queue: list[dict] = []           # 进 HITL 待批(边缘区间,或号名不相关)
    for c in cands:
        kr = c["keep_rate"] or 0.0
        if c.get("promote_status") == "approved":
            to_subscribe.append(c)
        elif kr >= auto_line and _name_ok(c.get("name") or ""):
            to_subscribe.append(c)
        else:
            to_queue.append(c)

    cap_left = max(0, s.wechat_promote_max_per_day - _promoted_today())
    picked = to_subscribe[:cap_left]    # 超上限的自动候选保留(promote_status 不变),下轮重试
    out = {"eligible": len(cands), "auto_eligible": len(to_subscribe), "queued": len(to_queue),
           "cap_left": cap_left, "promoted": 0, "failed": 0, "dry_run": dry_run,
           "candidates": [dict(c) for c in cands[:20]]}
    if dry_run:
        return out

    # 边缘候选入 HITL 队列(只对尚未入队的置 queued,不覆盖 approved/rejected/auto)
    for c in to_queue:
        db.execute("UPDATE wechat_discovered SET promote_status='queued', updated_at=now() "
                   "WHERE gh_id=%s AND promote_status IS NULL", (c["gh_id"],))

    for c in picked:
        feed_id = subscribe_fn(c["gh_id"], c.get("name") or "")
        if not feed_id:
            out["failed"] += 1
            continue
        # 登记进策展名册(feed_id 键)→ 稳定轮询接管;tier=2(一般,待运营方按需升 1)
        roster.register(feed_id, name=c.get("name") or "", tier=2)
        # 保留晋升来源以供审计:人工批准记 'approved',自动线记 'auto'(WD-13 复评#2)
        src = "approved" if c.get("promote_status") == "approved" else "auto"
        db.execute("UPDATE wechat_discovered SET promoted_at=%s, feed_id=%s, "
                   "promote_status=%s, updated_at=now() WHERE gh_id=%s",
                   (_now(), feed_id, src, c["gh_id"]))
        out["promoted"] += 1
        log.info("promote: 订阅+登记 %s(%s) keep_rate=%.2f seen=%d → feed %s",
                 c.get("name"), c["gh_id"], c["keep_rate"], c["articles_seen"], feed_id)
    return out


def prune_accounts(*, dry_run: bool = False) -> dict:
    """账号级发现的止损闸:发现自动订阅的号,若其文章累计 triage ≥prune_min 篇且 keep_rate
    低于 prune_max,停用该 roster feed(证明低信噪,不再耗轮询/抽取额度)。只动**发现订阅**
    的号(promoted_at NOT NULL),绝不碰运营方手工策展的名册。文章按 meta.feed_id 溯源聚合。"""
    s = get_settings()
    dm = float(s.wechat_deep_min)
    rows = db.query(
        "SELECT w.name, w.feed_id, "
        "  count(*) FILTER (WHERE d.triaged_at IS NOT NULL) seen, "
        "  count(*) FILTER (WHERE d.triage_score >= %s) kept "
        "FROM wechat_discovered w JOIN documents d ON d.meta->>'feed_id' = w.feed_id "
        "WHERE w.feed_id IS NOT NULL AND w.promoted_at IS NOT NULL "
        "GROUP BY w.name, w.feed_id", (dm,))
    pruned: list[dict] = []
    for r in rows:
        seen, kept = int(r["seen"]), int(r["kept"])
        if seen < s.wechat_account_prune_min_articles:
            continue
        keep_rate = round(kept / seen, 3) if seen else 0.0
        if keep_rate < s.wechat_account_prune_max_keep_rate:
            pruned.append({"feed_id": r["feed_id"], "name": r["name"],
                           "keep_rate": keep_rate, "seen": seen})
    out = {"evaluated": len(rows), "pruned": len(pruned), "dry_run": dry_run, "accounts": pruned}
    if dry_run:
        return out
    from ..ingestion import werss_api
    for p in pruned:
        roster.deactivate(p["feed_id"])               # 停止轮询
        werss_api.unsubscribe(p["feed_id"])           # 真正从 we-mp-rss 退订(不留废号继续抓)
        log.info("prune: 停用+退订低信噪发现号 %s(%s) keep_rate=%.2f seen=%d",
                 p["name"], p["feed_id"], p["keep_rate"], p["seen"])
    return out


def set_promote_status(gh_id: str, action: str) -> dict:
    """HITL 晋升审批(供 ops/Fetchy):action ∈ approve|reject|reset。
    approve → 'approved'(下轮 promote_candidates 订阅);reject → 'rejected'(永不晋升);
    reset → 回 'queued'。只对未晋升号有效。"""
    mapped = {"approve": "approved", "reject": "rejected", "reset": "queued"}.get(action)
    if not mapped:
        return {"ok": False, "detail": f"action must be approve|reject|reset, got {action!r}"}
    rows = db.query(
        "UPDATE wechat_discovered SET promote_status=%s, updated_at=now() "
        "WHERE gh_id=%s AND promoted_at IS NULL RETURNING gh_id", (mapped, gh_id))
    if not rows:
        return {"ok": False, "detail": f"未找到未晋升发现号 gh_id={gh_id!r}", "gh_id": gh_id}
    return {"ok": True, "gh_id": gh_id, "promote_status": mapped}


def promotion_stats() -> dict:
    """晋升漏斗总览(供 ops/Jarvy 观测)。"""
    s = get_settings()
    r = db.query(
        "SELECT count(*) discovered, "
        "count(*) FILTER (WHERE promoted_at IS NOT NULL) promoted, "
        "count(*) FILTER (WHERE promote_status='queued') hitl_queued, "
        # eligible_pending 与 promote_candidates 过滤口径一致:排除 rejected(WD-13 复评#4)
        "count(*) FILTER (WHERE promoted_at IS NULL AND coalesce(promote_status,'') <> 'rejected' "
        "                 AND articles_seen >= %s AND keep_rate >= %s) eligible_pending "
        "FROM wechat_discovered",
        (s.wechat_promote_min_articles, s.wechat_promote_min_keep_rate))
    return dict(r[0]) if r else {}


def hitl_queue(limit: int = 30) -> list[dict]:
    """HITL 晋升待批队列(供 Fetchy):边缘区间、等运营方批准的发现号,按 keep_rate 降序。
    加 keep_rate>=min_keep 下限:入队后 keep_rate 跌破 min 的掉队号会从 promote_candidates 消失,
    不再更新状态而滞留 queued —— 过滤掉不再够格者,避免 Fetchy 待批区显示永不订阅的僵尸号(WD-13 复评#3)。"""
    rows = db.query(
        "SELECT gh_id, name, articles_seen, articles_kept, keep_rate "
        "FROM wechat_discovered WHERE promote_status='queued' AND promoted_at IS NULL "
        "AND keep_rate >= %s "
        "ORDER BY keep_rate DESC NULLS LAST, articles_kept DESC LIMIT %s",
        (get_settings().wechat_promote_min_keep_rate, limit))
    return [dict(r) for r in rows]
