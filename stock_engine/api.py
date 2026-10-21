"""
FastAPI REST service — Evergreen Purchase ERP Stock API.

Deployed on Cloud Run; called by Apps Script via UrlFetchApp.
BigQuery is the only database.  Stock is live via a materialized view
that BigQuery refreshes automatically — no trigger or scheduler needed.

Endpoints:
  POST /api/v1/transaction              — record one stock movement
  POST /api/v1/transactions/batch       — record multiple movements
  POST /api/v1/transaction/reverse      — cancel an earlier movement
  GET  /api/v1/stock                    — live stock (all or filtered)
  GET  /api/v1/stock/{branch}/{item}    — live stock for one item
  GET  /api/v1/ledger                   — raw movement history
  GET  /api/v1/exceptions               — negative stock / no-movement items
  GET  /health
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from google.cloud import bigquery

from . import config
from .ledger import StockMovement, write_transaction, write_transactions_batch, reverse_transaction

app = FastAPI(
    title="Evergreen Purchase ERP — Stock API",
    version="2.0.0",
    description="BigQuery-native live stock. No Sheets. No trigger.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_bq_client: bigquery.Client | None = None


def _get_client() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(
            project=config.PROJECT_ID,
            location=config.BQ_LOCATION,
        )
    return _bq_client


# ── Pydantic models ───────────────────────────────────────────────────────

class TransactionIn(BaseModel):
    branch_id:     str
    item_code:     str
    movement_type: str
    qty:           float = Field(ge=0, description="Always non-negative; direction from movement_type")
    value:         float = Field(default=0.0, ge=0)
    txn_date:      Optional[date] = None
    doc_id:        str = ""
    line_id:       str = ""
    rate:          Optional[float] = None
    lot_id:        str = ""
    notes:         str = ""
    created_by:    str = ""


class TransactionOut(BaseModel):
    ledger_id: str
    status:    str = "ok"


class BatchOut(BaseModel):
    ledger_ids: list[str]
    count:      int
    status:     str = "ok"


class ReverseIn(BaseModel):
    original_ledger_id: str
    branch_id:          str
    item_code:          str
    movement_type:      str
    qty:                float = Field(ge=0)
    value:              float = Field(default=0.0, ge=0)
    reversed_by:        str = ""


class StockRow(BaseModel):
    branch_id:        str
    item_code:        str
    stock_qty:        float
    stock_value:      float
    last_movement_at: Optional[datetime]
    movement_count:   int


class StockResponse(BaseModel):
    rows:   list[StockRow]
    count:  int
    as_of:  datetime


# ── Helpers ───────────────────────────────────────────────────────────────

def _to_movement(body: TransactionIn) -> StockMovement:
    return StockMovement(
        branch_id=body.branch_id,
        item_code=body.item_code,
        movement_type=body.movement_type,
        qty=body.qty,
        value=body.value,
        txn_date=body.txn_date,
        doc_id=body.doc_id,
        line_id=body.line_id,
        rate=body.rate,
        lot_id=body.lot_id,
        notes=body.notes,
        created_by=body.created_by,
    )


def _run_query(client: bigquery.Client, sql: str, params: list | None = None):
    cfg = bigquery.QueryJobConfig(query_parameters=params or [])
    return list(client.query(sql, job_config=cfg).result())


# ── Endpoints ─────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/v1/transaction", response_model=TransactionOut)
def post_transaction(
    body: TransactionIn,
    client: bigquery.Client = Depends(_get_client),
):
    try:
        lid = write_transaction(client, _to_movement(body))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    return TransactionOut(ledger_id=lid)


@app.post("/api/v1/transactions/batch", response_model=BatchOut)
def post_transactions_batch(
    body: list[TransactionIn],
    client: bigquery.Client = Depends(_get_client),
):
    if not body:
        raise HTTPException(422, "Request body must contain at least one transaction")
    try:
        lids = write_transactions_batch(client, [_to_movement(t) for t in body])
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    return BatchOut(ledger_ids=lids, count=len(lids))


@app.post("/api/v1/transaction/reverse", response_model=TransactionOut)
def post_reversal(
    body: ReverseIn,
    client: bigquery.Client = Depends(_get_client),
):
    try:
        lid = reverse_transaction(
            client,
            original_ledger_id=body.original_ledger_id,
            branch_id=body.branch_id,
            item_code=body.item_code,
            movement_type=body.movement_type,
            qty=body.qty,
            value=body.value,
            reversed_by=body.reversed_by,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    return TransactionOut(ledger_id=lid)


@app.get("/api/v1/stock", response_model=StockResponse)
def get_stock(
    branch_id: Optional[str] = Query(None),
    item_code: Optional[str] = Query(None),
    client: bigquery.Client = Depends(_get_client),
):
    """
    Returns live stock from the BigQuery materialized view.
    The view auto-refreshes every 5 minutes — no trigger needed.
    """
    clauses, params = [], []
    if branch_id:
        clauses.append("branch_id = @branch_id")
        params.append(bigquery.ScalarQueryParameter("branch_id", "STRING", branch_id))
    if item_code:
        clauses.append("item_code = @item_code")
        params.append(bigquery.ScalarQueryParameter("item_code", "STRING", item_code))

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT branch_id, item_code, stock_qty, stock_value,
               last_movement_at, movement_count
        FROM `{config.STOCK_CALC_VIEW}`
        {where}
        ORDER BY branch_id, item_code
    """
    rows = _run_query(client, sql, params)
    return StockResponse(
        rows=[
            StockRow(
                branch_id=r["branch_id"],
                item_code=r["item_code"],
                stock_qty=float(r["stock_qty"] or 0),
                stock_value=float(r["stock_value"] or 0),
                last_movement_at=r.get("last_movement_at"),
                movement_count=int(r["movement_count"] or 0),
            )
            for r in rows
        ],
        count=len(rows),
        as_of=datetime.now(timezone.utc),
    )


@app.get("/api/v1/stock/{branch_id}/{item_code}", response_model=StockRow)
def get_stock_item(
    branch_id: str,
    item_code: str,
    client: bigquery.Client = Depends(_get_client),
):
    sql = f"""
        SELECT branch_id, item_code, stock_qty, stock_value,
               last_movement_at, movement_count
        FROM `{config.STOCK_CALC_VIEW}`
        WHERE branch_id = @branch_id AND item_code = @item_code
        LIMIT 1
    """
    rows = _run_query(client, sql, [
        bigquery.ScalarQueryParameter("branch_id", "STRING", branch_id),
        bigquery.ScalarQueryParameter("item_code", "STRING", item_code),
    ])
    if not rows:
        raise HTTPException(404, f"No stock record for {branch_id}/{item_code}")
    r = rows[0]
    return StockRow(
        branch_id=r["branch_id"],
        item_code=r["item_code"],
        stock_qty=float(r["stock_qty"] or 0),
        stock_value=float(r["stock_value"] or 0),
        last_movement_at=r.get("last_movement_at"),
        movement_count=int(r["movement_count"] or 0),
    )


@app.get("/api/v1/ledger")
def get_ledger(
    branch_id: Optional[str] = Query(None),
    item_code:  Optional[str] = Query(None),
    since:      Optional[datetime] = Query(None, description="Filter movements after this time"),
    limit:      int = Query(500, le=5000),
    client: bigquery.Client = Depends(_get_client),
):
    """
    Raw movement history from stock_ledger.
    Partition pruning fires when `since` is provided.
    """
    clauses, params = ["(is_reversal IS NULL OR is_reversal = FALSE)"], []
    if branch_id:
        clauses.append("branch_id = @branch_id")
        params.append(bigquery.ScalarQueryParameter("branch_id", "STRING", branch_id))
    if item_code:
        clauses.append("item_code = @item_code")
        params.append(bigquery.ScalarQueryParameter("item_code", "STRING", item_code))
    if since:
        clauses.append("created_at >= @since")
        params.append(bigquery.ScalarQueryParameter("since", "TIMESTAMP", since))

    sql = f"""
        SELECT ledger_id, txn_date, branch_id, item_code, doc_id, line_id,
               movement_type, qty_in, qty_out, rate, value, lot_id, created_by, created_at
        FROM `{config.STOCK_LEDGER}`
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at DESC
        LIMIT @lim
    """
    params.append(bigquery.ScalarQueryParameter("lim", "INT64", limit))
    rows = _run_query(client, sql, params)
    return {"rows": [dict(r) for r in rows], "count": len(rows)}


@app.get("/api/v1/exceptions")
def get_exceptions(client: bigquery.Client = Depends(_get_client)):
    """Items with negative stock or no movements in 90 days."""
    sql = f"""
        SELECT
            branch_id,
            item_code,
            ROUND(stock_qty, 6)   AS stock_qty,
            ROUND(stock_value, 2) AS stock_value,
            last_movement_at,
            movement_count,
            CASE
                WHEN stock_qty < 0
                    THEN 'NEGATIVE_STOCK'
                WHEN last_movement_at < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
                    THEN 'NO_MOVEMENTS'
                ELSE 'OTHER'
            END AS exception_type
        FROM `{config.STOCK_CALC_VIEW}`
        WHERE stock_qty < 0
           OR last_movement_at < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
        ORDER BY stock_qty ASC, last_movement_at ASC
    """
    rows = _run_query(client, sql)
    return {"exceptions": [dict(r) for r in rows], "count": len(rows)}
