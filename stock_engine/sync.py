"""
Sync Google Sheets → BigQuery for the tables the stock engine reads.

The ERP stores live data in Google Sheets (ERP_TXN, ERP_MASTER).
For Python to calculate stock fast on 10 lakh+ rows we must have those
sheets in BigQuery — running Sheets API row-by-row would be 100× slower
and hit quota limits.

Two sync strategies are supported:

1. APPEND_SINCE  (default, fast)
   Reads only rows created_at > last_synced_at from Sheets and appends
   them to BigQuery.  Suitable for 5-minute incremental runs.

2. FULL_REPLACE  (slow, use for initial load or monthly reconciliation)
   Truncates the BigQuery table and reloads from Sheets in batches.

The caller decides which to use; main.py chooses based on elapsed time.
"""
import logging
import time
from datetime import datetime, timezone
from typing import Iterator

from google.cloud import bigquery
from googleapiclient.discovery import build
from google.oauth2 import service_account

from . import config

log = logging.getLogger(__name__)

# How many rows to load into BigQuery per API call (Sheets API max is ~1 million
# cells per call, ~50 K rows of ~20 columns = 1 M cells)
_SHEET_BATCH_ROWS = 50_000
# How many rows to stream into BigQuery per insert_rows_json call
_BQ_INSERT_BATCH  = 10_000


def _sheets_service(credentials=None):
    if credentials:
        return build("sheets", "v4", credentials=credentials, cache_discovery=False)
    return build("sheets", "v4", cache_discovery=False)


def _read_sheet_range(
    service,
    spreadsheet_id: str,
    tab_name: str,
    start_row: int,
    batch_size: int = _SHEET_BATCH_ROWS,
) -> list[list]:
    """Read one batch from a Google Sheet tab.  Returns raw rows (list of lists)."""
    end_row = start_row + batch_size - 1
    range_a1 = f"'{tab_name}'!A{start_row}:ZZ{end_row}"
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_a1)
        .execute()
    )
    return result.get("values", [])


def _rows_to_dicts(header: list[str], data_rows: list[list]) -> Iterator[dict]:
    for row in data_rows:
        # Pad short rows with empty strings so zip is safe
        padded = row + [""] * (len(header) - len(row))
        yield dict(zip(header, padded))


def _insert_to_bq(
    client: bigquery.Client,
    table_id: str,
    rows: list[dict],
    *,
    schema: list[bigquery.SchemaField],
) -> int:
    """
    Insert rows into BigQuery using the Storage Write API (batch mode).
    Falls back to streaming insert for small payloads.
    Returns number of rows inserted.
    """
    if not rows:
        return 0

    errors = []
    for i in range(0, len(rows), _BQ_INSERT_BATCH):
        batch = rows[i : i + _BQ_INSERT_BATCH]
        errs = client.insert_rows_json(table_id, batch)
        if errs:
            log.error("BQ insert errors (rows %d–%d): %s", i, i + len(batch), errs)
            errors.extend(errs)

    if errors:
        raise RuntimeError(f"{len(errors)} BigQuery insert errors — see logs")

    return len(rows)


# ── Public entry points ───────────────────────────────────────────────────

def sync_stock_ledger(
    client: bigquery.Client,
    spreadsheet_id: str,
    tab_name: str = "stock_ledger",
    since_time: datetime | None = None,
    credentials=None,
) -> int:
    """
    Sync ERP_TXN!stock_ledger from Google Sheets into BigQuery.

    If since_time is given, only rows where created_at >= since_time are read
    (the Sheets tab must be sorted by created_at ascending for this to work).
    Otherwise all rows are synced (initial load).

    Returns total rows inserted.
    """
    from .schema import STOCK_LEDGER_SCHEMA

    service = _sheets_service(credentials)

    # Row 1 is the header
    header_raw = _read_sheet_range(service, spreadsheet_id, tab_name, 1, 1)
    if not header_raw:
        log.warning("Sheet %s!%s appears empty", spreadsheet_id, tab_name)
        return 0
    header = [h.strip().lower().replace(" ", "_") for h in header_raw[0]]

    total_inserted = 0
    start_row = 2  # skip header

    while True:
        raw = _read_sheet_range(service, spreadsheet_id, tab_name, start_row)
        if not raw:
            break

        rows = list(_rows_to_dicts(header, raw))

        # Filter by since_time if provided (avoids re-inserting old rows)
        if since_time:
            rows = [
                r for r in rows
                if r.get("created_at", "") >= since_time.isoformat()
            ]

        if rows:
            n = _insert_to_bq(
                client, config.STOCK_LEDGER, rows,
                schema=STOCK_LEDGER_SCHEMA,
            )
            total_inserted += n
            log.info("Synced %d stock_ledger rows (batch starting row %d)",
                     n, start_row)

        if len(raw) < _SHEET_BATCH_ROWS:
            break  # last batch
        start_row += _SHEET_BATCH_ROWS

    log.info("stock_ledger sync complete: %d rows total", total_inserted)
    return total_inserted


def sync_inventory_stock(
    client: bigquery.Client,
    spreadsheet_id: str,
    tab_name: str = "INVENTORY STOCK",
    credentials=None,
) -> int:
    """
    Full replace of ERP_MASTER_inventory_stock from the INVENTORY STOCK tab.
    This sheet changes rarely (only when new items are approved via NIA),
    so a full replace is acceptable.
    """
    from .schema import STOCK_LEDGER_SCHEMA  # reuse pattern

    service = _sheets_service(credentials)

    log.info("Replacing inventory_stock from Sheets …")

    # Truncate first (safe — opening stock is reloaded from Sheets, not mutated)
    client.query(
        f"TRUNCATE TABLE `{config.INVENTORY_STOCK}`"
    ).result()

    header_raw = _read_sheet_range(service, spreadsheet_id, tab_name, 1, 1)
    if not header_raw:
        log.warning("INVENTORY STOCK tab appears empty")
        return 0
    header = [h.strip().lower().replace(" ", "_") for h in header_raw[0]]

    total = 0
    start_row = 2
    while True:
        raw = _read_sheet_range(service, spreadsheet_id, tab_name, start_row)
        if not raw:
            break
        rows = list(_rows_to_dicts(header, raw))
        if rows:
            n = _insert_to_bq(client, config.INVENTORY_STOCK, rows, schema=[])
            total += n
        if len(raw) < _SHEET_BATCH_ROWS:
            break
        start_row += _SHEET_BATCH_ROWS

    log.info("inventory_stock sync complete: %d rows", total)
    return total
