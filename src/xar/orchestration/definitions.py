"""Dagster sidecar for the daily auto-ingest system — a thin wrapper over
`xar.orchestration.daily.run_daily()` (dependency-free + unit-tested).

Optional: `pip install '.[orchestration]'` then
    dagster dev -m xar.orchestration.definitions

The whole universe is covered every night, split into N static partitions
(shard-0 .. shard-{N-1}) so each nightly run is bounded and independently
retryable. The schedule fans out one run per shard at the configured hour.

Not imported by the core package; loaded directly with Dagster (so the main app
image never needs the dagster dependency)."""
from __future__ import annotations

from dagster import (AssetExecutionContext, Definitions, RunRequest,
                     StaticPartitionsDefinition, asset, define_asset_job, schedule)

from xar.config import get_settings
from xar.orchestration.daily import run_daily

_N = max(1, get_settings().daily_universe_shards)
_shards = StaticPartitionsDefinition([f"shard-{i}" for i in range(_N)])


@asset(partitions_def=_shards)
def universe_daily(context: AssetExecutionContext) -> dict:
    """One nightly shard of the full universe: pull all sources → docs → parse →
    semantic extract → expert → signals (all incremental)."""
    shard = int(context.partition_key.split("-")[1])
    stats = run_daily(full_universe=True, shard=shard, n_shards=_N)
    context.log.info("daily shard %d: %s", shard, stats)
    return stats


@asset
def core_daily(context: AssetExecutionContext) -> dict:
    """Optional lighter job: the core curated basket only (no sharding). Not scheduled."""
    stats = run_daily(full_universe=False)
    context.log.info("core daily: %s", stats)
    return stats


universe_job = define_asset_job("universe_daily", selection=[universe_daily],
                                partitions_def=_shards)
core_job = define_asset_job("core_daily", selection=[core_daily])


@schedule(job=universe_job, cron_schedule=f"0 {get_settings().daily_run_hour} * * *")
def daily_schedule(context):
    """Fan out one run per universe shard each night (run-keyed per day so a given
    night's shards are launched exactly once)."""
    ts = context.scheduled_execution_time.strftime("%Y%m%d")
    for i in range(_N):
        yield RunRequest(run_key=f"{ts}-shard-{i}", partition_key=f"shard-{i}")


defs = Definitions(assets=[universe_daily, core_daily],
                   jobs=[universe_job, core_job],
                   schedules=[daily_schedule])
