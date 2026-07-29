"""解除 dagster 运行队列死锁 —— 一次性运维脚本(2026-07-29 审计产物)。

背景见同目录 dagster.yaml 顶部注释。简言之:run_monitoring 曾默认关闭,容器重建把
run worker 打死后 run 永远停在 STARTED,10 个僵尸吃满 max_concurrent_runs=10,
2026-07-22 起 68 个 run 全卡 QUEUED、夜间 ingest 连续 7 天零执行。

用法(在 dagster 容器内跑;deploy/dagster 已由 compose 挂到 /opt/xar-ops):
    docker exec main-dagster-1 python3 /opt/xar-ops/unstick_run_queue.py            # dry-run
    docker exec main-dagster-1 python3 /opt/xar-ops/unstick_run_queue.py --apply

⚠️ 顺序要求:**必须先作废积压 QUEUED,再让僵尸槽位释放**。否则槽位一空,68 个陈旧 run
   会立刻雪崩式启动(其中 60 个是全宇宙 pull 分片)。本脚本已按此顺序执行。

⚠️ 装上 dagster.yaml(run_monitoring.enabled=true + max_runtime_seconds)之后,僵尸
   STARTED 会被 MonitoringDaemon 自动回收,本脚本的第 2 步就只是兜底/加速。但第 1 步
   (作废积压队列)仍必须在重启 dagster **之前**手工做掉。

默认 dry-run,只打印不改状态。改状态前请先备份:
    docker exec main-dagster-1 cp /dagster/history/runs.db /dagster/_backup/runs.db.bak
"""
from __future__ import annotations

import sys

from dagster import DagsterInstance
from dagster._core.storage.dagster_run import DagsterRunStatus, RunsFilter

APPLY = "--apply" in sys.argv
TAG = "APPLY" if APPLY else "DRY-RUN"

inst = DagsterInstance.get()


def _runs(status: DagsterRunStatus):
    return inst.get_runs(RunsFilter(statuses=[status]))


queued = _runs(DagsterRunStatus.QUEUED)
started = _runs(DagsterRunStatus.STARTED)
print(f"[{TAG}] QUEUED={len(queued)}  STARTED(疑僵尸)={len(started)}")

# ── 1) 作废积压队列(必须先做)────────────────────────────────────────────────────
ok = bad = 0
for r in queued:
    if not APPLY:
        continue
    try:
        inst.report_run_canceling(
            r, message="运维清理:队列死锁期间积压、从未执行的 run,直接作废")
        inst.report_run_canceled(inst.get_run_by_id(r.run_id))
        ok += 1
    except Exception as e:  # noqa: BLE001 — 单条失败不应中断整批清理
        bad += 1
        print(f"  CANCEL-FAIL {r.run_id[:8]} {type(e).__name__}: {str(e)[:120]}")
print(f"[{TAG}] queued canceled={ok} failed={bad}")

# ── 2) 回收僵尸 STARTED(释放并发槽)──────────────────────────────────────────────
ok = bad = 0
for r in started:
    part = r.tags.get("dagster/partition")
    print(f"  zombie {r.run_id[:8]} {r.job_name} partition={part}")
    if not APPLY:
        continue
    try:
        inst.report_run_failed(
            r, "运维清理:run worker 进程早已不存在(容器重建时被杀),回收并发槽")
        ok += 1
    except Exception as e:  # noqa: BLE001
        bad += 1
        print(f"  FAIL-FAIL {r.run_id[:8]} {type(e).__name__}: {str(e)[:120]}")
print(f"[{TAG}] zombies marked failed={ok} errors={bad}")

print("[final]", {s.value: len(_runs(s)) for s in (
    DagsterRunStatus.QUEUED, DagsterRunStatus.STARTED, DagsterRunStatus.CANCELED,
    DagsterRunStatus.FAILURE, DagsterRunStatus.SUCCESS)})
if not APPLY:
    print("\n(dry-run — 加 --apply 才会真正改状态)")
