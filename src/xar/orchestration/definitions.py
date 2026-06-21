"""Dagster software-defined assets for scheduled, incremental ingestion + KG
refresh. Optional: `pip install '.[orchestration]'` then `dagster dev -m
xar.orchestration.definitions`. Lineage: filings -> chunks -> KG -> tracking.

Not imported by the core package; load it directly with Dagster."""
from __future__ import annotations

from dagster import (AssetExecutionContext, Definitions, ScheduleDefinition,
                     asset, define_asset_job)

from xar.ingestion import ingest_basket
from xar.kg import extract as kg_extract
from xar.parsing import parse


@asset
def filings(context: AssetExecutionContext) -> dict:
    counts = ingest_basket()
    context.log.info("ingested: %s", counts)
    return counts


@asset(deps=[filings])
def chunks(context: AssetExecutionContext) -> int:
    n = parse.parse_pending()
    context.log.info("parsed %d chunks", n)
    return n


@asset(deps=[chunks])
def knowledge_graph(context: AssetExecutionContext) -> dict:
    totals = kg_extract.build_kg()
    context.log.info("kg: %s", totals)
    return totals


refresh_job = define_asset_job("refresh", selection="*")
# Daily incremental refresh (filings -> chunks -> KG -> downstream tracking summaries)
daily = ScheduleDefinition(job=refresh_job, cron_schedule="0 6 * * *")

defs = Definitions(assets=[filings, chunks, knowledge_graph], jobs=[refresh_job], schedules=[daily])
