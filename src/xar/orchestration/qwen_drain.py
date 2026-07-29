"""常驻本地 qwen 抽取 drain(docker 服务 qwendrain)。

把 bulk KG+expert 从 glm_worker 的串行轮里**解耦**到独立常驻进程:持续把 pending 文档
(pipeline_priority 优先序:alphapai/aifinmarket 先)KG+expert 抽取,钉扎 qwen3-14b-local
(单元素 pin = 链外无回退,永不落云端计量),**原子 SKIP-LOCKED 领取并当场盖 kg_extracted_at**
(与 glm_worker/自身多 worker 都不双抽 —— build_kg 的先 SELECT 后盖戳不具此安全性,故 glm_worker
默认关 bulk_extract)。把本地 3090 喂满,不再被 glm_worker 的慢云端 thesis 阶段挟持而空转。

崩溃/重启幂等:extract_from_document 的 add_event/add_edge 去重、process_document 的 ON CONFLICT
upsert;领取盖戳后失败即跳过(毒文档语义,同 build_kg)。SIGTERM→干净退出。
"""
from __future__ import annotations

import signal
import time
from concurrent.futures import ThreadPoolExecutor

from ..config import get_settings
from ..logging import get_logger
from ..models import llm
from ..pipeline_priority import (DEFAULT_TAIL_DEPTH_ALPHA, STRICT_PRIORITY_ORDER,
                                 effective_tail_weight, tier_order_sql)
from ..storage import db

log = get_logger("xar.qwen_drain")


def _exclude() -> list[str]:
    return [s.strip() for s in (get_settings().qwen_drain_exclude_sources or "").split(",")
            if s.strip()]


def _claim_sql(n: int, *, only: list[str] | None = None,
               exclude_sources: list[str] | None = None) -> list[str]:
    """原子领取 n 篇(SKIP LOCKED + 当场盖戳 → 并发不双抽)。
    only:限定源集合;exclude_sources:排除源集合。按严格档位序 + 新→旧。"""
    if n <= 0:
        return []
    where = ["kg_extracted_at IS NULL", "permission<>'red'"]
    params: list = []
    excl = _exclude()
    if excl:
        where.append("source <> ALL(%s)")
        params.append(excl)
    if only is not None:
        if not only:
            return []
        where.append("source = ANY(%s)")
        params.append(list(only))
    if exclude_sources:
        where.append("source <> ALL(%s)")
        params.append(list(exclude_sources))
    params.append(n)
    return [r["id"] for r in db.query(
        "UPDATE documents SET kg_extracted_at=now() WHERE id IN ("
        f"  SELECT id FROM documents WHERE {' AND '.join(where)}"
        f"  ORDER BY {tier_order_sql('source')} ASC, published_at DESC NULLS LAST"
        "  LIMIT %s FOR UPDATE SKIP LOCKED) RETURNING id", params)]


def _tail_sources_pending() -> dict[str, int]:
    """尾部源(严格头部之外)当前待抽量。"""
    rows = db.query(
        "SELECT source, count(*) c FROM documents "
        "WHERE kg_extracted_at IS NULL AND permission<>'red' AND source <> ALL(%s) "
        "GROUP BY source", (list(STRICT_PRIORITY_ORDER),))
    excl = set(_exclude())
    return {r["source"]: r["c"] for r in rows if r["source"] not in excl and r["c"] > 0}


def _depth_alpha() -> float:
    return float(getattr(get_settings(), "qwen_drain_depth_alpha",
                         DEFAULT_TAIL_DEPTH_ALPHA))


def _split_by_quality(n: int, pending: dict[str, int],
                      alpha: float | None = None) -> dict[str, int]:
    """把 n 个名额按**信息质量 × 队列深度**切给尾部各源;某源待抽不足时,
    其剩余份额自动流给其他源(最大余额法 + 溢出再分配,不浪费产能)。

    深度项(alpha,见 pipeline_priority.effective_tail_weight)防止「纯质量权重对积压深度
    零感知」——否则占尾部 backlog 1.3% 的 edgar 与占 64.7% 的 finnhub 会拿到相同绝对份额。
    权重按 pool 里的**剩余**待抽量逐轮重算,溢出再分配时深度自然跟着收缩。"""
    a = _depth_alpha() if alpha is None else alpha
    quota: dict[str, int] = {}
    remaining = n
    pool = dict(pending)
    while remaining > 0 and pool:
        w = {s: effective_tail_weight(s, pool[s], a) for s in pool}
        total_w = sum(w.values()) or 1.0
        shares = {s: remaining * w[s] / total_w for s in pool}
        # 先按整数份额分,余数按小数大小补足(最大余额法)
        alloc = {s: min(int(v), pool[s]) for s, v in shares.items()}
        left = remaining - sum(alloc.values())
        for s, _ in sorted(shares.items(), key=lambda kv: kv[1] - int(kv[1]), reverse=True):
            if left <= 0:
                break
            if alloc[s] < pool[s]:
                alloc[s] += 1
                left -= 1
        progressed = False
        for s, k in alloc.items():
            if k > 0:
                quota[s] = quota.get(s, 0) + k
                pool[s] -= k
                remaining -= k
                progressed = True
        pool = {s: c for s, c in pool.items() if c > 0}
        if not progressed:                      # 无法再分配(全部取满)→ 收尾
            break
    return quota


def _claim(n: int) -> list[str]:
    """领取策略(2026-07-28 用户裁定):

    ① **严格头部 100% 抢占**:alphapai > gangtise > aifinmarket。只要靠前的源还有待抽文档,
       它就吃满整批 —— 后面的源与尾部一律等待(tier_order_sql 保证组内也按此序)。
    ② **尾部按「信息质量 × 队列深度」分配剩余产能**:头部取不满时,剩余名额按
       TAIL_QUALITY_WEIGHTS(= 实测 expert kept_rate:wechat 8.5 / edgar 6.0 / finnhub 5.9 /
       x 3.5 / rss 2.3 …)× pending^alpha 成比例切分;某源没货时份额自动流给其他源,
       GPU 不空转。alpha 由 qwen_drain_depth_alpha 控制(0=退回纯质量,见 2026-07-29 审计:
       纯质量下 edgar 占 backlog 1.3% 却与占 64.7% 的 finnhub 同额,深队列长期不收敛)。
    """
    head = _claim_sql(n, only=list(STRICT_PRIORITY_ORDER))
    left = n - len(head)
    if left <= 0:
        return head                              # 头部有货 → 100% 归头部
    quota = _split_by_quality(left, _tail_sources_pending())
    tail: list[str] = []
    for src, k in quota.items():
        tail += _claim_sql(k, only=[src])
    if len(head) + len(tail) < n:                # 配额未用尽(并发抢占等)→ 兜底补齐,不浪费产能
        tail += _claim_sql(n - len(head) - len(tail),
                           exclude_sources=list(STRICT_PRIORITY_ORDER))
    return head + tail


def _pending() -> int:
    """待抽计数(排除被暂停的源,反映 drain 实际可做的量)。"""
    excl = _exclude()
    if excl:
        return db.query("SELECT count(*) c FROM documents WHERE kg_extracted_at IS NULL "
                        "AND permission<>'red' AND source <> ALL(%s)", (excl,))[0]["c"]
    return db.query("SELECT count(*) c FROM documents WHERE kg_extracted_at IS NULL "
                    "AND permission<>'red'")[0]["c"]


def _one(doc_id: str, run_id: str, pin: tuple[str, ...]) -> None:
    from ..kg import expert, extract
    try:
        with llm.pinned(pin):                    # 必须在 worker 线程内钉扎(contextvar 不入池)
            extract.extract_from_document(doc_id, run_id=run_id)
            expert.process_document(doc_id, run_id=run_id)
    except Exception as e:  # noqa: BLE001 — 已盖戳,失败即跳过(毒文档语义)
        log.warning("qwen_drain %s: %s %s", doc_id, type(e).__name__, str(e)[:80])


def run_once() -> dict:
    """单批:领取 → KG+expert。空队列返回 {idle}。供 --once 与单测。"""
    s = get_settings()
    pin = (s.qwen_drain_model,)
    ids = _claim(s.qwen_drain_batch)
    if not ids:
        return {"idle": True, "pending": _pending()}
    run_id = llm.new_batch_run_id("kg")
    with ThreadPoolExecutor(max_workers=s.qwen_drain_workers) as ex:
        list(ex.map(lambda d: _one(d, run_id, pin), ids))
    return {"done": len(ids), "pending": _pending()}


def run_daemon() -> None:
    s = get_settings()
    pin = (s.qwen_drain_model,)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    log.info("qwen_drain up: pin=%s workers=%d pending=%d", pin[0], s.qwen_drain_workers, _pending())
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=s.qwen_drain_workers) as ex:
        while True:
            try:
                ids = _claim(s.qwen_drain_batch)
                if not ids:
                    time.sleep(s.qwen_drain_idle_seconds)
                    continue
                run_id = llm.new_batch_run_id("kg")
                list(ex.map(lambda d: _one(d, run_id, pin), ids))
                done += len(ids)
                el = time.time() - t0
                log.info("qwen_drain done=%d pending=%d rate=%.1f/min elapsed=%.1fm",
                         done, _pending(), done / el * 60, el / 60)
            except KeyboardInterrupt:
                log.info("qwen_drain interrupted — exiting cleanly")
                return
            except Exception as e:  # noqa: BLE001 — 常驻进程绝不因单轮异常退出
                log.warning("qwen_drain loop error: %s", str(e)[:160])
                time.sleep(5)
