"""
Transaction writer — writes stock movements directly to BigQuery.
BigQuery is the primary database; Google Sheets is not involved.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional
import uuid

from google.cloud import bigquery
from . import config

# IN movements fill qty_in; OUT movements fill qty_out
_IN_TYPES  = {"GRN_IN", "RETURN_IN", "TRF_IN", "ADJ_PLUS", "CHALLAN_IN", "OPENING"}
_OUT_TYPES = {"ISSUE_OUT", "RETURN_OUT", "TRF_OUT", "ADJ_MINUS", "CHALLAN_OUT"}
VALID_MOVEMENT_TYPES = _IN_TYPES | _OUT_TYPES


@dataclass
class StockMovement:
    branch_id:          str
    item_code:          str
    movement_type:      str
    qty:                float
    value:              float = 0.0
    txn_date:           Optional[date] = None
    doc_id:             str = ""
    line_id:            str = ""
    rate:               Optional[float] = None
    lot_id:             str = ""
    notes:              str = ""
    is_reversal:        bool = False
    reversed_ledger_id: Optional[str] = None
    ledger_id:          str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at:         datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_by:         str = ""

    def validate(self) -> None:
        if self.movement_type not in VALID_MOVEMENT_TYPES:
            raise ValueError(f"Invalid movement_type {self.movement_type!r}. "
                             f"Valid: {sorted(VALID_MOVEMENT_TYPES)}")
        if self.qty < 0:
            raise ValueError("qty must be >= 0; direction is encoded in movement_type")
        if not self.branch_id.strip():
            raise ValueError("branch_id is required")
        if not self.item_code.strip():
            raise ValueError("item_code is required")

    def to_bq_row(self) -> dict:
        self.validate()
        qty_in  = self.qty if self.movement_type in _IN_TYPES  else None
        qty_out = self.qty if self.movement_type in _OUT_TYPES else None
        txn_date = (self.txn_date or self.created_at.date()).isoformat()
        return {
            "ledger_id":           self.ledger_id,
            "txn_date":            txn_date,
            "branch_id":           self.branch_id,
            "item_code":           self.item_code,
            "doc_id":              self.doc_id or None,
            "line_id":             self.line_id or None,
            "movement_type":       self.movement_type,
            "qty_in":              qty_in,
            "qty_out":             qty_out,
            "rate":                self.rate,
            "value":               self.value,
            "lot_id":              self.lot_id or None,
            "is_reversal":         self.is_reversal,
            "reversed_ledger_id":  self.reversed_ledger_id,
            "created_by":          self.created_by or None,
            "created_at":          self.created_at.isoformat(),
        }


def write_transaction(client: bigquery.Client, movement: StockMovement) -> str:
    """Validate and stream one movement row into BigQuery. Returns ledger_id."""
    row = movement.to_bq_row()
    errors = client.insert_rows_json(config.STOCK_LEDGER, [row])
    if errors:
        raise RuntimeError(f"BigQuery insert error: {errors}")
    return movement.ledger_id


def write_transactions_batch(
    client: bigquery.Client,
    movements: list[StockMovement],
) -> list[str]:
    """Stream multiple movement rows in one insert call. Returns list of ledger_ids."""
    rows = [m.to_bq_row() for m in movements]
    errors = client.insert_rows_json(config.STOCK_LEDGER, rows)
    if errors:
        raise RuntimeError(f"BigQuery batch insert error: {errors}")
    return [m.ledger_id for m in movements]


def reverse_transaction(
    client: bigquery.Client,
    original_ledger_id: str,
    branch_id: str,
    item_code: str,
    movement_type: str,
    qty: float,
    value: float = 0.0,
    reversed_by: str = "",
) -> str:
    """Write a reversal row to cancel an earlier transaction."""
    rev = StockMovement(
        branch_id=branch_id,
        item_code=item_code,
        movement_type=movement_type,
        qty=qty,
        value=value,
        is_reversal=True,
        reversed_ledger_id=original_ledger_id,
        created_by=reversed_by,
    )
    return write_transaction(client, rev)
