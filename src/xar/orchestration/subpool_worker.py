"""常驻云端订阅并行池 worker(docker 服务 subpool)。

持续把 thesis 重建分发到 GLM-5.2 / Minimax-M3 / Kimi-K3 三订阅**并行**跑,吃满三份订阅计划的
token 额度(而非串行只用 GLM 一家)。优先重建 challenged(信号/争论挑战最重)+ stale(as_of 过期)
公司 —— 既是最有价值的产出(论点=产品),又能持续消耗额度。某 provider 触限即冷却(models/subpool
per-provider 5h 窗),其余继续;全 provider 冷却则休眠等窗口刷新后探针恢复。SIGTERM→干净退出。

与 qwen_drain(本地 GPU 抽取)、glm_worker(抓取+解析+轻活)三条常驻流并行 —— 本地算力与云端订阅
额度各自被打满、互不挟持。
"""
from __future__ import annotations

import signal
import time

from ..config import get_settings
from ..logging import get_logger
from ..models import subpool

log = get_logger("xar.subpool_worker")


def _pick_companies(limit: int) -> list[str]:
    """待重建 thesis 的公司:challenged(信号/争论挑战)优先,补 stale(thesis 过期/缺失),去重。"""
    out: list[str] = []
    try:
        from ..research import thesis_health
        out += list(thesis_health.challenged_companies_v2(limit=limit))
    except Exception as e:  # noqa: BLE001
        log.warning("challenged pick failed: %s", str(e)[:120])
    if len(out) < limit:
        stale_h = get_settings().subpool_thesis_stale_hours
        try:
            from ..storage import db
            rows = db.query(
                "SELECT c.id FROM companies c "
                "LEFT JOIN LATERAL (SELECT max(as_of) mx FROM company_thesis t "
                "                   WHERE t.company_id=c.id) th ON true "
                "WHERE th.mx IS NULL OR th.mx < now() - (%s || ' hours')::interval "
                "ORDER BY th.mx ASC NULLS FIRST LIMIT %s", (stale_h, limit * 4))
            for r in rows:
                if r["id"] not in out:
                    out.append(r["id"])
                if len(out) >= limit:
                    break
        except Exception as e:  # noqa: BLE001
            log.warning("stale pick failed: %s", str(e)[:120])
    return out[:limit]


def run_once() -> dict:
    from ..models import llm
    from ..research import thesis

    s = get_settings()
    if not s.subpool_enabled:
        return {"skipped": "subpool disabled"}
    pins = subpool.available_pins()
    if not pins:
        return {"idle": "all providers cooling", "quota": subpool.status()}
    cids = _pick_companies(s.subpool_batch)
    if not cids:
        return {"idle": "no theses to rebuild"}
    run_id = llm.new_batch_run_id("thesis")

    def _build(cid: str):
        # 返回 "built" 视为成功;rejected/no_data/None → 返 None(provider 健康信号,连续失败即冷却)。
        st = thesis.build(cid, force=True, run_id=run_id).get("status")
        return st if st in ("built", "skipped") else None

    res = subpool.run_parallel(cids, _build)
    built = sum(1 for _, r in res if r)
    return {"attempted": len(cids), "built": built,
            "providers": [p for p, _ in pins], "quota": subpool.status()}


def run_daemon() -> None:
    s = get_settings()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    log.info("subpool worker up: pins=%s batch=%d", s.subpool_pins, s.subpool_batch)
    while True:
        try:
            out = run_once()
            log.info("subpool cycle: %s",
                     {k: out.get(k) for k in ("attempted", "built", "idle", "skipped")})
            idle = ("idle" in out) or ("skipped" in out)
        except KeyboardInterrupt:
            log.info("subpool worker interrupted — exiting cleanly")
            return
        except Exception as e:  # noqa: BLE001 — 常驻进程绝不因单轮异常退出
            log.warning("subpool cycle failed: %s", str(e)[:160])
            idle = True
        try:
            time.sleep(s.subpool_idle_seconds if idle else 2)
        except KeyboardInterrupt:
            log.info("subpool worker interrupted during sleep — exiting cleanly")
            return
