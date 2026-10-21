"""
BigQuery table schemas for the stock engine.

ERP_StockCalc_Log:
  - Partitioned by DATE(calc_time)  → BigQuery only scans today's partition for
    dashboard queries and the latest-row view, not the entire 10 lakh+ history.
  - Clustered by (branch_id, item_code)  → within a partition, BigQuery skips
    blocks that don't match the branch+item filter.

Together partitioning + clustering cut the typical query cost by 100–1000×
compared to a flat table as data grows past 10 lakh rows.
"""
from google.cloud import bigquery

# ── ERP_StockCalc_Log ──────────────────────────────────────────────────────
STOCK_CALC_LOG_SCHEMA = [
    bigquery.SchemaField("log_id",          "STRING",    mode="REQUIRED",
                         description="UUID, one row per changed item per run"),
    bigquery.SchemaField("branch_id",       "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("item_code",       "STRING",    mode="REQUIRED"),
    bigquery.SchemaField("calc_time",       "TIMESTAMP", mode="REQUIRED",
                         description="When this row was written (partition key)"),
    bigquery.SchemaField("stock_qty",       "FLOAT64",   mode="REQUIRED",
                         description="Net stock in INVENTORY STOCK unit"),
    bigquery.SchemaField("stock_value",     "FLOAT64",   mode="NULLABLE",
                         description="Value at FIFO / last purchase rate"),
    bigquery.SchemaField("rate",            "FLOAT64",   mode="NULLABLE",
                         description="Effective unit rate"),
    # Component breakdown for audit / exceptions report
    bigquery.SchemaField("opening_qty",     "FLOAT64",   mode="NULLABLE"),
    bigquery.SchemaField("grn_in_qty",      "FLOAT64",   mode="NULLABLE"),
    bigquery.SchemaField("issue_out_qty",   "FLOAT64",   mode="NULLABLE"),
    bigquery.SchemaField("return_in_qty",   "FLOAT64",   mode="NULLABLE"),
    bigquery.SchemaField("trf_in_qty",      "FLOAT64",   mode="NULLABLE"),
    bigquery.SchemaField("trf_out_qty",     "FLOAT64",   mode="NULLABLE"),
    bigquery.SchemaField("adj_qty",         "FLOAT64",   mode="NULLABLE"),
    bigquery.SchemaField("movement_count",  "INT64",     mode="NULLABLE"),
    bigquery.SchemaField("source",          "STRING",    mode="NULLABLE",
                         description="FULL_CALC | INCREMENTAL | LEGACY_SYNC"),
]

STOCK_CALC_LOG_TABLE_OPTIONS = dict(
    schema=STOCK_CALC_LOG_SCHEMA,
    time_partitioning=bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="calc_time",
        expiration_ms=None,   # keep forever — it is the audit log
    ),
    clustering_fields=["branch_id", "item_code"],
    labels={"app": "purchase-erp", "layer": "stock-engine"},
)

# ── ERP_StockCalc view DDL (latest row per branch+item) ───────────────────
STOCK_CALC_VIEW_DDL = """
CREATE OR REPLACE VIEW `{view_id}` AS
SELECT
  branch_id,
  item_code,
  stock_qty,
  stock_value,
  rate,
  opening_qty,
  grn_in_qty,
  issue_out_qty,
  return_in_qty,
  trf_in_qty,
  trf_out_qty,
  adj_qty,
  movement_count,
  source,
  calc_time
FROM (
  SELECT *,
    ROW_NUMBER() OVER (
      PARTITION BY branch_id, item_code
      ORDER BY calc_time DESC
    ) AS rn
  FROM `{log_id}`
)
WHERE rn = 1
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
