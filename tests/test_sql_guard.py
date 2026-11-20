import pytest

from data_agent.domain.sql_guard import SqlRejected, check_select

ACCEPTED = [
    "SELECT TOP 10 * FROM fact_sales",
    "SELECT s.city FROM [dbo].[fact_sales] f JOIN dbo.dim_store s ON s.store_id = f.store_id",
    "WITH m AS (SELECT region_id, SUM(net_sales_mad) AS n FROM dbo.fact_sales GROUP BY region_id)"
    " SELECT * FROM m",
    "WITH a AS (SELECT sku FROM dim_product), b AS (SELECT sku FROM a) SELECT * FROM b",
    "SELECT sku FROM promotions UNION ALL SELECT sku FROM price_history",
    "SELECT * FROM dim_product p WHERE EXISTS (SELECT 1 FROM supplier_costs c WHERE c.sku = p.sku)",
    "SELECT region_id FROM competitor_prices;",
    "SELECT 1",
    "SELECT * FROM price_history"
    " WHERE effective_date >= '2026-02-01' AND effective_date < '2026-03-01'",
]

REJECTED = [
    ("", "exactly one statement"),
    ("-- nothing", "exactly one statement"),
    ("SELECT 1; SELECT 2", "exactly one statement"),
    ("SELECT * FROM fact_sales DROP TABLE dim_store", "couldn't be parsed"),
    ("SELECT 1 GO SELECT 2", "couldn't be parsed"),
    ("SELECT FROM WHERE", "couldn't be parsed"),
    ("DELETE FROM fact_sales", "only SELECT"),
    ("UPDATE dim_product SET tier = 'A'", "only SELECT"),
    ("EXEC sp_who", "only SELECT"),
    ("DECLARE @x int", "only SELECT"),
    ("DROP TABLE fact_sales", "only SELECT"),
    ("SELECT * INTO #copy FROM fact_sales", "plain SELECT"),
    ("SELECT * FROM fact_sales f CROSS APPLY OPENJSON(f.sku)", "plain SELECT"),
    ("SELECT * FROM rls.user_region", "rls.user_region isn't one of the pricing tables"),
    ("SELECT * FROM fact_sales WHERE sku IN (SELECT upn FROM rls.user_region)", "rls.user_region"),
    ("SELECT * FROM sys.objects", "sys.objects"),
    ("SELECT * FROM INFORMATION_SCHEMA.TABLES", "information_schema.tables"),
    ("SELECT * FROM otherdb.dbo.fact_sales", "isn't a table you can query"),
    ("SELECT * FROM srv.db.dbo.fact_sales", "isn't a table you can query"),
    ("SELECT * FROM OPENROWSET('x', 'y', 'z')", "isn't a table you can query"),
    ("SELECT * FROM STRING_SPLIT('a,b', ',')", "isn't a table you can query"),
    ("SELECT * FROM ##fact_sales", "temporary tables"),
    ("SELECT * FROM (WITH x AS (SELECT 1 AS n) SELECT n FROM x) t", "WITH is allowed only"),
    ("WITH a AS (SELECT * FROM later), later AS (SELECT 1 AS n) SELECT * FROM a", "dbo.later"),
    ("SELECT rls.fn_region_filter('N')", "schema-qualified functions"),
    ("SELECT * FROM user_region", "dbo.user_region"),
    # sqlglot reads an unquoted keyword after a table as an alias; SQL Server runs it.
    ("SELECT TOP 1 sku FROM fact_sales EXEC('SELECT 1')", "T-SQL keyword|alias column lists"),
    ("SELECT sku FROM fact_sales CHECKPOINT", "CHECKPOINT is a T-SQL keyword"),
    ("SELECT sku FROM fact_sales s SHUTDOWN", "couldn't be parsed"),
    ("SELECT * FROM fact_sales THROW", "THROW is a T-SQL keyword"),
    ("SELECT * FROM fact_sales MATCH(x)", "alias column lists"),
    ("SELECT * FROM dim_region -- ; DROP TABLE dim_region", "comments"),
    ("SELECT 1 /* note */", "comments"),
    ("SELECT sku FROM fact_sales --\x0bEXEC('x')", "control characters"),
    ("SELECT sku\u2028FROM fact_sales", "control characters"),
    ("SELECT * FROM price_history WHERE effective_date <= '2026-02-29'", "2026-02-29 isn't a real"),
    (
        "SELECT * FROM fact_sales WHERE sale_date BETWEEN '2026-04-01' AND '2026-04-31'",
        "2026-04-31",
    ),
]


@pytest.mark.parametrize("sql", ACCEPTED)
def test_plain_selects_over_the_pricing_tables_pass(sql):
    check_select(sql)


@pytest.mark.parametrize(("sql", "reason"), REJECTED)
def test_everything_else_is_rejected_with_a_reason(sql, reason):
    with pytest.raises(SqlRejected, match=reason):
        check_select(sql)
