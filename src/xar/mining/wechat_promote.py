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

import httpx

from ..config import get_settings
from ..logging import get_logger
from ..storage import db
from . import roster

log = get_logger("xar.wechat_promote")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _werss_subscribe(gh_id: str, name: str) -> str | None:
    """默认订阅适配器:请 we-mp-rss 服务端订阅该号。成功返回 feed_id,失败返回 None。
    we-mp-rss 的确切订阅端点在主机 spike 期定型 —— 只需改这一处;失败安全降级。"""
    s = get_settings()
    base = s.werss_base_url.strip()
    if not base:
        log.warning("promote: WERSS_BASE_URL 未配置,无法自动订阅 %s(%s)", name, gh_id)
        return None
    headers = {"User-Agent": s.http_user_agent}
    if s.werss_api_token:
        headers["Authorization"] = f"Bearer {s.werss_api_token}"
    try:
        r = httpx.post(base.rstrip("/") + "/api/subscribe",
                       json={"gh_id": gh_id, "name": name},
                       headers=headers, timeout=30, follow_redirects=True)
        r.raise_for_status()
        data = r.json() if r.content else {}
        feed = data.get("feed_id") or data.get("id")
        if not feed:   # 200 但 body 缺 feed id(端点 spike-pending 时完全可能)—— 不能拿 gh_id
            # 冒充 feed id 幽灵订阅(会被永久标 promoted + roster 轮询错误 feed);判为未订阅重试
            log.warning("promote: we-mp-rss 订阅 %s 返回 200 但无 feed_id/id — 判为未订阅,下轮重试", gh_id)
            return None
        return str(feed)
    except Exception as e:  # noqa: BLE001
        log.warning("promote: we-mp-rss 订阅 %s 失败: %s", gh_id, str(e)[:160])
        return None


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
    """跑一轮晋升:聚合 → 选够格的号 → 每日上限内订阅并登记进策展名册。返回统计。"""
    s = get_settings()
    subscribe_fn = subscribe_fn or _werss_subscribe
    _sync_candidates()

    cands = db.query(
        "SELECT gh_id, name, articles_seen, articles_kept, keep_rate FROM wechat_discovered "
        "WHERE promoted_at IS NULL AND articles_seen >= %s AND keep_rate >= %s "
        "ORDER BY keep_rate DESC, articles_kept DESC",
        (s.wechat_promote_min_articles, s.wechat_promote_min_keep_rate))

    cap_left = max(0, s.wechat_promote_max_per_day - _promoted_today())
    picked = cands[:cap_left]
    out = {"eligible": len(cands), "cap_left": cap_left, "promoted": 0,
           "failed": 0, "dry_run": dry_run,
           "candidates": [dict(c) for c in cands[:20]]}
    if dry_run:
        return out

    for c in picked:
        feed_id = subscribe_fn(c["gh_id"], c.get("name") or "")
        if not feed_id:
            out["failed"] += 1
            continue
        # 登记进策展名册(feed_id 键)→ 稳定轮询接管;tier=2(一般,待运营方按需升 1)
        roster.register(feed_id, name=c.get("name") or "", tier=2)
        db.execute("UPDATE wechat_discovered SET promoted_at=%s, feed_id=%s, updated_at=now() "
                   "WHERE gh_id=%s", (_now(), feed_id, c["gh_id"]))
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
    for p in pruned:
        roster.deactivate(p["feed_id"])
        log.info("prune: 停用低信噪发现号 %s(%s) keep_rate=%.2f seen=%d",
                 p["name"], p["feed_id"], p["keep_rate"], p["seen"])
    return out


def promotion_stats() -> dict:
    """晋升漏斗总览(供 ops/Jarvy 观测)。"""
    s = get_settings()
    r = db.query(
        "SELECT count(*) discovered, "
        "count(*) FILTER (WHERE promoted_at IS NOT NULL) promoted, "
        "count(*) FILTER (WHERE promoted_at IS NULL AND articles_seen >= %s "
        "                 AND keep_rate >= %s) eligible_pending "
        "FROM wechat_discovered",
        (s.wechat_promote_min_articles, s.wechat_promote_min_keep_rate))
    return dict(r[0]) if r else {}
