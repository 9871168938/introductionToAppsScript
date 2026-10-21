"""
BigQuery table schemas and DDL for the Evergreen Purchase ERP stock engine.

ERP_TXN_stock_ledger
  - Partitioned by DATE(created_at)    → partition pruning on time-range queries
  - Clustered by (branch_id, item_code) → fast per-item scans within a partition

ERP_StockCalc (MATERIALIZED VIEW)
  - Auto-refreshes every 5 minutes via BigQuery's built-in scheduler
  - No Python trigger, no cron job — BigQuery handles it
  - Dashboard/API queries always hit the pre-computed result
"""
from google.cloud import bigquery

# ── ERP_StockCalc — Materialized View DDL ─────────────────────────────────
#
# BigQuery auto-refreshes this view every refresh_interval_minutes.
# allow_non_incremental_definition = true lets us use COALESCE and ROUND
# which are not supported in incremental MV mode.
#
# Stock formula: stock = SUM(qty_in) - SUM(qty_out)
# This works because the ledger schema separates inbound (qty_in) from
# outbound (qty_out) quantities for every movement_type.
STOCK_CALC_MV_DDL = """
CREATE MATERIALIZED VIEW IF NOT EXISTS `{view_id}`
OPTIONS (
  enable_refresh = true,
  refresh_interval_minutes = 5,
  allow_non_incremental_definition = true
)
AS
SELECT
  branch_id,
  item_code,
  ROUND(
    SUM(COALESCE(qty_in, 0) - COALESCE(qty_out, 0)),
    6
  )                          AS stock_qty,
  ROUND(SUM(COALESCE(value, 0)), 2) AS stock_value,
  MAX(created_at)            AS last_movement_at,
  COUNT(*)                   AS movement_count
FROM `{ledger_id}`
WHERE is_reversal IS NULL OR is_reversal = FALSE
GROUP BY branch_id, item_code
"""

# ── ERP_TXN_stock_ledger (synced from Google Sheets) ─────────────────────
# Partition + cluster for fast incremental reads
STOCK_LEDGER_SCHEMA = [
    bigquery.SchemaField("ledger_id",           "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("txn_date",            "DATE",      mode="REQUIRED"),
    bigquery.SchemaField("branch_id",           "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("item_code",           "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("doc_id",              "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("line_id",             "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("movement_type",       "STRING",    mode="REQUIRED",
                         description="OPENING/GRN_IN/ISSUE_OUT/RETURN_IN/RETURN_OUT/"
                                     "TRF_OUT/TRF_IN/ADJ_PLUS/ADJ_MINUS/"
                                     "CHALLAN_OUT/CHALLAN_IN"),
    bigquery.SchemaField("qty_in",              "FLOAT64",   mode="NULLABLE"),
    bigquery.SchemaField("qty_out",             "FLOAT64",   mode="NULLABLE"),
    bigquery.SchemaField("rate",                "FLOAT64",   mode="NULLABLE"),
    bigquery.SchemaField("value",               "FLOAT64",   mode="NULLABLE"),
    bigquery.SchemaField("is_reversal",         "BOOL",      mode="NULLABLE"),
    bigquery.SchemaField("reversed_ledger_id",  "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("lot_id",              "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("created_by",          "STRING",    mode="NULLABLE"),
    bigquery.SchemaField("created_at",          "TIMESTAMP", mode="REQUIRED"),
]

STOCK_LEDGER_TABLE_OPTIONS = dict(
    schema=STOCK_LEDGER_SCHEMA,
    time_partitioning=bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="created_at",
    ),
    clustering_fields=["branch_id", "item_code"],
    labels={"app": "purchase-erp", "layer": "txn-sync"},
)
