"""
Entry point for the Evergreen Purchase ERP — BigQuery Stock Engine.

Can be run three ways:

1. Direct execution (local / Cloud Run job):
       python -m stock_engine.main

2. Cloud Functions HTTP trigger (set as function_target in cloudbuild.yaml):
       export FUNCTION_TARGET=run_stock_engine

3. Apps Script UrlFetchApp trigger (calls the Cloud Run job URL).

Environment variables
---------------------
BQ_PROJECT_ID          BigQuery project (default: productionreport-467412)
BQ_DATASET_ID          BigQuery dataset (default: PurchaseERP)
BQ_LOCATION            BigQuery region   (default: asia-south1)
SHEETS_STOCK_LEDGER_ID Google Sheets ID for ERP_TXN (stock_ledger tab)
SHEETS_MASTER_ID       Google Sheets ID for ERP_MASTER (INVENTORY STOCK tab)
FORCE_FULL_CALC        Set to "true" to skip incremental and do a full recalc
SYNC_SHEETS            Set to "true" to sync Sheets → BQ before calculating
QUERY_TIMEOUT_SECS     BigQuery query timeout in seconds (default: 300)

Performance notes
-----------------
- At 10 lakh rows, the incremental path takes ~2–5 seconds in BigQuery
  (partition pruning + cluster filtering limits the scan to today's slice).
- The full-recalc path scans the entire stock_ledger; with partitioning
  and 200 GB max-bytes-billed the cost stays predictable.
- The sync step (Sheets → BQ) is the bottleneck at large volumes;
  run it separately on a longer interval and run the calc every 5 min.
"""
import logging
import os
import sys
import time
from datetime import timezone

from google.cloud import bigquery

from . import config
from .calculator import StockCalculator
from .setup_tables import setup_all

log = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )


def _build_client() -> bigquery.Client:
    return bigquery.Client(
        project=config.PROJECT_ID,
        location=config.BQ_LOCATION,
    )


# ── Cloud Function HTTP handler ───────────────────────────────────────────

def run_stock_engine(request=None):
    """
    Cloud Functions HTTP trigger entry point.
    Also callable directly (request can be None for non-HTTP invocations).
    """
    _configure_logging()
    start = time.monotonic()

    force_full = os.getenv("FORCE_FULL_CALC", "false").lower() == "true"
    do_sync    = os.getenv("SYNC_SHEETS",     "false").lower() == "true"

    client = _build_client()

    # Ensure tables / view exist (idempotent)
    setup_all(client)

    # Optional: sync Google Sheets → BigQuery before calculating
    if do_sync:
        _sync_sheets(client)

    # Run stock calculation
    calc   = StockCalculator(client)
    rows   = calc.run(force_full=force_full)
    elapsed = time.monotonic() - start

    log.info(
        "Stock engine finished: %d rows updated in %.1fs", rows, elapsed
    )

    # Return JSON for Cloud Functions / Cloud Run
    return {
        "status":   "ok",
        "rows":     rows,
        "elapsed_s": round(elapsed, 2),
        "force_full": force_full,
    }


def _sync_sheets(client: bigquery.Client) -> None:
    """Sync stock_ledger and inventory_stock from Google Sheets."""
    from .sync import sync_stock_ledger, sync_inventory_stock
    from .calculator import StockCalculator

    # Determine how far back to sync (same window as calc)
    calc    = StockCalculator(client)
    last_ts = calc._last_calc_time()

    ledger_sheet_id = os.getenv("SHEETS_STOCK_LEDGER_ID")
    master_sheet_id = os.getenv("SHEETS_MASTER_ID")

    if ledger_sheet_id:
        sync_stock_ledger(client, ledger_sheet_id, since_time=last_ts)
    else:
        log.warning("SHEETS_STOCK_LEDGER_ID not set — skipping stock_ledger sync")

    if master_sheet_id:
        sync_inventory_stock(client, master_sheet_id)
    else:
        log.warning("SHEETS_MASTER_ID not set — skipping inventory_stock sync")


# ── CLI entry point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    result = run_stock_engine()
    print(result)
