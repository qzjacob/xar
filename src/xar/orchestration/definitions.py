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

from dagster import (Definitions, RunRequest, StaticPartitionsDefinition,
                     asset, define_asset_job, schedule)

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
pull_job = define_asset_job("pull_shard_job", selection=[pull_shard], partitions_def=_shards)
extract_job = define_asset_job("extract_all_job", selection=[extract_all])
core_job = define_asset_job("core_daily_job", selection=[core_daily])


@schedule(job=pull_job, cron_schedule=f"0 {_HOUR} * * *")
def pull_schedule(context):
    """Fan out one PULL run per universe shard at the top of the configured hour."""
    ts = context.scheduled_execution_time.strftime("%Y%m%d")
    for i in range(_N):
        yield RunRequest(run_key=f"{ts}-pull-{i}", partition_key=f"shard-{i}")


@schedule(job=extract_job, cron_schedule=f"30 {_HOUR} * * *")
def extract_schedule(context):
    """Run the single global extraction 30 min after the pulls start (one run/day)."""
    ts = context.scheduled_execution_time.strftime("%Y%m%d")
    yield RunRequest(run_key=f"{ts}-extract")


defs = Definitions(assets=[pull_shard, extract_all, core_daily],
                   jobs=[pull_job, extract_job, core_job],
                   schedules=[pull_schedule, extract_schedule])
