"""发现查询进化引擎(mining/wechat_evolve)—— UCB 选臂 + triage 反馈 + 挖词。全 mock,离线。"""
from __future__ import annotations

from xar.mining import wechat_evolve as we


class _S:
    wechat_deep_min = 0.4


def test_select_exploits_high_keep_and_explores_untested(monkeypatch):
    """UCB 选臂:高 keep_rate 被利用 + 未测查询被探索;每个 picked 记 runs。"""
    monkeypatch.setattr(we, "update_query_stats", lambda: 0)
    monkeypatch.setattr(we, "mine_new_queries", lambda: 0)
    monkeypatch.setattr(we, "prune_query_pool", lambda: 0)
    pool = {f"eval{i}": "broad" for i in range(10)}
    pool.update({"光模块": "broad", "untested1": "broad", "untested2": "mined"})
    monkeypatch.setattr(we, "_candidate_queries", lambda: pool)
    stats = [{"query": f"eval{i}", "runs": 5, "articles": 10, "kept": i} for i in range(10)]
    stats.append({"query": "光模块", "runs": 5, "articles": 10, "kept": 9})   # keep 0.9,最高
    monkeypatch.setattr(we.db, "query",
                        lambda sql, params=None: stats if "runs, articles, kept" in sql else [])
    executed: list = []
    monkeypatch.setattr(we.db, "execute", lambda sql, params=None: executed.append(params))
    picked = we.select_queries(6)
    assert "光模块" in picked                                   # 高 keep → 利用
    assert any(p in ("untested1", "untested2") for p in picked)  # 未测 → 探索(覆盖前沿)
    assert len(picked) == 6 and len(executed) == 6              # 每个 picked 记一次 runs


def test_update_query_stats_aggregates_keep_rate_from_docs(monkeypatch):
    """命中率完全由 documents(meta.query)的真实 triage 结果决定(赛马裁判)。"""
    monkeypatch.setattr(we, "get_settings", lambda: _S())
    rows = [{"q": "AI存储", "articles": 10, "kept": 7, "accts": 3}]
    monkeypatch.setattr(we.db, "query",
                        lambda sql, params=None: rows if "GROUP BY meta" in sql else [])
    ex: list = []
    monkeypatch.setattr(we.db, "execute", lambda sql, params=None: ex.append(params))
    n = we.update_query_stats()
    assert n == 1 and ex[0][0] == "AI存储" and ex[0][3] == 0.7   # keep_rate = 7/10


def test_mine_new_queries_adds_frequent_ngrams_not_in_pool(monkeypatch):
    """从高信噪标题挖高频 CJK 新词(不在池)→ 入池 mined(开放式拓覆盖)。"""
    monkeypatch.setattr(we, "get_settings", lambda: _S())
    titles = [{"title": "存储涨价新周期"}, {"title": "存储涨价了"}, {"title": "存储涨价预测2028"}]
    monkeypatch.setattr(we.db, "query",
                        lambda sql, params=None: titles if "triage_score" in sql else [])
    monkeypatch.setattr(we, "_candidate_queries", lambda: {"光模块": "broad"})   # 池无「存储涨价」
    added: list = []
    monkeypatch.setattr(we.db, "execute", lambda sql, params=None: added.append(params[0]))
    n = we.mine_new_queries(top=4)
    assert n >= 1 and any("存储" in a for a in added)            # 挖出高频新词


def test_mine_no_titles_is_noop(monkeypatch):
    monkeypatch.setattr(we, "get_settings", lambda: _S())
    monkeypatch.setattr(we.db, "query", lambda sql, params=None: [])
    assert we.mine_new_queries() == 0


def test_prune_removes_dud_mined_queries(monkeypatch):
    """剪枝:删跑过≥2次仍无用的 mined 查询 → 池有界、长期稳定。"""
    counts = iter([{"n": 5}, {"n": 3}])          # before=5, after=3 → pruned 2
    calls: list = []

    def _q(sql, params=None):
        if "count(*) n FROM wechat_query_stats WHERE strategy" in sql:
            return [next(counts)]
        return []

    monkeypatch.setattr(we.db, "query", _q)
    monkeypatch.setattr(we.db, "execute", lambda sql, params=None: calls.append(sql))
    n = we.prune_query_pool()
    assert n == 2 and any("DELETE" in c and "mined" in c for c in calls)
