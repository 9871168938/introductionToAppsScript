"""
One-time setup: create the BigQuery dataset and tables the stock engine uses.

Safe to re-run — uses CREATE IF NOT EXISTS semantics and only adds missing
columns (never drops existing ones).

    python -m stock_engine.setup_tables
"""
import logging
import sys

from google.cloud import bigquery
from google.api_core.exceptions import Conflict

from . import config
from .schema import STOCK_LEDGER_SCHEMA, STOCK_LEDGER_TABLE_OPTIONS, STOCK_CALC_MV_DDL

log = logging.getLogger(__name__)


def _ensure_dataset(client: bigquery.Client) -> None:
    ds_ref = bigquery.Dataset(f"{config.PROJECT_ID}.{config.DATASET_ID}")
    ds_ref.location = config.BQ_LOCATION
    client.create_dataset(ds_ref, exists_ok=True)
    log.info("Dataset %s.%s ready", config.PROJECT_ID, config.DATASET_ID)


def _ensure_table(client: bigquery.Client, table_id: str, options: dict) -> None:
    tbl = bigquery.Table(table_id)
    for k, v in options.items():
        setattr(tbl, k, v)
    try:
        client.create_table(tbl)
        log.info("Created table %s", table_id)
    except Conflict:
        existing = client.get_table(table_id)
        existing_names = {f.name for f in existing.schema}
        new_fields = [f for f in options["schema"] if f.name not in existing_names]
        if new_fields:
            existing.schema = list(existing.schema) + new_fields
            client.update_table(existing, ["schema"])
            log.info("Added %d new column(s) to %s", len(new_fields), table_id)
        else:
            log.info("Table %s already up-to-date", table_id)


def _ensure_materialized_view(client: bigquery.Client) -> None:
    """
    Create ERP_StockCalc as a BigQuery materialized view.
    Auto-refreshes every 5 minutes — no trigger or scheduler needed.
    """
    ddl = STOCK_CALC_MV_DDL.format(
        view_id=config.STOCK_CALC_VIEW,
        ledger_id=config.STOCK_LEDGER,
    )
    client.query(ddl).result()
    log.info("Materialized view %s ready (auto-refresh every 5 min)", config.STOCK_CALC_VIEW)


def setup_all(client: bigquery.Client | None = None) -> None:
    if client is None:
        client = bigquery.Client(
            project=config.PROJECT_ID,
            location=config.BQ_LOCATION,
        )
    log.info("=== Evergreen Purchase ERP — BigQuery setup ===")
    _ensure_dataset(client)
    _ensure_table(client, config.STOCK_LEDGER, STOCK_LEDGER_TABLE_OPTIONS)
    _ensure_materialized_view(client)
    log.info("=== Setup complete ===")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        stream=sys.stdout)
    setup_all()
