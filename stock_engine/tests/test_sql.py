"""
Unit tests for SQL generation and the ledger writer — no BigQuery connection needed.
"""
import pytest
from stock_engine import config
from stock_engine.schema import STOCK_CALC_MV_DDL
from stock_engine.ledger import (
    StockMovement, VALID_MOVEMENT_TYPES, _IN_TYPES, _OUT_TYPES,
)


LEDGER = "project.dataset.stock_ledger"
VIEW   = "project.dataset.ERP_StockCalc"


# ── Materialized View DDL ─────────────────────────────────────────────────

class TestMaterializedViewDdl:
    def test_renders(self):
        ddl = STOCK_CALC_MV_DDL.format(view_id=VIEW, ledger_id=LEDGER)
        assert isinstance(ddl, str) and len(ddl) > 100

    def test_contains_create_materialized_view(self):
        ddl = STOCK_CALC_MV_DDL.format(view_id=VIEW, ledger_id=LEDGER)
        assert "CREATE MATERIALIZED VIEW" in ddl

    def test_auto_refresh_enabled(self):
        ddl = STOCK_CALC_MV_DDL.format(view_id=VIEW, ledger_id=LEDGER)
        assert "enable_refresh = true" in ddl

    def test_refresh_interval_present(self):
        ddl = STOCK_CALC_MV_DDL.format(view_id=VIEW, ledger_id=LEDGER)
        assert "refresh_interval_minutes" in ddl

    def test_stock_qty_column(self):
        ddl = STOCK_CALC_MV_DDL.format(view_id=VIEW, ledger_id=LEDGER)
        assert "stock_qty" in ddl

    def test_stock_value_column(self):
        ddl = STOCK_CALC_MV_DDL.format(view_id=VIEW, ledger_id=LEDGER)
        assert "stock_value" in ddl

    def test_reversal_filter_present(self):
        ddl = STOCK_CALC_MV_DDL.format(view_id=VIEW, ledger_id=LEDGER)
        assert "is_reversal" in ddl

    def test_table_refs_substituted(self):
        ddl = STOCK_CALC_MV_DDL.format(view_id=VIEW, ledger_id=LEDGER)
        assert VIEW   in ddl
        assert LEDGER in ddl

    def test_no_literal_braces_remain(self):
        ddl = STOCK_CALC_MV_DDL.format(view_id=VIEW, ledger_id=LEDGER)
        assert "{" not in ddl and "}" not in ddl

    def test_group_by_branch_item(self):
        ddl = STOCK_CALC_MV_DDL.format(view_id=VIEW, ledger_id=LEDGER)
        assert "branch_id" in ddl
        assert "item_code"  in ddl


# ── StockMovement validation ──────────────────────────────────────────────

class TestStockMovementValidation:
    def _make(self, **kw) -> StockMovement:
        defaults = dict(branch_id="BR1", item_code="ITEM001",
                        movement_type="GRN_IN", qty=10.0, value=500.0)
        defaults.update(kw)
        return StockMovement(**defaults)

    def test_valid_movement_passes(self):
        m = self._make()
        m.validate()  # must not raise

    def test_invalid_movement_type_raises(self):
        with pytest.raises(ValueError, match="Invalid movement_type"):
            self._make(movement_type="INVALID_TYPE").validate()

    def test_negative_qty_raises(self):
        with pytest.raises(ValueError, match="qty must be"):
            self._make(qty=-1.0).validate()

    def test_empty_branch_raises(self):
        with pytest.raises(ValueError, match="branch_id"):
            self._make(branch_id="").validate()

    def test_empty_item_raises(self):
        with pytest.raises(ValueError, match="item_code"):
            self._make(item_code="  ").validate()

    @pytest.mark.parametrize("mt", sorted(VALID_MOVEMENT_TYPES))
    def test_all_movement_types_valid(self, mt):
        self._make(movement_type=mt).validate()


class TestStockMovementToRow:
    def _make(self, movement_type="GRN_IN", **kw) -> StockMovement:
        return StockMovement(branch_id="BR1", item_code="ITEM001",
                             movement_type=movement_type, qty=10.0,
                             value=500.0, **kw)

    def test_in_type_sets_qty_in(self):
        for mt in _IN_TYPES:
            row = self._make(movement_type=mt).to_bq_row()
            assert row["qty_in"] == 10.0, f"{mt} should set qty_in"
            assert row["qty_out"] is None, f"{mt} should leave qty_out None"

    def test_out_type_sets_qty_out(self):
        for mt in _OUT_TYPES:
            row = self._make(movement_type=mt).to_bq_row()
            assert row["qty_out"] == 10.0, f"{mt} should set qty_out"
            assert row["qty_in"] is None, f"{mt} should leave qty_in None"

    def test_row_has_ledger_id(self):
        row = self._make().to_bq_row()
        assert "ledger_id" in row and row["ledger_id"]

    def test_row_has_created_at(self):
        row = self._make().to_bq_row()
        assert "created_at" in row and row["created_at"]

    def test_is_reversal_default_false(self):
        row = self._make().to_bq_row()
        assert row["is_reversal"] is False

    def test_reversal_flag_set(self):
        row = self._make(is_reversal=True, reversed_ledger_id="orig-id").to_bq_row()
        assert row["is_reversal"] is True
        assert row["reversed_ledger_id"] == "orig-id"

    def test_txn_date_defaults_to_today(self):
        from datetime import date
        row = self._make().to_bq_row()
        assert row["txn_date"] == date.today().isoformat()


# ── Movement type completeness ────────────────────────────────────────────

class TestMovementTypeCoverage:
    ERP_TYPES = {
        "GRN_IN", "ISSUE_OUT", "RETURN_IN", "RETURN_OUT",
        "TRF_IN", "TRF_OUT", "ADJ_PLUS", "ADJ_MINUS",
        "CHALLAN_OUT", "CHALLAN_IN", "OPENING",
    }

    def test_all_erp_types_in_valid_set(self):
        missing = self.ERP_TYPES - VALID_MOVEMENT_TYPES
        assert not missing, f"Missing from VALID_MOVEMENT_TYPES: {missing}"

    def test_every_type_in_either_in_or_out(self):
        for mt in VALID_MOVEMENT_TYPES:
            assert mt in _IN_TYPES or mt in _OUT_TYPES, \
                f"{mt} not classified as IN or OUT"

    def test_no_overlap_between_in_and_out(self):
        overlap = _IN_TYPES & _OUT_TYPES
        assert not overlap, f"movement_types in both IN and OUT: {overlap}"
