"""
Unit tests for SQL generation — no BigQuery connection needed.
Verifies that the SQL templates render without syntax errors and
that the correct CTE blocks appear for full vs incremental paths.
"""
import pytest
from stock_engine import sql


INV  = "project.dataset.inventory_stock"
SL   = "project.dataset.stock_ledger"
VIEW = "project.dataset.ERP_StockCalc"


class TestFullCalcSql:
    def test_renders(self):
        q = sql.full_calc_sql(INV, SL, VIEW)
        assert isinstance(q, str) and len(q) > 100

    def test_contains_opening_cte(self):
        q = sql.full_calc_sql(INV, SL, VIEW)
        assert "opening AS" in q

    def test_contains_ledger_agg_cte(self):
        q = sql.full_calc_sql(INV, SL, VIEW)
        assert "ledger_agg AS" in q

    def test_contains_calculated_cte(self):
        q = sql.full_calc_sql(INV, SL, VIEW)
        assert "calculated AS" in q

    def test_contains_changed_cte(self):
        q = sql.full_calc_sql(INV, SL, VIEW)
        assert "changed AS" in q

    def test_source_tag_is_full_calc(self):
        q = sql.full_calc_sql(INV, SL, VIEW)
        assert "'FULL_CALC'" in q

    def test_table_refs_substituted(self):
        q = sql.full_calc_sql(INV, SL, VIEW)
        assert INV  in q
        assert SL   in q
        assert VIEW in q

    def test_no_literal_braces_remain(self):
        q = sql.full_calc_sql(INV, SL, VIEW)
        # All {placeholder} tokens must be replaced
        assert "{" not in q and "}" not in q

    def test_epsilon_values_substituted(self):
        q = sql.full_calc_sql(INV, SL, VIEW, qty_epsilon=0.005, value_epsilon=0.05)
        assert "0.005" in q
        assert "0.05"  in q


class TestIncrementalSql:
    def test_renders(self):
        q = sql.incremental_sql(INV, SL, VIEW)
        assert isinstance(q, str) and len(q) > 100

    def test_contains_since_time_parameter(self):
        q = sql.incremental_sql(INV, SL, VIEW)
        assert "@since_time" in q

    def test_contains_changed_items_since_cte(self):
        q = sql.incremental_sql(INV, SL, VIEW)
        assert "changed_items_since AS" in q

    def test_source_tag_is_incremental(self):
        q = sql.incremental_sql(INV, SL, VIEW)
        assert "'INCREMENTAL'" in q

    def test_no_literal_braces_remain(self):
        q = sql.incremental_sql(INV, SL, VIEW)
        assert "{" not in q and "}" not in q

    def test_partition_pruning_comment_present(self):
        q = sql.incremental_sql(INV, SL, VIEW)
        assert "Partition pruning" in q or "partition pruning" in q.lower()

    def test_inner_join_on_changed_items(self):
        q = sql.incremental_sql(INV, SL, VIEW)
        assert "INNER JOIN changed_items_since" in q


class TestHelperSql:
    def test_last_calc_time_sql(self):
        q = sql.last_calc_time_sql("p.d.ERP_StockCalc_Log")
        assert "MAX(calc_time)" in q
        assert "p.d.ERP_StockCalc_Log" in q

    def test_exceptions_sql(self):
        q = sql.exceptions_sql(VIEW)
        assert "NEGATIVE_STOCK" in q
        assert "NO_MOVEMENTS"   in q
        assert VIEW in q


class TestStockFormula:
    """Verify the stock formula components appear in both query types."""

    @pytest.mark.parametrize("build_fn", [sql.full_calc_sql, sql.incremental_sql])
    def test_all_movement_types_covered(self, build_fn):
        q = build_fn(INV, SL, VIEW)
        # Every movement_type referenced in the ERP database reference doc
        for mt in ["GRN_IN", "ISSUE_OUT", "RETURN_IN",
                   "TRF_IN", "TRF_OUT", "ADJ_PLUS", "ADJ_MINUS",
                   "CHALLAN_OUT", "CHALLAN_IN", "RETURN_OUT"]:
            assert mt in q, f"movement_type {mt!r} missing from {build_fn.__name__}"

    @pytest.mark.parametrize("build_fn", [sql.full_calc_sql, sql.incremental_sql])
    def test_reversal_filter_present(self, build_fn):
        q = build_fn(INV, SL, VIEW)
        assert "is_reversal" in q

    @pytest.mark.parametrize("build_fn", [sql.full_calc_sql, sql.incremental_sql])
    def test_generate_uuid_used_for_log_id(self, build_fn):
        q = build_fn(INV, SL, VIEW)
        assert "GENERATE_UUID()" in q
