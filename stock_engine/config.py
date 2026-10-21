"""
Configuration for the BigQuery stock engine.

All table IDs come from the ERP_DATABASE reference document v2 (section 2 + 5).
Override any value via environment variable.
"""
import os

# ── BigQuery project / dataset ─────────────────────────────────────────────
PROJECT_ID = os.getenv("BQ_PROJECT_ID", "productionreport-467412")
DATASET_ID = os.getenv("BQ_DATASET_ID", "PurchaseERP")


def table(name: str) -> str:
    """Return a fully-qualified BigQuery table reference."""
    return f"{PROJECT_ID}.{DATASET_ID}.{name}"


# ── ERP source tables (synced from Google Sheets by Apps Script) ───────────
# ERP_TXN sheet tabs replicated as BQ tables for performance
STOCK_LEDGER          = table("ERP_TXN_stock_ledger")        # stock movements
STOCK_LOT             = table("ERP_TXN_stock_lot")           # FIFO lots
DOC_HEADER            = table("ERP_TXN_doc_header")
DOC_LINE              = table("ERP_TXN_doc_line")
MR_HEADER             = table("ERP_TXN_mr_header")
MI_LINE               = table("ERP_TXN_mi_line")

# ERP_MASTER sheet tabs
INVENTORY_STOCK       = table("ERP_MASTER_inventory_stock")  # opening stock
BRANCH                = table("ERP_MASTER_branch")
ITEM_SETTING          = table("ERP_MASTER_item_setting")

# ── BigQuery stock output (append-only log + latest view) ─────────────────
STOCK_CALC_LOG        = table("ERP_StockCalc_Log")           # append-only
STOCK_CALC_VIEW       = table("ERP_StockCalc")               # latest per BRANCH+ITEM_CODE

# ── Legacy BigQuery tables (old AppSheet / PURCHASE ERP data) ─────────────
LEGACY_MATERIAL_ISSUE = table("MaterialIssue")
LEGACY_STOCK_TRANSFER = table("StockTransferChild")
LEGACY_ADJUSTMENT     = table("StockAdjustment")

# ── Performance tuning ────────────────────────────────────────────────────
# How many minutes back the incremental query looks when no last_calc_time found
FALLBACK_LOOKBACK_MIN: int  = int(os.getenv("FALLBACK_LOOKBACK_MIN",  "60"))
# BigQuery query timeout (seconds)
QUERY_TIMEOUT_SECS: int     = int(os.getenv("BQ_QUERY_TIMEOUT_SECS", "300"))
# Qty / value change threshold below which we skip writing a new log row
QTY_EPSILON: float          = float(os.getenv("QTY_EPSILON",          "0.0001"))
VALUE_EPSILON: float        = float(os.getenv("VALUE_EPSILON",         "0.01"))
# BigQuery location
BQ_LOCATION: str            = os.getenv("BQ_LOCATION",                "asia-south1")
