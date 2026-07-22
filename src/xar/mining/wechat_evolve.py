"""发现查询的**持续进化赛马引擎** —— 多臂老虎机(UCB)over 查询空间。

目标:稳定的 广覆盖 + 高信噪 + 高频更新。做法:
  · 广覆盖  = 大候选池(全本体主题/路线/公司名 + 海外资产词 + 从高信噪内容挖的新词)+ 每轮探索前沿。
  · 高信噪  = 按 triage keep_rate 反馈,UCB 利用高命中率查询(每查询一个「臂」,持续赛马)。
  · 高频    = 轻量、可频繁调度(select→discover→triage→反馈闭环)。

每轮:① update_query_stats(从 documents.meta.query 聚合 triage 反馈刷新 keep_rate)
     → ② mine_new_queries(从高信噪标题挖新候选词入池,拓覆盖)
     → ③ select_queries(UCB 选一批:利用高 keep + 探索低 runs)→ 调 discover_via_wcda 落库。
下一轮 ① 自动吃到这批的 triage 结果 —— 闭环自进化。keep_rate 是赛马的持续裁判。
"""
from __future__ import annotations

import math
import re
from collections import Counter

from ..config import get_settings
from ..ingestion.registry import COMPANIES
from ..logging import get_logger
from ..storage import db

log = get_logger("xar.wechat_evolve")

_CJK_RE = re.compile(r"[一-鿿]")
_EXPLORE_FRAC = 0.35        # 每轮探索(低 runs)占比,其余利用(高 keep_rate)
_MIN_SAMPLE = 3            # 有 >=N 篇样本才算「已评估」,可参与利用
_UCB_C = 0.25             # 探索系数


def _has_cjk(s: str) -> bool:
    return bool(_CJK_RE.search(s or ""))


def _candidate_queries() -> dict[str, str]:
    """候选臂池(query→strategy)= 高信噪默认集 + 海外聚焦 + 公司名(覆盖,bandit 自会淘汰败者)
    + 已挖的新词。first-wins 保留最强 strategy 标签。这是**覆盖广度**的来源。"""
    from ..ingestion import wechat_discover as wd

    out: dict[str, str] = {}
    for q in wd._precise_queries():
        out.setdefault(q, "broad")
    for q in wd._overseas_queries():
        out.setdefault(q, "overseas")
    for c in COMPANIES:                        # 公司中文名(覆盖;败者由 keep_rate 淘汰)
        for a in [c.get("name", ""), *c.get("aliases", [])]:
            a = (a or "").strip()
            if _has_cjk(a) and 2 <= len(a) <= 8:
                out.setdefault(a, "company")
    for r in db.query("SELECT query, strategy FROM wechat_query_stats "
                      "WHERE strategy IN ('mined','sub_mined','kg_mined')"):
        out.setdefault(r["query"], r["strategy"])   # 挖的新词:发现标题/订阅正文/KG 实体(拓覆盖+反哺)
    return out


def update_query_stats() -> int:
    """从 documents(meta.query)聚合 triage 反馈,刷新每查询的 articles/kept/keep_rate。
    这是赛马的**裁判**:命中率完全由真实 triage 结果决定。返回更新的查询数。"""
    dm = float(get_settings().wechat_deep_min)
    rows = db.query(
        "SELECT meta->>'query' q, count(*) FILTER (WHERE triaged_at IS NOT NULL) articles, "
        "count(*) FILTER (WHERE triage_score >= %s) kept, "
        "count(DISTINCT meta->>'gh_id') accts "
        "FROM documents WHERE source='wechat' AND meta->>'via'='discover' "
        "AND coalesce(meta->>'query','') <> '' AND triaged_at IS NOT NULL "
        "GROUP BY meta->>'query'", (dm,))
    for r in rows:
        art, kept = int(r["articles"]), int(r["kept"])
        kr = round(kept / art, 4) if art else None
        db.execute(
            "INSERT INTO wechat_query_stats (query, articles, kept, keep_rate, accounts) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (query) DO UPDATE SET "
            "articles=EXCLUDED.articles, kept=EXCLUDED.kept, keep_rate=EXCLUDED.keep_rate, "
            "accounts=EXCLUDED.accounts",
            (r["q"], art, kept, kr, int(r["accts"])))
    return len(rows)


def _cjk_grams(texts: list[str], pool: set[str], top: int) -> list[str]:
    """从一批文本挖高频 CJK n-gram(长 2-4,≥3 次、不在池中)→ 返回 top 个新词。"""
    cnt: Counter = Counter()
    for t in texts:
        for chunk in re.findall(r"[一-鿿]{2,8}", t or ""):
            for length in (2, 3, 4):
                for i in range(len(chunk) - length + 1):
                    cnt[chunk[i:i + length]] += 1
    out: list[str] = []
    for gram, c in cnt.most_common(400):
        if len(out) >= top:
            break
        if c >= 3 and gram not in pool and len(gram) >= 2:
            out.append(gram)
    return out


def _insert_mined(grams: list[str], strategy: str, pool: set[str]) -> int:
    added = 0
    for g in grams:
        if g in pool:
            continue
        db.execute("INSERT INTO wechat_query_stats (query, strategy, runs) "
                   "VALUES (%s,%s,0) ON CONFLICT (query) DO NOTHING", (g, strategy))
        pool.add(g)
        added += 1
    return added


def mine_new_queries(top: int = 6) -> int:
    """从**三源**挖新查询臂(拓覆盖 + WCDA↔werss 反哺闭环);入池后由 bandit 以 keep_rate 自纠正:
      1) WCDA 发现流 kept 标题 → 'mined'(开放式拓覆盖,让进化跳出本体词表)。
      2) werss 订阅流 kept 标题 → 'sub_mined'(订阅流信噪 ~81% 远高于发现流,更干净的挖词源)。
      3) KG 抽出的实体名(kg_nodes,源自 kept 微信文档)→ 'kg_mined'(图谱新标的/技术 → 下一轮搜索词)。
    这就是「订阅优质内容 → 更准搜索词 → WCDA 搜得更广 → 更多好号晋升」的持续增强环。"""
    dm = float(get_settings().wechat_deep_min)
    pool = set(_candidate_queries())
    added = 0

    disc = [r["title"] for r in db.query(
        "SELECT title FROM documents WHERE source='wechat' AND meta->>'via'='discover' "
        "AND triage_score >= %s AND title IS NOT NULL", (dm,))]
    added += _insert_mined(_cjk_grams(disc, pool, top), "mined", pool)

    sub = [r["title"] for r in db.query(   # werss 订阅流(feed_id 非空)= 更高信噪反哺源
        "SELECT title FROM documents WHERE source='wechat' AND coalesce(meta->>'feed_id','') <> '' "
        "AND triage_score >= %s AND title IS NOT NULL", (dm,))]
    added += _insert_mined(_cjk_grams(sub, pool, top), "sub_mined", pool)

    try:                                    # KG 实体:图谱把正文里的具体标的/技术抽成了节点名
        # 有界:只看**近 300 篇** kept 微信文档的边(而非全 KG),整体 LIMIT 500 —— 避免
        # 随文档/边数增长每轮全表扫(WD-13 复评#5:原 `OR` 阻索引、无 LIMIT、无时间窗)。
        ents = [r["name"] for r in db.query(
            "SELECT DISTINCT n.name FROM kg_nodes n "
            "JOIN kg_edges e ON n.id IN (e.src_id, e.dst_id) "
            "WHERE e.source_doc_id IN ("
            "  SELECT id FROM documents WHERE source='wechat' AND triage_score >= %s "
            "  ORDER BY triaged_at DESC NULLS LAST LIMIT 300) "
            "AND n.name IS NOT NULL LIMIT 500", (dm,))]
        kg_terms = [e for e in ents if _has_cjk(e) and 2 <= len(e) <= 8 and e not in pool][:top]
        added += _insert_mined(kg_terms, "kg_mined", pool)
    except Exception as e:  # noqa: BLE001 — KG 挖词是尽力而为,不得拖垮赛马主流程
        log.warning("wechat_evolve KG 挖词跳过: %s", str(e)[:120])

    if added:
        log.info("wechat_evolve mined %d new candidate queries (title/sub/kg)", added)
    return added


_MINED_STRATEGIES = ("mined", "sub_mined", "kg_mined")


def prune_query_pool() -> int:
    """删已证明无用的**挖词**(跑过 ≥2 次仍 0 命中,或 keep_rate<5%)→ 池不膨胀、长期稳定。
    只删挖词(mined/sub_mined/kg_mined);本体词/海外词/公司名永久保留作覆盖底座,bandit 靠低 UCB 自然少选。"""
    q_in = "('mined','sub_mined','kg_mined')"
    before = db.query(f"SELECT count(*) n FROM wechat_query_stats WHERE strategy IN {q_in}")[0]["n"]
    db.execute(f"DELETE FROM wechat_query_stats WHERE strategy IN {q_in} AND runs >= 2 "
               "AND (articles = 0 OR keep_rate < 0.05)")
    after = db.query(f"SELECT count(*) n FROM wechat_query_stats WHERE strategy IN {q_in}")[0]["n"]
    pruned = int(before) - int(after)
    if pruned:
        log.info("wechat_evolve pruned %d dud mined queries (pool stays bounded)", pruned)
    return pruned


def _query_theme_index() -> dict[str, str]:
    """query → 主题标签(主题均衡选臂用)。主题词→其 theme;路线词→归属 theme(ROUTE_THEMES 首个);
    海外资产词→'overseas';公司名/挖词等→'other'。"""
    from ..ingestion import wechat_discover as wd
    from ..ingestion.registry import ROUTE_THEMES
    from ..ontology import cn_routing

    idx: dict[str, str] = {}
    for theme, terms in cn_routing.CN_THEME_TERMS.items():
        for t in terms:
            idx.setdefault(t, theme)
    for route, terms in cn_routing.CN_ROUTE_TERMS.items():
        ths = ROUTE_THEMES.get(route)
        th = ths[0] if isinstance(ths, (list, tuple)) and ths else "route"
        for t in terms:
            idx.setdefault(t, th)
    for t in wd._OVERSEAS_ASSET_TERMS:
        idx.setdefault(t, "overseas")
    return idx


def select_queries(n: int = 40) -> list[str]:
    """一轮进化选臂:先刷新反馈 + 挖新词 + 剪枝,再 **主题均衡** UCB 选 n 个:利用段每主题限额(防
    光模块霸榜)、探索段按主题 round-robin 保底(每主题都拿探索位)、最终列表按主题交错(让 discover
    的广度优先收号看到主题均匀分布)。治「光模块单一化」;keep_rate 仍是赛马裁判。"""
    update_query_stats()
    mine_new_queries()
    prune_query_pool()
    cands = _candidate_queries()
    stats = {r["query"]: r for r in db.query(
        "SELECT query, runs, articles, kept FROM wechat_query_stats")}
    total_runs = sum(int(s["runs"]) for s in stats.values()) + 1

    def _bayes(q: str) -> float:
        s = stats.get(q)
        if not s or not s["articles"]:
            return 0.5                          # 无样本 → 中性先验
        return (int(s["kept"]) + 1) / (int(s["articles"]) + 2)   # Laplace 平滑

    def _runs(q: str) -> int:
        s = stats.get(q)
        return int(s["runs"]) if s else 0

    def _ucb(q: str) -> float:
        return _bayes(q) + _UCB_C * math.sqrt(math.log(total_runs) / (_runs(q) + 1))

    theme_idx = _query_theme_index()

    def _theme(q: str) -> str:
        return theme_idx.get(q, "other")

    all_q = list(cands)
    explore_n = max(1, int(n * _EXPLORE_FRAC))
    exploit_n = n - explore_n
    evaluated = sorted([q for q in all_q if stats.get(q) and int(stats[q]["articles"]) >= _MIN_SAMPLE],
                       key=_ucb, reverse=True)

    # 利用:已评估按 UCB 降序,但**每主题限额**(单一主题不得霸榜 exploit → 治光模块单一化)。
    n_themes = max(1, len({_theme(q) for q in all_q}))
    per_theme_cap = max(2, exploit_n // n_themes)
    picked: list[str] = []
    tct: Counter = Counter()
    for q in evaluated:
        if len(picked) >= exploit_n:
            break
        if tct[_theme(q)] < per_theme_cap:
            picked.append(q)
            tct[_theme(q)] += 1
    for q in evaluated:                     # 限额太紧没填满 → 放宽补齐(仍从已评估)
        if len(picked) >= exploit_n:
            break
        if q not in picked:
            picked.append(q)

    # 探索:剩余按主题分桶,round-robin 取「runs 最少」的 → **每主题都拿到探索位**(覆盖前沿)。
    picked_set = set(picked)
    by_theme: dict[str, list[str]] = {}
    for q in sorted([q for q in all_q if q not in picked_set], key=lambda q: (_runs(q), -_ucb(q))):
        by_theme.setdefault(_theme(q), []).append(q)
    while len(picked) < n and any(by_theme.values()):
        for th in list(by_theme):
            if len(picked) >= n:
                break
            if by_theme[th]:
                picked.append(by_theme[th].pop(0))

    # 交错:按主题 round-robin 重排 → discover 广度优先收号看到主题均匀分布(否则 exploit 光模块全在前)。
    inter: dict[str, list[str]] = {}
    for q in picked:
        inter.setdefault(_theme(q), []).append(q)
    ordered: list[str] = []
    while any(inter.values()):
        for th in list(inter):
            if inter[th]:
                ordered.append(inter[th].pop(0))
    picked = ordered

    # 记 runs + strategy + last_run
    for q in picked:
        db.execute(
            "INSERT INTO wechat_query_stats (query, strategy, runs, last_run) "
            "VALUES (%s,%s,1,now()) ON CONFLICT (query) DO UPDATE SET "
            "runs = wechat_query_stats.runs + 1, last_run = now(), "
            "strategy = COALESCE(wechat_query_stats.strategy, EXCLUDED.strategy)",
            (q, cands.get(q, "broad")))
    log.info("wechat_evolve select: %d picked across %d themes, pool=%d", len(picked), n_themes, len(all_q))
    return picked


def leaderboard(top: int = 15) -> dict:
    """赛马榜:命中率最高/最低查询 + 池子概况(供观测)。"""
    winners = db.query(
        "SELECT query, strategy, articles, kept, keep_rate, runs FROM wechat_query_stats "
        "WHERE articles >= %s ORDER BY keep_rate DESC NULLS LAST, kept DESC LIMIT %s",
        (_MIN_SAMPLE, top))
    losers = db.query(
        "SELECT query, strategy, articles, kept, keep_rate FROM wechat_query_stats "
        "WHERE articles >= %s ORDER BY keep_rate ASC NULLS LAST LIMIT %s", (_MIN_SAMPLE, top))
    agg = db.query(
        "SELECT count(*) pool, count(*) FILTER (WHERE runs > 0) tried, "
        "count(*) FILTER (WHERE articles >= %s) evaluated, "
        "count(*) FILTER (WHERE strategy IN ('mined','sub_mined','kg_mined')) mined, "
        "round(avg(keep_rate) FILTER (WHERE articles >= %s)::numeric,3) avg_keep "
        "FROM wechat_query_stats", (_MIN_SAMPLE, _MIN_SAMPLE))
    return {"summary": dict(agg[0]) if agg else {},
            "winners": [dict(r) for r in winners], "losers": [dict(r) for r in losers]}
