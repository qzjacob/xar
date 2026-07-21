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
    for r in db.query("SELECT query FROM wechat_query_stats WHERE strategy='mined'"):
        out.setdefault(r["query"], "mined")     # 从内容挖的新词(开放式拓覆盖)
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


def mine_new_queries(top: int = 6) -> int:
    """从高信噪(kept)文章标题挖高频 CJK n-gram 新词(不在池中)→ 入池 strategy='mined'。
    开放式拓覆盖:让进化跳出本体词表;噪音候选会被 bandit 以低 keep_rate 淘汰(自纠正)。"""
    dm = float(get_settings().wechat_deep_min)
    titles = [r["title"] for r in db.query(
        "SELECT title FROM documents WHERE source='wechat' AND meta->>'via'='discover' "
        "AND triage_score >= %s AND title IS NOT NULL", (dm,))]
    if not titles:
        return 0
    pool = set(_candidate_queries())
    cnt: Counter = Counter()
    for t in titles:
        for chunk in re.findall(r"[一-鿿]{2,8}", t or ""):
            for length in (2, 3, 4):
                for i in range(len(chunk) - length + 1):
                    cnt[chunk[i:i + length]] += 1
    added = 0
    for gram, c in cnt.most_common(300):
        if added >= top:
            break
        if c >= 3 and gram not in pool and len(gram) >= 2:
            db.execute("INSERT INTO wechat_query_stats (query, strategy, runs) "
                       "VALUES (%s,'mined',0) ON CONFLICT (query) DO NOTHING", (gram,))
            added += 1
    if added:
        log.info("wechat_evolve mined %d new candidate queries", added)
    return added


def prune_query_pool() -> int:
    """删已证明无用的 **mined** 查询(跑过 ≥2 次仍 0 命中,或 keep_rate<5%)→ 池不膨胀、长期稳定。
    只删 mined(本体词/海外词/公司名永久保留作覆盖底座,bandit 靠低 UCB 自然少选它们)。"""
    before = db.query("SELECT count(*) n FROM wechat_query_stats WHERE strategy='mined'")[0]["n"]
    db.execute("DELETE FROM wechat_query_stats WHERE strategy='mined' AND runs >= 2 "
               "AND (articles = 0 OR keep_rate < 0.05)")
    after = db.query("SELECT count(*) n FROM wechat_query_stats WHERE strategy='mined'")[0]["n"]
    pruned = int(before) - int(after)
    if pruned:
        log.info("wechat_evolve pruned %d dud mined queries (pool stays bounded)", pruned)
    return pruned


def select_queries(n: int = 40) -> list[str]:
    """一轮进化选臂:先刷新反馈 + 挖新词 + 剪枝(稳定),再 UCB 选 n 个(利用高 keep + 探索低 runs)。"""
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

    all_q = list(cands)
    explore_n = max(1, int(n * _EXPLORE_FRAC))
    exploit_n = n - explore_n
    # 利用:已评估(articles>=MIN_SAMPLE)按 UCB 降序(高 keep_rate 优先)
    evaluated = sorted([q for q in all_q if stats.get(q) and int(stats[q]["articles"]) >= _MIN_SAMPLE],
                       key=_ucb, reverse=True)
    picked: list[str] = evaluated[:exploit_n]
    # 探索:剩余里 runs 最少的(未评估/新词优先)—— 覆盖前沿
    rest = sorted([q for q in all_q if q not in picked], key=lambda q: (_runs(q), -_ucb(q)))
    for q in rest:
        if len(picked) >= n:
            break
        picked.append(q)
    # 记 runs + strategy + last_run
    for q in picked:
        db.execute(
            "INSERT INTO wechat_query_stats (query, strategy, runs, last_run) "
            "VALUES (%s,%s,1,now()) ON CONFLICT (query) DO UPDATE SET "
            "runs = wechat_query_stats.runs + 1, last_run = now(), "
            "strategy = COALESCE(wechat_query_stats.strategy, EXCLUDED.strategy)",
            (q, cands.get(q, "broad")))
    log.info("wechat_evolve select: %d picked (%d exploit / %d explore), pool=%d",
             len(picked), min(exploit_n, len(evaluated)), len(picked) - min(exploit_n, len(evaluated)),
             len(all_q))
    return picked


def leaderboard(top: int = 15) -> dict:
    """赛马榜:命中率最高/最低查询 + 池子概况(供观测)。"""
    dm = float(get_settings().wechat_deep_min)
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
        "count(*) FILTER (WHERE strategy='mined') mined, "
        "round(avg(keep_rate) FILTER (WHERE articles >= %s)::numeric,3) avg_keep "
        "FROM wechat_query_stats", (_MIN_SAMPLE, _MIN_SAMPLE))
    return {"summary": dict(agg[0]) if agg else {},
            "winners": [dict(r) for r in winners], "losers": [dict(r) for r in losers]}
