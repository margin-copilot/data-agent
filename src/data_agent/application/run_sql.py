"""The run_sql tool: one checked SELECT, run as the signed-in user. The model sees rows, our own
rejection reasons or "query failed"; never database error text, which can leak hidden rows."""

import csv
import io
import re
from datetime import date
from typing import Protocol

from margin_copilot_shared.telemetry import span

from data_agent.domain.sql_guard import SqlRejected, check_select

MAX_ROWS = 200
MAX_RESULT_CHARS = 16_000
VISIBLE_REGIONS = "SELECT DISTINCT region_id FROM dbo.fact_sales"
REGIONS = frozenset({"N", "C", "S"})
CATEGORIES = frozenset({"Bakery", "Beverages", "Dairy", "Grocery", "Household", "Produce"})
SKU = re.compile(r"[A-Z]{3}-\d{3}")
# Margin is the figure the stories turn on, so one tested query computes it every time (the
# category needs the product join, which hand-written SQL can forget).
MARGIN = (
    "SUM(f.net_sales_mad) AS net_sales_mad, SUM(f.cogs_mad) AS cogs_mad, "
    "CAST((SUM(f.net_sales_mad) - SUM(f.cogs_mad)) * 100 / NULLIF(SUM(f.net_sales_mad), 0) "
    "AS decimal(6, 1)) AS margin_pct "
    "FROM dbo.fact_sales AS f JOIN dbo.dim_product AS p ON p.sku = f.sku "
    "WHERE f.sale_date BETWEEN ? AND ?"
)
# Without a region, RLS silently limits the total to the regions the user can see: show each one.
BY_REGION = (
    "CASE WHEN GROUPING(f.region_id) = 1 THEN 'total of the regions you can see' "
    "ELSE f.region_id END AS region, "
)
GROUPED = " GROUP BY GROUPING SETS ((f.region_id), ()) ORDER BY GROUPING(f.region_id), f.region_id"
# margin_change: why a category's margin moved between two periods, in one call (each figure
# computed here, so the model quotes rather than computes). Every value is a bound parameter.
_CATEGORY = " AND f.region_id = ? AND p.category = ?"
CHANGE_TOTALS = (
    f"SELECT 'before' AS period, {MARGIN}{_CATEGORY} "
    f"UNION ALL SELECT 'after' AS period, {MARGIN}{_CATEGORY}"
)
CHANGE_PRODUCTS = (
    "WITH s AS (SELECT f.sku, CASE WHEN f.sale_date <= ? THEN 'before' ELSE 'after' END AS period, "
    "f.net_sales_mad, f.cogs_mad FROM dbo.fact_sales AS f "
    "JOIN dbo.dim_product AS p ON p.sku = f.sku "
    "WHERE f.region_id = ? AND p.category = ? "
    "AND (f.sale_date BETWEEN ? AND ? OR f.sale_date BETWEEN ? AND ?)), "
    "m AS (SELECT sku, "
    "SUM(CASE WHEN period = 'before' THEN net_sales_mad END) AS net_before, "
    "SUM(CASE WHEN period = 'before' THEN cogs_mad END) AS cogs_before, "
    "SUM(CASE WHEN period = 'after' THEN net_sales_mad END) AS net_after, "
    "SUM(CASE WHEN period = 'after' THEN cogs_mad END) AS cogs_after FROM s GROUP BY sku), "
    "r AS (SELECT sku, net_before, net_after, "
    "(net_before - cogs_before) * 100 / NULLIF(net_before, 0) AS before_pct, "
    "(net_after - cogs_after) * 100 / NULLIF(net_after, 0) AS after_pct FROM m) "
    "SELECT r.sku, p.product_name, CAST(r.net_before AS decimal(14, 2)) AS net_sales_before_mad, "
    "CAST(r.before_pct AS decimal(6, 1)) AS margin_before_pct, "
    "CAST(r.net_after AS decimal(14, 2)) AS net_sales_after_mad, "
    "CAST(r.after_pct AS decimal(6, 1)) AS margin_after_pct, "
    "CAST(r.after_pct - r.before_pct AS decimal(6, 1)) AS margin_change_pp "
    "FROM r JOIN dbo.dim_product AS p ON p.sku = r.sku ORDER BY r.after_pct - r.before_pct, r.sku"
)
CHANGE_COSTS = (
    "SELECT c.sku, p.product_name, c.supplier_id, c.effective_from, c.previous_cost_mad, "
    "c.unit_cost_mad, CAST((c.unit_cost_mad - c.previous_cost_mad) * 100 "
    "/ NULLIF(c.previous_cost_mad, 0) AS decimal(6, 1)) AS change_pct FROM (SELECT sku, region_id, "
    "supplier_id, effective_from, unit_cost_mad, LAG(unit_cost_mad) OVER (PARTITION BY sku, "
    "region_id ORDER BY effective_from) AS previous_cost_mad FROM dbo.supplier_costs) AS c "
    "JOIN dbo.dim_product AS p ON p.sku = c.sku "
    "WHERE c.region_id = ? AND p.category = ? AND c.effective_from BETWEEN ? AND ? "
    "ORDER BY c.effective_from, c.sku"
)
CHANGE_PROMOTIONS = (
    "SELECT pr.promo_id, pr.sku, p.product_name, pr.start_date, pr.end_date, pr.discount_pct, "
    "pr.supplier_funded_pct FROM dbo.promotions AS pr JOIN dbo.dim_product AS p ON p.sku = pr.sku "
    "WHERE pr.region_id = ? AND p.category = ? AND pr.start_date <= ? AND pr.end_date >= ? "
    "ORDER BY pr.start_date, pr.sku"
)
CHANGE_PRICES = (
    "SELECT h.sku, p.product_name, h.effective_date, h.old_price_mad, h.new_price_mad, "
    "h.change_pct, h.reason_code FROM dbo.price_history AS h JOIN dbo.dim_product AS p "
    "ON p.sku = h.sku WHERE h.region_id = ? AND p.category = ? "
    "AND h.effective_date BETWEEN ? AND ? "
    "ORDER BY h.effective_date, h.sku"
)


class QueryFailed(Exception):
    """A database failure. Deliberately carries no database text."""


class NoDatabaseAccess(QueryFailed):
    """The database refused the signed-in user's login: their account isn't one it knows."""


class QueryError(QueryFailed):
    """Raised before any row is read (syntax, unknown names): its text names only what the query
    itself wrote, so the model may see it and fix the query."""


class SqlRunner(Protocol):
    async def query(
        self, sql: str, max_rows: int, params: tuple = ()
    ) -> tuple[list[str], list[tuple]]: ...


class SqlTool:
    def __init__(self, runner: SqlRunner) -> None:
        self._runner = runner

    async def run_sql(self, sql: str) -> str:
        labels = {"margin.sql.rejected": False, "margin.sql.failed": False}
        with span("data_agent.run_sql", **labels) as ran:
            try:
                check_select(sql)
            except SqlRejected as rejected:
                ran.set_attribute("margin.sql.rejected", True)
                return f"rejected: {rejected}"
            try:
                columns, rows = await self._runner.query(sql, MAX_ROWS + 1)
            except QueryError as error:
                ran.set_attribute("margin.sql.failed", True)
                return f"query error: {error}"
            except QueryFailed:
                ran.set_attribute("margin.sql.failed", True)
                return "query failed"
            ran.set_attribute("margin.sql.rows", len(rows))
            return as_csv(columns, rows)

    async def margin(
        self, start: str, end: str, region: str | None, category: str | None, sku: str | None
    ) -> str:
        try:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
        except (TypeError, ValueError):
            return "rejected: give real dates as YYYY-MM-DD"
        problems = [
            *(["start after end"] if first > last else []),
            *(
                [f"region must be one of {sorted(REGIONS)}"]
                if region and region not in REGIONS
                else []
            ),
            *(
                [f"category must be one of {sorted(CATEGORIES)}"]
                if category and category not in CATEGORIES
                else []
            ),
            *(["sku looks like DAI-002"] if sku and not SKU.fullmatch(sku) else []),
        ]
        if problems:
            return f"rejected: {'; '.join(problems)}"
        sql, params = "SELECT " + ("" if region else BY_REGION) + MARGIN, [first, last]
        for column, value in (("f.region_id", region), ("p.category", category), ("f.sku", sku)):
            if value:
                sql += f" AND {column} = ?"
                params.append(value)
        sql += "" if region else GROUPED
        try:
            columns, rows = await self._runner.query(sql, 5, tuple(params))
        except QueryFailed:
            return "query failed"
        if all(row[-3] is None for row in rows):  # net_sales_mad
            return "no sales rows for that period and filter"
        return as_csv(columns, rows)

    async def margin_change(
        self,
        before_start: str,
        before_end: str,
        after_start: str,
        after_end: str,
        region: str,
        category: str,
    ) -> str:
        try:
            bs, be, a_s, ae = (
                date.fromisoformat(d) for d in (before_start, before_end, after_start, after_end)
            )
        except (TypeError, ValueError):
            return "rejected: give real dates as YYYY-MM-DD"
        problems = [
            *(["the period before must start no later than it ends"] if bs > be else []),
            *(["the period before must end before the period after starts"] if be >= a_s else []),
            *(["the period after must start no later than it ends"] if a_s > ae else []),
            *([f"region must be one of {sorted(REGIONS)}"] if region not in REGIONS else []),
            *(
                [f"category must be one of {sorted(CATEGORIES)}"]
                if category not in CATEGORIES
                else []
            ),
        ]
        if problems:
            return f"rejected: {'; '.join(problems)}"
        queries = (
            ("category", CHANGE_TOTALS, (bs, be, region, category, a_s, ae, region, category)),
            ("products", CHANGE_PRODUCTS, (be, region, category, bs, be, a_s, ae)),
            ("cost changes", CHANGE_COSTS, (region, category, bs, ae)),
            ("promotions", CHANGE_PROMOTIONS, (region, category, ae, bs)),
            ("price changes", CHANGE_PRICES, (region, category, bs, ae)),
        )
        sections = []
        try:
            for title, sql, params in queries:
                columns, rows = await self._runner.query(sql, MAX_ROWS, params)
                if title == "category" and all(row[1] is None for row in rows):  # net_sales_mad
                    return "no sales rows for that period and filter"
                sections.append(f"{title}\n{as_csv(columns, rows).rstrip() if rows else 'none'}")
        except QueryFailed:
            return "query failed"
        return "\n\n".join(sections)

    async def visible_regions(self) -> list[str]:
        """Regions whose rows RLS lets this user see. A failure here fails the whole request."""
        _, rows = await self._runner.query(VISIBLE_REGIONS, 10)
        return sorted(str(row[0]) for row in rows)


def as_csv(columns: list[str], rows: list[tuple]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(
        ["NULL" if value is None else value for value in row] for row in rows[:MAX_ROWS]
    )
    if not rows:
        out.write("(no rows)\n")
    if len(rows) > MAX_ROWS:
        out.write(f"(first {MAX_ROWS} rows only; aggregate or filter to see the rest)\n")
    text = out.getvalue()
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n(cut at {MAX_RESULT_CHARS} characters)\n"
    return text
