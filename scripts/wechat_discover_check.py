"""微信「全网发现」24h 效果 + 信息质量体检(可随时跑,不改任何状态)。

用法: python3 scripts/wechat_discover_check.py
读:配置就绪度 → 发现/订阅了哪些号 → 拉到多少文章 → triage 信噪质量(vs 基线)→ 样本 → 判定。
只读,幂等。psycopg 不吃 interval 参数,故窗口用 make_interval()。
"""
from __future__ import annotations

from xar.config import get_settings
from xar.storage import db


def _hr(t):
    print("\n" + "─" * 4 + " " + t + " " + "─" * (66 - len(t)))


def main() -> None:
    s = get_settings()
    dm = float(s.wechat_deep_min)
    print("=" * 72)
    print("微信「全网发现」体检报告")
    print("=" * 72)

    # 1) 配置就绪度 ----------------------------------------------------------
    _hr("1) 配置就绪度")
    try:
        from xar.ingestion import werss_api
        acct_ready = werss_api.available()
    except Exception as e:  # noqa: BLE001
        acct_ready = False
        print("  werss_api import 失败:", e)
    print(f"  发现开关 XAR_WECHAT_DISCOVER_ENABLED = {s.wechat_discover_enabled}")
    print(f"  账号级后端就绪(we-mp-rss AK/SK) = {acct_ready}"
          + ("" if acct_ready else "   ← 未就绪:需 WERSS_AK/SK + we-mp-rss 已扫码登录"))
    print(f"  文章级后端就绪(WECHAT_SEARCH_BASE_URL) = {bool(s.wechat_search_base_url)}")
    print(f"  triage 深抽门槛 wechat_deep_min = {dm}  | 每日订阅上限 = {s.wechat_promote_max_per_day}")

    # 2) 发现/订阅了哪些号 ---------------------------------------------------
    _hr("2) 发现 → 订阅的公众号(wechat_discovered)")
    disc = db.query(
        "SELECT gh_id, name, feed_id, promoted_at, first_seen FROM wechat_discovered "
        "ORDER BY promoted_at DESC NULLS LAST, first_seen DESC")
    subscribed = [d for d in disc if d.get("feed_id") and d.get("promoted_at")]
    print(f"  发现候选号总数 = {len(disc)}  |  已自动订阅 = {len(subscribed)}")
    roster_n = db.query("SELECT count(*) n, count(*) FILTER (WHERE active) act FROM wechat_accounts")
    print(f"  策展名册 wechat_accounts:总 {roster_n[0]['n']} / active {roster_n[0]['act']}")
    for d in subscribed[:15]:
        print(f"    · {d['name']}  [{d['feed_id']}]  订阅于 {str(d['promoted_at'])[:16]}")
    if not subscribed:
        print("  (还没有通过发现订阅的号 —— 若配置就绪,等下一次 daily(约 02:00 EDT)或手动 "
              "`xar ingest-wechat-discover`)")

    feeds = [d["feed_id"] for d in subscribed if d.get("feed_id")]

    # 3) 拉到多少文章 + 信噪质量 --------------------------------------------
    _hr("3) 发现号的文章检索量 + triage 信噪质量")
    if feeds:
        agg = db.query(
            "SELECT count(*) total, "
            "count(*) FILTER (WHERE triaged_at IS NOT NULL) triaged, "
            "count(*) FILTER (WHERE triage_score >= %s) kept, "
            "count(*) FILTER (WHERE ingested_at > now() - make_interval(hours=>24)) last24, "
            "count(*) FILTER (WHERE kg_extracted_at IS NOT NULL) extracted "
            "FROM documents WHERE source='wechat' AND meta->>'feed_id' = ANY(%s)", (dm, feeds))
        a = agg[0]
        kr = (a["kept"] / a["triaged"]) if a["triaged"] else None
        print(f"  发现号文章:总 {a['total']}  |  近24h {a['last24']}  |  已 triage {a['triaged']}")
        print(f"  高信噪(triage_score≥{dm})= {a['kept']}  →  keep_rate = "
              + (f"{kr:.1%}" if kr is not None else "n/a(尚无 triage)"))
        print(f"  已进 kg 抽取 = {a['extracted']}")
        sample = db.query(
            "SELECT title, meta->>'feed_id' f, round(triage_score::numeric,2) sc "
            "FROM documents WHERE source='wechat' AND meta->>'feed_id' = ANY(%s) "
            "AND triage_score IS NOT NULL ORDER BY triage_score DESC LIMIT 8", (feeds,))
        if sample:
            print("  信噪最高样本(供人工眼检质量):")
            for r in sample:
                print(f"    [{r['sc']}] {(r['title'] or '')[:48]}  ({r['f']})")
    else:
        print("  (暂无发现号 → 无文章可评估)")

    # 4) 质量基线对照 + 止损候选 --------------------------------------------
    _hr("4) 质量基线对照 + 止损候选")
    try:
        from xar.mining import triage
        base = triage.stats()
        print(f"  全微信 triage 基线:keep_rate={base.get('keep_rate')} "
              f"(triaged={base.get('triaged')}, avg={base.get('avg_score')})")
    except Exception as e:  # noqa: BLE001
        print("  基线读取失败:", e)
    if feeds:
        prune = db.query(
            "SELECT w.name, w.feed_id, count(*) FILTER (WHERE d.triaged_at IS NOT NULL) seen, "
            "count(*) FILTER (WHERE d.triage_score >= %s) kept "
            "FROM wechat_discovered w JOIN documents d ON d.meta->>'feed_id' = w.feed_id "
            "WHERE w.feed_id IS NOT NULL AND w.promoted_at IS NOT NULL "
            "GROUP BY w.name, w.feed_id "
            "HAVING count(*) FILTER (WHERE d.triaged_at IS NOT NULL) >= %s",
            (dm, s.wechat_account_prune_min_articles))
        bad = [p for p in prune if (p["kept"] / p["seen"] if p["seen"] else 0)
               < s.wechat_account_prune_max_keep_rate]
        print(f"  样本足(≥{s.wechat_account_prune_min_articles}篇)的发现号 = {len(prune)}  |  "
              f"够格止损(keep_rate<{s.wechat_account_prune_max_keep_rate}) = {len(bad)}")
        for p in bad[:8]:
            print(f"    ✗ {p['name']} keep_rate={p['kept']}/{p['seen']}")

    # 5) 判定 ----------------------------------------------------------------
    _hr("5) 判定")
    if not (s.wechat_discover_enabled and acct_ready):
        print("  ⏸ 未就绪 —— 功能开关已开但后端未接。行动:①we-mp-rss 重扫码;②生成 AK/SK 填 "
              ".env(WERSS_AK/WERSS_SK);③docker compose up -d;④xar ingest-wechat-discover。")
    elif not subscribed:
        print("  ⚠ 已就绪但尚无订阅 —— 可能还没跑发现。手动跑 `xar ingest-wechat-discover` 观察。")
    elif not feeds or db.query("SELECT count(*) n FROM documents WHERE source='wechat' "
                               "AND meta->>'feed_id' = ANY(%s)", (feeds,))[0]["n"] == 0:
        print("  ⏳ 已订阅但文章还没拉到 —— 逐号轮询有滞后,等 glm_worker 几个周期后复查。")
    else:
        print("  ✓ 发现→订阅→检索→triage 闭环在跑。看上面 keep_rate 与样本判断信息质量;"
              "低信噪号会被止损闸自动停用。")
    print("=" * 72)


if __name__ == "__main__":
    main()
