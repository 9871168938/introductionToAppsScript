"""
BigQuery SQL templates for stock calculation.

Design principle: ALL aggregation and filtering happens inside BigQuery.
Python only passes parameters and reads the final row count.
This keeps memory usage O(1) in Python regardless of how large the
stock_ledger grows (10 lakh rows today, more tomorrow).

movement_type values (from ERP_DATABASE reference v2, section 7):
  GRN_IN        — goods received, QC accepted
  ISSUE_OUT     — issued to production / department
  RETURN_IN     — returned from department to store
  RETURN_OUT    — return to vendor (challan / debit note)
  TRF_OUT       — stock transferred to another branch (sending side)
  TRF_IN        — stock received from another branch (receiving side)
  ADJ_PLUS      — stock adjustment increase (management approved)
  ADJ_MINUS     — stock adjustment decrease (management approved)
  CHALLAN_OUT   — outward challan (non-returnable or pending return)
  CHALLAN_IN    — inward challan return
  OPENING       — opening balance row (written once per item)

Stock formula (section 9):
  stock = opening_qty
        + grn_in_qty
        - issue_out_qty
        + return_in_qty
        - return_out_qty  (RETURN_OUT / CHALLAN_OUT)
        + trf_in_qty
        - trf_out_qty
        + adj_plus_qty
        - adj_minus_qty
"""

# ── Shared CTEs reused in both full and incremental queries ───────────────

_OPENING_CTE = """
opening AS (
  -- One opening-stock row per BRANCH + ITEM_CODE.
  -- INVENTORY_STOCK.TIMESTAMP is the ERP cut-off date:
  -- only later stock_ledger entries count; entries before it are
  -- already baked into OLD_STOCK (section 9 of reference doc).
  SELECT
    branch_id,
    item_code,
    COALESCE(SUM(
      CASE
        WHEN cutoff_ts IS NOT NULL AND created_at <= cutoff_ts THEN old_stock
        WHEN cutoff_ts IS NULL THEN old_stock
        ELSE 0
      END
    ), 0) AS opening_qty,
    MAX(rate) AS opening_rate
  FROM (
    SELECT
      branch_id,
      item_code,
      old_stock,
      rate,
      created_at,
      -- The per-row TIMESTAMP column (cut-off date) lives in INVENTORY STOCK;
      -- we alias it so the CASE logic above reads cleanly.
      TIMESTAMP AS cutoff_ts
    FROM `{inventory_stock}`
    WHERE (is_active IS NULL OR is_active = TRUE)
  )
  {opening_filter}
  GROUP BY branch_id, item_code
)"""

_LEDGER_AGG_CTE = """
ledger_agg AS (
  -- Aggregate all stock_ledger movements per BRANCH + ITEM_CODE.
  -- Reversal rows cancel their paired row: we exclude both.
  -- Partition pruning fires automatically when created_at is filtered
  -- (the table is DATE-partitioned on created_at).
  SELECT
    sl.branch_id,
    sl.item_code,
    SUM(CASE WHEN sl.movement_type = 'GRN_IN'
             THEN COALESCE(sl.qty_in, 0) ELSE 0 END)   AS grn_in_qty,
    SUM(CASE WHEN sl.movement_type IN ('ISSUE_OUT')
             THEN COALESCE(sl.qty_out, 0) ELSE 0 END)  AS issue_out_qty,
    SUM(CASE WHEN sl.movement_type IN ('RETURN_IN')
             THEN COALESCE(sl.qty_in, 0) ELSE 0 END)   AS return_in_qty,
    SUM(CASE WHEN sl.movement_type IN ('RETURN_OUT', 'CHALLAN_OUT')
             THEN COALESCE(sl.qty_out, 0) ELSE 0 END)  AS return_out_qty,
    SUM(CASE WHEN sl.movement_type = 'CHALLAN_IN'
             THEN COALESCE(sl.qty_in, 0) ELSE 0 END)   AS challan_in_qty,
    SUM(CASE WHEN sl.movement_type = 'TRF_IN'
             THEN COALESCE(sl.qty_in, 0) ELSE 0 END)   AS trf_in_qty,
    SUM(CASE WHEN sl.movement_type = 'TRF_OUT'
             THEN COALESCE(sl.qty_out, 0) ELSE 0 END)  AS trf_out_qty,
    SUM(CASE WHEN sl.movement_type = 'ADJ_PLUS'
             THEN COALESCE(sl.qty_in, 0) ELSE 0 END)   AS adj_plus_qty,
    SUM(CASE WHEN sl.movement_type = 'ADJ_MINUS'
             THEN COALESCE(sl.qty_out, 0) ELSE 0 END)  AS adj_minus_qty,
    SUM(COALESCE(sl.value, 0))                          AS net_value,
    COUNT(*)                                            AS movement_count,
    MAX(sl.rate)                                        AS last_rate
  FROM `{stock_ledger}` sl
  {ledger_join}
  WHERE (sl.is_reversal IS NULL OR sl.is_reversal = FALSE)
  {ledger_filter}
  GROUP BY sl.branch_id, sl.item_code
)"""

_CALC_CTE = """
calculated AS (
  SELECT
    COALESCE(o.branch_id, l.branch_id)  AS branch_id,
    COALESCE(o.item_code,  l.item_code)  AS item_code,
    -- stock = opening + receipts - issues + returns - outward + transfers + adjustments
    COALESCE(o.opening_qty,  0)
      + COALESCE(l.grn_in_qty,    0)
      - COALESCE(l.issue_out_qty, 0)
      + COALESCE(l.return_in_qty, 0)
      - COALESCE(l.return_out_qty,0)
      + COALESCE(l.challan_in_qty,0)
      + COALESCE(l.trf_in_qty,    0)
      - COALESCE(l.trf_out_qty,   0)
      + COALESCE(l.adj_plus_qty,  0)
      - COALESCE(l.adj_minus_qty, 0)                    AS stock_qty,
    COALESCE(l.net_value,   0)                          AS stock_value,
    COALESCE(l.last_rate,   o.opening_rate)             AS rate,
    COALESCE(o.opening_qty, 0)                          AS opening_qty,
    COALESCE(l.grn_in_qty,    0)                        AS grn_in_qty,
    COALESCE(l.issue_out_qty, 0)                        AS issue_out_qty,
    COALESCE(l.return_in_qty, 0)                        AS return_in_qty,
    COALESCE(l.trf_in_qty,    0)                        AS trf_in_qty,
    COALESCE(l.trf_out_qty,   0)                        AS trf_out_qty,
    COALESCE(l.adj_plus_qty,  0)
      - COALESCE(l.adj_minus_qty, 0)                    AS adj_qty,
    COALESCE(l.movement_count, 0)                       AS movement_count
  FROM opening o
  FULL OUTER JOIN ledger_agg l
    ON o.branch_id = l.branch_id
   AND o.item_code  = l.item_code
)"""

_DIFF_CTE = """
last_known AS (
  SELECT branch_id, item_code,
         stock_qty  AS last_qty,
         stock_value AS last_value
  FROM `{stock_calc_view}`
  {last_known_filter}
),
changed AS (
  SELECT c.*
  FROM calculated c
  LEFT JOIN last_known lk
    ON c.branch_id = lk.branch_id
   AND c.item_code  = lk.item_code
  WHERE
    lk.branch_id IS NULL                                             -- new item
    OR ABS(COALESCE(c.stock_qty,  0) - COALESCE(lk.last_qty,   0)) > {qty_epsilon}
    OR ABS(COALESCE(c.stock_value,0) - COALESCE(lk.last_value, 0)) > {value_epsilon}
)"""

_SELECT_FINAL = """
SELECT
  GENERATE_UUID()        AS log_id,
  branch_id,
  item_code,
  CURRENT_TIMESTAMP()    AS calc_time,
  ROUND(stock_qty,    6) AS stock_qty,
  ROUND(stock_value,  2) AS stock_value,
  ROUND(rate,         6) AS rate,
  ROUND(opening_qty,  6) AS opening_qty,
  ROUND(grn_in_qty,   6) AS grn_in_qty,
  ROUND(issue_out_qty,6) AS issue_out_qty,
  ROUND(return_in_qty,6) AS return_in_qty,
  ROUND(trf_in_qty,   6) AS trf_in_qty,
  ROUND(trf_out_qty,  6) AS trf_out_qty,
  ROUND(adj_qty,      6) AS adj_qty,
  movement_count,
  '{source}'             AS source
FROM changed
"""


# ── Public query builders ─────────────────────────────────────────────────

def full_calc_sql(
    inventory_stock: str,
    stock_ledger: str,
    stock_calc_view: str,
    qty_epsilon: float = 0.0001,
    value_epsilon: float = 0.01,
) -> str:
    """
    Full recalculation: every BRANCH + ITEM_CODE in the system.
    Use for first run or monthly reconciliation.
    BigQuery scans the entire stock_ledger; with partitioning + clustering
    the cost is still linear in data size, not quadratic.
    """
    opening_cte   = _OPENING_CTE.format(
        inventory_stock=inventory_stock,
        opening_filter="",
    )
    ledger_cte    = _LEDGER_AGG_CTE.format(
        stock_ledger=stock_ledger,
        ledger_join="",
        ledger_filter="",
    )
    calc_cte      = _CALC_CTE
    diff_cte      = _DIFF_CTE.format(
        stock_calc_view=stock_calc_view,
        last_known_filter="",
        qty_epsilon=qty_epsilon,
        value_epsilon=value_epsilon,
    )
    select_final  = _SELECT_FINAL.format(source="FULL_CALC")

    return (
        "WITH\n"
        + opening_cte  + ",\n"
        + ledger_cte   + ",\n"
        + calc_cte     + ",\n"
        + diff_cte     + "\n"
        + select_final
    )


def incremental_sql(
    inventory_stock: str,
    stock_ledger: str,
    stock_calc_view: str,
    qty_epsilon: float = 0.0001,
    value_epsilon: float = 0.01,
) -> str:
    """
    Incremental recalculation: only items with ledger activity since @since_time.

    @since_time is a BigQuery TIMESTAMP query parameter injected at run time.
    This means BigQuery will use the partition on created_at and skip ALL
    partitions older than @since_time — the dominant cost saving for 10 lakh+
    rows running every 5 minutes.
    """
    # Only open items that appear in changed_items_since
    changed_items_cte = """
changed_items_since AS (
  -- Items whose stock_ledger was touched since @since_time.
  -- Partition pruning on created_at fires here.
  SELECT DISTINCT branch_id, item_code
  FROM `{stock_ledger}`
  WHERE created_at >= @since_time
    AND (is_reversal IS NULL OR is_reversal = FALSE)
)""".format(stock_ledger=stock_ledger)

    opening_cte = _OPENING_CTE.format(
        inventory_stock=inventory_stock,
        opening_filter="WHERE (branch_id, item_code) IN "
                       "(SELECT branch_id, item_code FROM changed_items_since)",
    )

    # ledger_agg uses an INNER JOIN on changed_items_since to limit scope,
    # but aggregates ALL history (not just since @since_time) so the total
    # is correct even when reversals span multiple time windows.
    ledger_cte = _LEDGER_AGG_CTE.format(
        stock_ledger=stock_ledger,
        ledger_join="INNER JOIN changed_items_since cis\n"
                    "    ON sl.branch_id = cis.branch_id\n"
                    "   AND sl.item_code  = cis.item_code",
        ledger_filter="",
    )

    calc_cte   = _CALC_CTE

    # Narrow last_known to the same items to avoid a full view scan
    diff_cte   = _DIFF_CTE.format(
        stock_calc_view=stock_calc_view,
        last_known_filter="WHERE (branch_id, item_code) IN "
                          "(SELECT branch_id, item_code FROM changed_items_since)",
        qty_epsilon=qty_epsilon,
        value_epsilon=value_epsilon,
    )

    select_final = _SELECT_FINAL.format(source="INCREMENTAL")

    return (
        "WITH\n"
        + changed_items_cte + ",\n"
        + opening_cte       + ",\n"
        + ledger_cte        + ",\n"
        + calc_cte          + ",\n"
        + diff_cte          + "\n"
        + select_final
    )


# ── Helper queries ────────────────────────────────────────────────────────

def last_calc_time_sql(stock_calc_log: str) -> str:
    return f"SELECT MAX(calc_time) AS ts FROM `{stock_calc_log}`"


def exceptions_sql(stock_calc_view: str) -> str:
    """
    Returns items with negative stock or zero movement_count
    (ref: section 9 Exceptions report).
    """
    return f"""
SELECT
  branch_id,
  item_code,
  stock_qty,
  stock_value,
  movement_count,
  calc_time,
  CASE
    WHEN stock_qty < 0 THEN 'NEGATIVE_STOCK'
    WHEN movement_count = 0 THEN 'NO_MOVEMENTS'
    ELSE 'OTHER'
  END AS exception_type
FROM `{stock_calc_view}`
WHERE stock_qty < 0 OR movement_count = 0
ORDER BY branch_id, item_code
"""
