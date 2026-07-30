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


def _pick_companies(limit: int) -> list[tuple[str, str | None]]:
    """待重建 thesis 的公司 → [(cid, because)]。优先级:

      ① challenged —— 信号/争论天平挑战最重(既有);
      ② **刚出过财报且论点比这次财报旧**(2026-07-29 新增)—— 此前财报完全不参与重建
         候选,一家公司刚出完季报、论点还停在上季度,系统对此毫无反应;
      ③ stale —— 论点过期/缺失兜底。

    `because` 会写进新版本的 `changed_because`,让「这版因何重建」在库里留下因果。"""
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()

    def _add(cid: str, because: str | None) -> None:
        if cid not in seen:
            seen.add(cid)
            out.append((cid, because))

    try:
        from ..research import thesis_health
        for cid in thesis_health.challenged_companies_v2(limit=limit):
            _add(cid, "信号/争论挑战")
    except Exception as e:  # noqa: BLE001
        log.warning("challenged pick failed: %s", str(e)[:120])
    if len(out) < limit:
        try:
            from ..research import quarterly_feedback
            for cid, because in quarterly_feedback.recent_print_companies(limit=limit):
                _add(cid, because)
        except Exception as e:  # noqa: BLE001
            log.warning("recent-print pick failed: %s", str(e)[:120])
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
                _add(r["id"], None)
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
    picks = _pick_companies(s.subpool_batch)
    if not picks:
        return {"idle": "no theses to rebuild"}
    cids = [c for c, _ in picks]
    because_by = dict(picks)
    run_id = llm.new_batch_run_id("thesis")

    def _build(cid: str):
        """返回值 = **provider 健康信号**(None 表示这家供应商有问题,连续 N 次即冷却)。

        2026-07-29 修正误诊闭环:`rejected`(模型答了但违反纪律)与 `no_data`(证据不足)
        都说明 **provider 是健康的** —— 它按时给出了可解析的结构化输出,只是内容不合格。
        把它们当成 provider 故障会冷却整家供应商,于是「论点纪律严」被翻译成「三家订阅全挂」,
        这正是 thesis 停摆期间看到的假象。只有 `llm_failed`(没吐出可用 JSON / 调用异常)
        与未知异常才是真的 provider 故障。"""
        st = thesis.build(cid, force=True, run_id=run_id,
                          because=because_by.get(cid)).get("status")
        return st if st in ("built", "skipped", "rejected", "no_data") else None

    res = subpool.run_parallel(cids, _build)
    stats: dict[str, int] = {}
    for _, r in res:
        stats[r or "llm_failed"] = stats.get(r or "llm_failed", 0) + 1
    return {"attempted": len(cids), "built": stats.get("built", 0), "statuses": stats,
            "providers": [p for p, _ in pins], "quota": subpool.status()}


def _beat(out: dict, *, idle: bool) -> None:
    """持久化心跳(2026-07-29 监控加入)。此前每轮结果只进 stdout,而 sub_quota 只在**状态
    变化**时才写(实测 3 家 provider 只有 1 家有行)—— 于是「健康且安静」与「从未跑过」
    在库里长得一模一样。空转也写,idle≠dead。"""
    from datetime import datetime, timezone

    from ..storage.kvstate import save_state
    try:
        save_state("subpool_beat", {
            "at": datetime.now(timezone.utc).isoformat(), "idle": idle,
            "attempted": out.get("attempted"), "built": out.get("built"),
            "providers": out.get("providers")})
    except Exception as e:  # noqa: BLE001 — 心跳写失败绝不能影响重建本身
        log.warning("subpool beat failed: %s", str(e)[:120])


def run_daemon() -> None:
    from datetime import datetime, timezone

    from ..monitoring import control

    s = get_settings()
    started_at = datetime.now(timezone.utc)      # 软重启用:只响应晚于本进程启动的请求
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    log.info("subpool worker up: pins=%s batch=%d", s.subpool_pins, s.subpool_batch)
    while True:
        control.exit_if_requested("subpool", started_at=started_at)
        try:
            out = run_once()
            log.info("subpool cycle: %s",
                     {k: out.get(k) for k in ("attempted", "built", "idle", "skipped")})
            idle = ("idle" in out) or ("skipped" in out)
            _beat(out, idle=idle)
        except KeyboardInterrupt:
            log.info("subpool worker interrupted — exiting cleanly")
            return
        except Exception as e:  # noqa: BLE001 — 常驻进程绝不因单轮异常退出
            log.warning("subpool cycle failed: %s", str(e)[:160])
            _beat({}, idle=True)
            idle = True
        try:
            time.sleep(s.subpool_idle_seconds if idle else 2)
        except KeyboardInterrupt:
            log.info("subpool worker interrupted during sleep — exiting cleanly")
            return
