"""Dagster sidecar for the daily auto-ingest system — a thin wrapper over
`xar.orchestration.daily.run_daily()` (dependency-free + unit-tested).

Optional: `pip install '.[orchestration]'` then
    dagster dev -m xar.orchestration.definitions

The night splits into two scheduled jobs so the expensive stage runs exactly once:
  - `pull_shard` (partitioned, shard-0..shard-{N-1}) fans the per-source PULL across the
    whole universe — sharded by company so the fetch parallelises safely. Fires at :00.
  - `extract_all` (un-partitioned, single run, single batch budget) does the GLOBAL
    parse → build_kg → expert → signals over the whole pending queue. Fires at :30, after
    the pulls have mostly landed. It MUST NOT be partitioned: those stages read the global
    queue, so running them per shard would N× the LLM spend and race on the same docs.

Not imported by the core package; loaded directly with Dagster (so the main app image
never needs the dagster dependency)."""
from __future__ import annotations

from dagster import (DefaultScheduleStatus, Definitions, RunRequest,
                     StaticPartitionsDefinition, asset, define_asset_job,
                     in_process_executor, schedule)

from xar.config import get_settings
from xar.orchestration.daily import run_daily

_N = max(1, get_settings().daily_universe_shards)
_HOUR = get_settings().daily_run_hour
_shards = StaticPartitionsDefinition([f"shard-{i}" for i in range(_N)])


@asset(partitions_def=_shards)
def pull_shard(context) -> dict:
    """One nightly shard of the universe-wide PULL (sources → documents). Sharded by
    company so fetching parallelises safely; the heavy extract runs once in extract_all."""
    shard = int(context.partition_key.split("-")[1])
    stats = run_daily(full_universe=True, shard=shard, n_shards=_N, stages=("pull",))
    context.log.info("pull shard %d: %s", shard, stats)
    return stats


@asset
def extract_all(context) -> dict:
    """The once-per-night GLOBAL extraction: parse → build_kg → expert → signals over the
    whole pending queue, with ONE batch budget. Un-partitioned by design — running these
    stages per shard would multiply the LLM spend and race on the same documents/chunks."""
    stats = run_daily(stages=("extract",))
    context.log.info("extract_all: %s", stats)
    return stats


@asset
def core_daily(context) -> dict:
    """Optional lighter job: pull+extract the curated core basket only (no sharding).
    Not scheduled — run on demand from the Dagster UI."""
    stats = run_daily(full_universe=False)
    context.log.info("core daily: %s", stats)
    return stats


# Job names must NOT collide with the asset/op names — Dagster also builds an implicit
# __ASSET_JOB over the same ops, and op/graph names must be unique within the repository.
#
# executor_def=in_process_executor(2026-07-30 内存实测后改):这三个 job **各自只有一个
# asset = 一个 op = 一个 step**,默认的 multiprocess executor 买不到任何并行度,只买到
# 「每个 run 多一个进程 + 多一份 214MB import」—— 而 dagster 的 start_method 默认是
# **spawn**(可选值只有 spawn/forkserver,没有 fork),子进程是全新解释器、零 copy-on-write
# 共享,所以那 214MB 是实打实再付一遍。
#
# 实测账(dagster 容器硬限 8G):固定开销约 580MB;multiprocess 下每 run 约 951MB
# (由 8 个 run 打满 8G 反解,与内核 OOM 记录的 733/953/1119MB 吻合),in_process 下约 730MB。
# 2026-07-30 夜间 9 个 run 里 4 个被 memcg OOM 杀,正是 8×951+580 越过 8192 所致。
#
# ⚠️ 代价要记住:multiprocess 恰恰是那 3 个被 OOM 的 shard 能被正确记成 FAILURE 的原因
# (内核杀的是 step 子进程,父进程活着写下终态)。in_process 下 OOM 会连带杀掉唯一进程 →
# run 卡在 STARTED,即当初造成 7 天队列死锁的那个形态。现在依赖 dagster.yaml 的
# run_monitoring.max_runtime_seconds 兜底回收(8h),风险有界但不为零 —— 那份配置不可删。
_EXECUTOR = in_process_executor

pull_job = define_asset_job("pull_shard_job", selection=[pull_shard], partitions_def=_shards,
                            executor_def=_EXECUTOR)
extract_job = define_asset_job("extract_all_job", selection=[extract_all],
                               executor_def=_EXECUTOR)
core_job = define_asset_job("core_daily_job", selection=[core_daily], executor_def=_EXECUTOR)


@schedule(job=pull_job, cron_schedule=f"0 {_HOUR} * * *",
          default_status=DefaultScheduleStatus.RUNNING)
def pull_schedule(context):
    """Fan out one PULL run per universe shard at the top of the configured hour."""
    ts = context.scheduled_execution_time.strftime("%Y%m%d")
    for i in range(_N):
        yield RunRequest(run_key=f"{ts}-pull-{i}", partition_key=f"shard-{i}")


@schedule(job=extract_job, cron_schedule=f"30 {_HOUR} * * *",
          default_status=DefaultScheduleStatus.RUNNING)
def extract_schedule(context):
    """Run the single global extraction 30 min after the pulls start (one run/day)."""
    ts = context.scheduled_execution_time.strftime("%Y%m%d")
    yield RunRequest(run_key=f"{ts}-extract")


defs = Definitions(assets=[pull_shard, extract_all, core_daily],
                   jobs=[pull_job, extract_job, core_job],
                   schedules=[pull_schedule, extract_schedule])
