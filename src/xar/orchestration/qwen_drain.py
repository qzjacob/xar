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
from ..pipeline_priority import priority_order_sql
from ..storage import db

log = get_logger("xar.qwen_drain")


def _claim(n: int) -> list[str]:
    """原子领取 n 篇待抽文档并当场盖 kg_extracted_at(SKIP LOCKED → 并发不双抽)。"""
    return [r["id"] for r in db.query(
        "UPDATE documents SET kg_extracted_at=now() WHERE id IN ("
        "  SELECT id FROM documents WHERE kg_extracted_at IS NULL AND permission<>'red'"
        f"  ORDER BY {priority_order_sql('source')} DESC, published_at DESC NULLS LAST"
        "  LIMIT %s FOR UPDATE SKIP LOCKED) RETURNING id", (n,))]


def _pending() -> int:
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
