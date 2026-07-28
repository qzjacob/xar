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
from ..pipeline_priority import DEPRIORITIZED_SOURCES, tier_order_sql
from ..storage import db

log = get_logger("xar.qwen_drain")


def _exclude() -> list[str]:
    return [s.strip() for s in (get_settings().qwen_drain_exclude_sources or "").split(",")
            if s.strip()]


def _claim_where(n: int, *, filler: bool) -> list[str]:
    """原子领取 n 篇(SKIP LOCKED + 当场盖戳 → 并发不双抽)。
    filler=False:只取**非末位**源(tier 0/1),按三档序;filler=True:只取末位源(x/finnhub 存量)。"""
    if n <= 0:
        return []
    excl = _exclude()
    where = ["kg_extracted_at IS NULL", "permission<>'red'"]
    params: list = []
    if excl:
        where.append("source <> ALL(%s)")
        params.append(excl)
    if DEPRIORITIZED_SOURCES:
        where.append("source = ANY(%s)" if filler else "source <> ALL(%s)")
        params.append(list(DEPRIORITIZED_SOURCES))
    elif filler:
        return []
    params.append(n)
    return [r["id"] for r in db.query(
        "UPDATE documents SET kg_extracted_at=now() WHERE id IN ("
        f"  SELECT id FROM documents WHERE {' AND '.join(where)}"
        f"  ORDER BY {tier_order_sql('source')} ASC, published_at DESC NULLS LAST"
        "  LIMIT %s FOR UPDATE SKIP LOCKED) RETURNING id", params)]


def _claim(n: int) -> list[str]:
    """一批的领取策略:**高价值源优先 + 末位源保留填充份额**。

    严格优先级在本系统里等于"末位源永不执行":tier-1 的 edgar 10 年历史回填持续灌入
    (实测 6h 灌 1390 / 抽 687,且才走到 168/1062 家),tier 0/1 永远不空 → x/finnhub 的 31.6 万
    存量拿不到任何 GPU。故按 `qwen_drain_filler_ratio` 给末位源留一小份(默认 25%):
      · 高价值源仍拿绝大多数产能(75%),优先级次序不变;
      · 末位存量以稳定小速率消化,不再被无限饿死;
      · 高价值源不足时,末位源**自动吸收全部剩余产能**(GPU 永不空转)。
    ratio=0 即退回严格优先级(末位仅在前两档空时才抽)。"""
    ratio = max(0.0, min(1.0, get_settings().qwen_drain_filler_ratio))
    n_filler = int(n * ratio) if ratio > 0 else 0
    main = _claim_where(n - n_filler, filler=False)
    # 末位取:保留份额 + 高价值源没取满时的剩余产能(两者合一 = n - len(main))
    return main + _claim_where(n - len(main), filler=True)


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
