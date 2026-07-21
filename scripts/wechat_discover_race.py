"""发现策略赛马:broad(宽泛行业词)vs overseas(美股/海外热门资产聚焦)。

同预算(各 ~20 查询、各 RACE_LIMIT 篇)分别发现 → 同一 triage 打分 → 对比 keep_rate/样本。
需 WCDA_BASE_URL 指向 wechat-download-api。用法:WCDA_BASE_URL=http://172.17.0.1:5000 python3 scripts/wechat_discover_race.py
"""
from __future__ import annotations

import time

from xar.ingestion import wechat_discover as w
from xar.storage import db

RACE_LIMIT = 24        # 每策略入库文章上限(界定预算)
N_QUERIES = 20         # 每策略查询数


def _run(strategy: str, queries: list[str]) -> dict:
    t = time.time()
    ids = w.discover_via_wcda(limit=RACE_LIMIT, queries=queries, strategy=strategy)
    return {"strategy": strategy, "queries": len(queries), "ingested": len(ids),
            "secs": round(time.time() - t)}


def _triage_all() -> None:
    from xar.mining import triage
    from xar.models import llm
    from xar.orchestration import glm_worker as gw

    with llm.pinned(gw._fetchy_pin(gw.fetchy_config())):
        for _ in range(3):                      # 多轮直到清空(每轮 up to 40)
            r = triage.triage_pending(limit=40, run_id="strategy-race")
            if not r.get("triaged"):
                break


def _report(strategy: str) -> None:
    r = db.query(
        "SELECT count(*) total, count(*) FILTER (WHERE triaged_at IS NOT NULL) triaged, "
        "count(*) FILTER (WHERE triage_score >= 0.4) kept, "
        "round(avg(triage_score)::numeric, 3) avg, count(DISTINCT meta->>'account') accts "
        "FROM documents WHERE meta->>'strategy' = %s", (strategy,))
    a = r[0]
    kr = (a["kept"] / a["triaged"]) if a["triaged"] else 0
    print(f"\n【{strategy}】 账号 {a['accts']} · 入库 {a['total']} · triaged {a['triaged']} · "
          f"kept {a['kept']} · keep_rate {kr:.0%} · avg {a['avg']}")
    for x in db.query(
            "SELECT round(triage_score::numeric,2) sc, title, meta->>'account' acct "
            "FROM documents WHERE meta->>'strategy'=%s AND triage_score>=0.4 "
            "ORDER BY triage_score DESC LIMIT 6", (strategy,)):
        print(f"    ✓[{x['sc']}] {(x['title'] or '')[:44]} | {x['acct']}")


def main() -> None:
    broad_q = w._precise_queries()[::9][:N_QUERIES]        # 跨主题跨步采样(代表 broad 全貌)
    overseas_q = w._overseas_queries()[:N_QUERIES]
    print("=" * 74)
    print("发现策略赛马:broad(宽泛行业词) vs overseas(美股/海外热门资产)")
    print("=" * 74)
    print("broad 样本:", " ".join(broad_q[:12]))
    print("overseas 样本:", " ".join(overseas_q[:12]))
    print("\n-- 发现中(各同预算)--")
    print(_run("broad_race", broad_q))
    print(_run("overseas_race", overseas_q))
    print("\n-- triage 打分中 --")
    _triage_all()
    print("\n" + "=" * 74 + "\n判定(keep_rate 高者胜;看样本投研相关性)")
    _report("broad_race")
    _report("overseas_race")
    print("=" * 74)


if __name__ == "__main__":
    main()
