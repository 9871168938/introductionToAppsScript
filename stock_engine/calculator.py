"""
StockCalculator — orchestrates BigQuery stock calculation runs.

All heavy lifting (aggregation over 10 lakh+ rows) is pushed into
BigQuery SQL; Python only submits jobs and reads back row counts.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPIError
from google.api_core import retry

from . import config, sql

log = logging.getLogger(__name__)


class StockCalculator:
    """
    Manages incremental and full BigQuery stock calculation runs.

    Usage:
        bq = bigquery.Client(project=config.PROJECT_ID)
        calc = StockCalculator(bq)
        rows = calc.run()       # auto-chooses incremental vs full
    """

    def __init__(self, client: bigquery.Client):
        self._client = client
        self._sql_kwargs = dict(
            inventory_stock=config.INVENTORY_STOCK,
            stock_ledger=config.STOCK_LEDGER,
            stock_calc_view=config.STOCK_CALC_VIEW,
            qty_epsilon=config.QTY_EPSILON,
            value_epsilon=config.VALUE_EPSILON,
        )

    # ── Public API ────────────────────────────────────────────────────────

    def run(self, force_full: bool = False) -> int:
        """
        Run one stock calculation cycle.

        If the log table has a previous run recorded, runs an incremental
        calc limited to items that changed since that run (fast path, ~seconds).
        Falls back to a full calc on the first run or when force_full=True.

        Returns the number of new rows written to ERP_StockCalc_Log.
        """
        last_ts = None if force_full else self._last_calc_time()

        if last_ts is None:
            log.info("No previous run found — running full stock recalculation")
            return self._run_full()

        log.info("Last calc at %s — running incremental", last_ts.isoformat())
        return self._run_incremental(since_time=last_ts)

    def get_exceptions(self) -> list[dict]:
        """
        Return items with negative stock or zero movements (audit use).
        """
        query = sql.exceptions_sql(config.STOCK_CALC_VIEW)
        rows = list(self._client.query(query).result(
            timeout=config.QUERY_TIMEOUT_SECS))
        return [dict(r) for r in rows]

    # ── Private helpers ───────────────────────────────────────────────────

    def _run_incremental(self, since_time: datetime) -> int:
        query = sql.incremental_sql(**self._sql_kwargs)
        job_cfg = self._append_job_config()
        job_cfg.query_parameters = [
            bigquery.ScalarQueryParameter(
                "since_time", "TIMESTAMP", since_time
            )
        ]
        return self._submit_and_wait(query, job_cfg, mode="INCREMENTAL")

    def _run_full(self) -> int:
        query = sql.full_calc_sql(**self._sql_kwargs)
        job_cfg = self._append_job_config()
        return self._submit_and_wait(query, job_cfg, mode="FULL_CALC")

    def _append_job_config(self) -> bigquery.QueryJobConfig:
        return bigquery.QueryJobConfig(
            destination=config.STOCK_CALC_LOG,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            time_partitioning=bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field="calc_time",
            ),
            clustering_fields=["branch_id", "item_code"],
            use_legacy_sql=False,
            create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
            # Prevent accidental full-table scans costing money
            maximum_bytes_billed=200 * 1024 ** 3,  # 200 GB cap
        )

    @retry.Retry(
        predicate=retry.if_transient_error,
        initial=2.0, maximum=60.0, multiplier=2.0, deadline=300.0,
    )
    def _submit_and_wait(
        self,
        query: str,
        job_cfg: bigquery.QueryJobConfig,
        mode: str,
    ) -> int:
        log.debug("Submitting %s query to BigQuery …", mode)
        job = self._client.query(query, job_config=job_cfg)

        try:
            job.result(timeout=config.QUERY_TIMEOUT_SECS)
        except GoogleAPIError as exc:
            log.error("BigQuery job %s failed: %s", job.job_id, exc)
            raise

        rows = job.num_dml_affected_rows or 0
        billed_gb = (job.total_bytes_billed or 0) / 1024 ** 3
        log.info(
            "%s complete — %d rows written, %.2f GB billed (job %s)",
            mode, rows, billed_gb, job.job_id,
        )
        return rows

    def _last_calc_time(self) -> Optional[datetime]:
        """Return the timestamp of the last log row, or None if the table is empty."""
        try:
            query = sql.last_calc_time_sql(config.STOCK_CALC_LOG)
            rows = list(self._client.query(query).result(
                timeout=30))
            if rows and rows[0].ts:
                return rows[0].ts.replace(tzinfo=timezone.utc)
        except GoogleAPIError as exc:
            log.warning("Could not read last calc time: %s", exc)
        return None
