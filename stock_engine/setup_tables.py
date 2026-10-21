"""
One-time setup: create / update BigQuery tables with the right schema,
partitioning and clustering.

Run once before the first stock engine run:
    python -m stock_engine.setup_tables

Safe to re-run: it uses CREATE IF NOT EXISTS semantics and only adds
missing columns (never drops existing ones).
"""
import logging
import sys

from google.cloud import bigquery
from google.api_core.exceptions import NotFound, Conflict

from . import config
from .schema import (
    STOCK_CALC_LOG_SCHEMA,
    STOCK_CALC_LOG_TABLE_OPTIONS,
    STOCK_LEDGER_SCHEMA,
    STOCK_LEDGER_TABLE_OPTIONS,
    STOCK_CALC_VIEW_DDL,
)

log = logging.getLogger(__name__)


def _ensure_dataset(client: bigquery.Client) -> None:
    ds_ref = bigquery.Dataset(f"{config.PROJECT_ID}.{config.DATASET_ID}")
    ds_ref.location = config.BQ_LOCATION
    try:
        client.create_dataset(ds_ref, exists_ok=True)
        log.info("Dataset %s.%s ready", config.PROJECT_ID, config.DATASET_ID)
    except Conflict:
        pass


def _ensure_table(
    client: bigquery.Client,
    table_id: str,
    options: dict,
) -> None:
    """Create the table if it does not exist, or add any new columns."""
    tbl = bigquery.Table(table_id)
    for k, v in options.items():
        setattr(tbl, k, v)

    try:
        client.create_table(tbl)
        log.info("Created table %s (partitioned+clustered)", table_id)
    except Conflict:
        # Table exists — patch any new schema fields
        existing = client.get_table(table_id)
        existing_names = {f.name for f in existing.schema}
        new_fields = [f for f in options["schema"] if f.name not in existing_names]
        if new_fields:
            existing.schema = list(existing.schema) + new_fields
            client.update_table(existing, ["schema"])
            log.info("Added %d new column(s) to %s", len(new_fields), table_id)
        else:
            log.info("Table %s already up-to-date", table_id)


def _ensure_view(client: bigquery.Client) -> None:
    ddl = STOCK_CALC_VIEW_DDL.format(
        view_id=config.STOCK_CALC_VIEW,
        log_id=config.STOCK_CALC_LOG,
    )
    # DDL statement; no job config needed
    job = client.query(ddl)
    job.result()
    log.info("View %s created / replaced", config.STOCK_CALC_VIEW)


def setup_all(client: bigquery.Client | None = None) -> None:
    if client is None:
        client = bigquery.Client(
            project=config.PROJECT_ID,
            location=config.BQ_LOCATION,
        )

    log.info("=== Setting up BigQuery tables for Evergreen Purchase ERP ===")
    _ensure_dataset(client)

    # stock_ledger (source — may already exist from ERP sync)
    _ensure_table(client, config.STOCK_LEDGER, STOCK_LEDGER_TABLE_OPTIONS)

    # ERP_StockCalc_Log (output)
    _ensure_table(client, config.STOCK_CALC_LOG, STOCK_CALC_LOG_TABLE_OPTIONS)

    # ERP_StockCalc view (latest row per BRANCH+ITEM_CODE)
    _ensure_view(client)

    log.info("=== Setup complete ===")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    setup_all()
