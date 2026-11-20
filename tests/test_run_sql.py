from datetime import date
from decimal import Decimal

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
