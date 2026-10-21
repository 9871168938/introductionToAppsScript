"""
Configuration for the Evergreen Purchase ERP — BigQuery Stock API.

BigQuery is the primary database.  All tables are written by the Python API;
Google Sheets is not involved in data storage.
"""
import os

# ── BigQuery project / dataset ─────────────────────────────────────────────
PROJECT_ID = os.getenv("BQ_PROJECT_ID", "productionreport-467412")
DATASET_ID = os.getenv("BQ_DATASET_ID", "PurchaseERP")


def table(name: str) -> str:
    return f"{PROJECT_ID}.{DATASET_ID}.{name}"


# ── Primary transaction table ─────────────────────────────────────────────
# Written directly by the API; partitioned on created_at, clustered on
# (branch_id, item_code) for fast partition-pruned queries.
STOCK_LEDGER    = table("ERP_TXN_stock_ledger")

# ── Live stock (Materialized View, auto-refreshes every 5 min) ───────────
# No trigger or scheduler needed — BigQuery refreshes it internally.
STOCK_CALC_VIEW = table("ERP_StockCalc")

# ── Supporting master tables ──────────────────────────────────────────────
INVENTORY_STOCK = table("ERP_MASTER_inventory_stock")
BRANCH          = table("ERP_MASTER_branch")
ITEM_SETTING    = table("ERP_MASTER_item_setting")

# ── Tuning ────────────────────────────────────────────────────────────────
BQ_LOCATION: str        = os.getenv("BQ_LOCATION", "asia-south1")
QUERY_TIMEOUT_SECS: int = int(os.getenv("BQ_QUERY_TIMEOUT_SECS", "300"))
