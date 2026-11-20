from datetime import date
from decimal import Decimal

import pytest

from data_agent.application.run_sql import MAX_ROWS, VISIBLE_REGIONS, QueryError, SqlTool
from tests.fakes import FakeRunner


async def test_rows_come_back_as_csv():
    runner = FakeRunner(
        columns=["sku", "price", "day", "promo"],
        rows=[("DAI-002", Decimal("7.20"), date(2026, 10, 5), None)],
    )
    result = await SqlTool(runner).run_sql("SELECT * FROM fact_sales")
    assert result == "sku,price,day,promo\nDAI-002,7.20,2026-10-05,NULL\n"


async def test_rejected_sql_never_reaches_the_database():
    runner = FakeRunner()
    result = await SqlTool(runner).run_sql("SELECT * FROM rls.user_region")
    assert result == "rejected: rls.user_region isn't one of the pricing tables"
    assert runner.queries == []


async def test_database_errors_reach_the_model_as_query_failed_only():
    result = await SqlTool(FakeRunner(fail=True)).run_sql("SELECT 1/0 FROM fact_sales")
    assert result == "query failed"


async def test_results_are_capped_and_say_so():
    runner = FakeRunner(rows=[(n,) for n in range(MAX_ROWS + 50)])
    result = await SqlTool(runner).run_sql("SELECT units FROM fact_sales")
    assert runner.queries == [("SELECT units FROM fact_sales", MAX_ROWS + 1)]
    lines = result.splitlines()
    assert len(lines) == 1 + MAX_ROWS + 1
    assert lines[-1].startswith(f"(first {MAX_ROWS} rows only")


async def test_an_empty_result_says_no_rows():
    assert (
        await SqlTool(FakeRunner(rows=[])).run_sql("SELECT n FROM fact_sales") == "n\n(no rows)\n"
    )


async def test_visible_regions_come_from_the_database_as_the_user():
    runner = FakeRunner(regions=("S", "N"))
    assert await SqlTool(runner).visible_regions() == ["N", "S"]
    assert runner.queries[0][0] == VISIBLE_REGIONS


async def test_a_query_error_is_reported_so_the_model_can_fix_its_sql():
    class Typo(FakeRunner):
        async def query(self, sql, max_rows):
            raise QueryError("Invalid column name 'regionid'.")

    result = await SqlTool(Typo()).run_sql("SELECT regionid FROM fact_sales")
    assert result == "query error: Invalid column name 'regionid'."


async def test_margin_is_one_tested_query_with_bound_filters():
    runner = FakeRunner(
        columns=["net_sales_mad", "cogs_mad", "margin_pct"],
        rows=[(Decimal("506679.50"), Decimal("398765.86"), Decimal("21.3"))],
    )
    result = await SqlTool(runner).margin("2026-10-01", "2026-10-31", "N", "Dairy", None)
    assert result == "net_sales_mad,cogs_mad,margin_pct\n506679.50,398765.86,21.3\n"
    sql = runner.queries[0][0]
    assert "JOIN dbo.dim_product AS p ON p.sku = f.sku" in sql
    assert sql.endswith("AND f.region_id = ? AND p.category = ?")
    assert runner.params[0] == (date(2026, 10, 1), date(2026, 10, 31), "N", "Dairy")


async def test_margin_by_product_across_the_visible_regions():
    runner = FakeRunner(columns=["net_sales_mad", "cogs_mad", "margin_pct"], rows=[(1, 1, 0)])
    await SqlTool(runner).margin("2026-08-11", "2026-08-31", None, None, "BEV-001")
    assert runner.queries[0][0].endswith("AND f.sku = ?")
    assert runner.params[0][2:] == ("BEV-001",)


@pytest.mark.parametrize(
    ("args", "reason"),
    [
        (("Oct 2026", "2026-10-31", "N", None, None), "real dates as YYYY-MM-DD"),
        (("2026-02-01", "2026-02-29", "N", None, None), "real dates as YYYY-MM-DD"),
        (("2026-10-31", "2026-10-01", "N", None, None), "start after end"),
        (("2026-10-01", "2026-10-31", "X", None, None), "region"),
        (("2026-10-01", "2026-10-31", None, "Toys", None), "category"),
        (("2026-10-01", "2026-10-31", None, None, "1; DROP"), "sku"),
    ],
)
async def test_bad_margin_arguments_never_reach_the_database(args, reason):
    runner = FakeRunner()
    result = await SqlTool(runner).margin(*args)
    assert result.startswith("rejected:") and reason in result
    assert runner.queries == []


async def test_a_margin_with_no_rows_says_so():
    runner = FakeRunner(
        columns=["net_sales_mad", "cogs_mad", "margin_pct"], rows=[(None, None, None)]
    )
    result = await SqlTool(runner).margin("2026-07-15", "2026-07-15", "S", None, None)
    assert result == "no sales rows for that period and filter"
