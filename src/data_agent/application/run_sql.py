"""The run_sql tool: one checked SELECT, run as the signed-in user. The model sees rows, our own
rejection reasons or "query failed"; never database error text, which can leak hidden rows."""

import csv
import io
import re
from datetime import date
from typing import Protocol

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
    "SELECT SUM(f.net_sales_mad) AS net_sales_mad, SUM(f.cogs_mad) AS cogs_mad, "
    "CAST((SUM(f.net_sales_mad) - SUM(f.cogs_mad)) * 100 / NULLIF(SUM(f.net_sales_mad), 0) "
    "AS decimal(6, 1)) AS margin_pct "
    "FROM dbo.fact_sales AS f JOIN dbo.dim_product AS p ON p.sku = f.sku "
    "WHERE f.sale_date BETWEEN ? AND ?"
)


class QueryFailed(Exception):
    """A database failure. Deliberately carries no database text."""


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
        try:
            check_select(sql)
        except SqlRejected as rejected:
            return f"rejected: {rejected}"
        try:
            columns, rows = await self._runner.query(sql, MAX_ROWS + 1)
        except QueryError as error:
            return f"query error: {error}"
        except QueryFailed:
            return "query failed"
        return as_csv(columns, rows)

    async def margin(
        self, start: str, end: str, region: str | None, category: str | None, sku: str | None
    ) -> str:
        try:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
        except (TypeError, ValueError):
            return "rejected: give dates as YYYY-MM-DD"
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
        sql, params = MARGIN, [first, last]
        for column, value in (("f.region_id", region), ("p.category", category), ("f.sku", sku)):
            if value:
                sql += f" AND {column} = ?"
                params.append(value)
        try:
            columns, rows = await self._runner.query(sql, 1, tuple(params))
        except QueryFailed:
            return "query failed"
        if not rows or rows[0][0] is None:
            return "no sales rows for that period and filter"
        return as_csv(columns, rows)

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
