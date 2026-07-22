"""微信「全网发现」双轨制体检(可随时跑,只读、幂等,不改任何状态)。

双轨:WCDA = 全网搜索引擎(广度,meta.via='discover');werss = 优质订阅名册(subscribe+小时轮询,
meta.feed_id)。晋升桥把 WCDA 证明的高信噪号混合晋升(自动/HITL)进 werss;反哺闭环从订阅正文/KG
实体挖 WCDA 新搜索词。本报告分轨看检索量/信噪、晋升漏斗、主题覆盖(治单一化)、进化赛马、名册。

用法: python3 scripts/wechat_discover_check.py
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
    print("微信「全网发现」双轨制体检报告")
    print("=" * 72)

    # 1) 配置就绪度 + 会话健康 -----------------------------------------------
    _hr("1) 配置就绪度(WCDA 搜索 / werss 订阅)")
    from xar.ingestion import wcda_api, werss_api
    wcda_ready = False
    try:
        wcda_ready = bool(s.wechat_discover_enabled) and wcda_api.available()
    except Exception as e:  # noqa: BLE001
        print("  wcda_api 检查失败:", e)
    werss_ready = False
    try:
        werss_ready = werss_api.available()
    except Exception as e:  # noqa: BLE001
        print("  werss_api 检查失败:", e)
    print(f"  发现开关 XAR_WECHAT_DISCOVER_ENABLED = {s.wechat_discover_enabled}")
    print(f"  WCDA 搜索引擎就绪(WCDA_BASE_URL) = {wcda_ready}"
          + ("" if wcda_ready else "   ← 未就绪:设 WCDA_BASE_URL 指向 wechat-download-api"))
    print(f"  werss 订阅后端就绪(AK/SK) = {werss_ready}"
          + ("" if werss_ready else "   ← 晋升订阅需 WERSS_AK/SK + we-mp-rss 已扫码会话"))
    # 会话健康探针:一次轻量 search,看是否返回(会话过期返回空)—— 只影响新订阅,不影响轮询
    if werss_ready:
        try:
            probe = werss_api.search_accounts("光模块", limit=1)
            print(f"  werss 会话探针: {'OK(可订阅)' if probe else '空返回 —— 会话可能过期,需重扫码(不影响已订阅轮询)'}")
        except Exception as e:  # noqa: BLE001
            print(f"  werss 会话探针: 失败 {str(e)[:60]}(host 侧须用网关 IP 而非 werss:8001)")
    print(f"  triage 深抽门槛={dm} | 晋升:min_articles={s.wechat_promote_min_articles} "
          f"auto_keep={s.wechat_promote_auto_keep_rate} 每日上限={s.wechat_promote_max_per_day} "
          f"| 止损:min_articles={s.wechat_account_prune_min_articles} max_keep={s.wechat_account_prune_max_keep_rate}")

    # 2) 双轨分层信噪(WCDA 发现流 vs werss 订阅流 vs 全微信基线)-------------
    _hr("2) 双轨分层信噪(keep_rate)")
    strata = db.query(
        "SELECT CASE WHEN meta->>'via'='discover' THEN 'WCDA发现流' "
        "  WHEN coalesce(meta->>'feed_id','')<>'' THEN 'werss订阅流' ELSE '其它' END strat, "
        "count(*) FILTER (WHERE triaged_at IS NOT NULL) triaged, "
        "count(*) FILTER (WHERE triage_score >= %s) kept, "
        "count(*) FILTER (WHERE ingested_at > now() - make_interval(hours=>24)) last24, "
        "count(*) FILTER (WHERE kg_extracted_at IS NOT NULL) extracted, "
        "round(avg(triage_score) FILTER (WHERE triaged_at IS NOT NULL)::numeric,3) avg_s "
        "FROM documents WHERE source='wechat' AND triaged_at IS NOT NULL GROUP BY 1 ORDER BY 1", (dm,))
    for r in strata:
        kr = (r["kept"] / r["triaged"]) if r["triaged"] else 0
        print(f"  {r['strat']:10} triaged={r['triaged']:4} keep_rate={kr:5.1%} avg={r['avg_s']} "
              f"近24h={r['last24']:3} 入KG={r['extracted']}")

    # 3) 晋升漏斗 + HITL 待批队列 --------------------------------------------
    _hr("3) 晋升漏斗:WCDA 发现 → werss 订阅")
    try:
        from xar.mining import wechat_promote as wp
        ps = wp.promotion_stats()
        print(f"  发现候选={ps.get('discovered')} | 已晋升订阅={ps.get('promoted')} "
              f"| HITL 待批={ps.get('hitl_queued')} | 够格待晋升={ps.get('eligible_pending')}")
        q = wp.hitl_queue(8)
        if q:
            print("  HITL 待批(运营方批准后订阅):")
            for c in q:
                print(f"    · {(c['name'] or '?')[:22]:22} keep={c['keep_rate'] or 0:.2f} seen={c['articles_seen']}")
    except Exception as e:  # noqa: BLE001
        print("  晋升漏斗读取失败:", str(e)[:80])

    # 4) 主题覆盖分布(治「光模块单一化」的验收指标)--------------------------
    _hr("4) 主题覆盖分布(发现流,按 documents.theme)")
    themes = db.query(
        "SELECT coalesce(theme,'(未定)') theme, count(*) n, "
        "count(*) FILTER (WHERE triage_score >= %s) kept "
        "FROM documents WHERE source='wechat' AND meta->>'via'='discover' AND triaged_at IS NOT NULL "
        "GROUP BY theme ORDER BY n DESC", (dm,))
    if themes:
        for r in themes:
            print(f"    {r['theme']:20} 文章={r['n']:4} kept={r['kept']}")
        if len([r for r in themes if r["theme"] != "(未定)"]) <= 1:
            print("  ⚠ 覆盖仍单一 —— 广度优先收号 + 主题均衡选臂上线后应逐轮变宽")

    # 5) 进化赛马 + 反哺挖词 --------------------------------------------------
    _hr("5) 查询进化赛马 + 反哺挖词")
    try:
        from xar.mining import wechat_evolve
        lb = wechat_evolve.leaderboard(10)
        print("  池概况:", lb["summary"])
        mined = db.query("SELECT strategy, count(*) n FROM wechat_query_stats "
                         "WHERE strategy IN ('mined','sub_mined','kg_mined') GROUP BY strategy")
        print("  反哺挖词:", {r["strategy"]: r["n"] for r in mined} or "(尚无)")
        print("  高命中率查询(利用):")
        for w in lb["winners"][:8]:
            print(f"    +[{w['keep_rate']}] {w['query'][:16]:16} ({w['kept']}/{w['articles']}) [{w['strategy']}]")
    except Exception as e:  # noqa: BLE001
        print("  (进化榜不可用:", str(e)[:80], ")")

    # 6) werss 名册(订阅名册,tier 分布)------------------------------------
    _hr("6) werss 订阅名册(wechat_accounts)")
    roster_rows = db.query(
        "SELECT tier, count(*) FILTER (WHERE active) act, count(*) FILTER (WHERE NOT active) inact "
        "FROM wechat_accounts GROUP BY tier ORDER BY tier")
    if roster_rows:
        for r in roster_rows:
            print(f"    tier{r['tier']}  在册(active)={r['act']}  已停用(pruned)={r['inact']}")
    else:
        print("    (名册为空 —— 手工 tier-1 核心走 WERSS_FEEDS;tier-2 由晋升桥自动增补)")

    # 7) 判定 ----------------------------------------------------------------
    _hr("7) 判定")
    if not wcda_ready:
        print("  ⏸ WCDA 搜索引擎未就绪 —— 设 WCDA_BASE_URL(发现的唯一搜索腿)。")
    elif not werss_ready:
        print("  ⚠ WCDA 在跑但 werss 订阅未就绪 —— 好号无法沉淀进名册;配 WERSS_AK/SK + 重扫码。")
    else:
        print("  ✓ 双轨就绪。WCDA 广度发现 → triage → 高信噪号混合晋升(自动/HITL)→ werss 小时轮询;"
              "\n    订阅优质内容 + KG 实体反哺 WCDA 搜索词;低信噪号止损(停用+退订)。")
    print("=" * 72)


if __name__ == "__main__":
    main()
