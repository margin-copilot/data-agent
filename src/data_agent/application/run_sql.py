"""The run_sql tool: one checked SELECT, run as the signed-in user. The model sees rows, our own
rejection reasons or "query failed"; never database error text, which can leak hidden rows."""

import csv
import io
from typing import Protocol

from data_agent.domain.sql_guard import SqlRejected, check_select

MAX_ROWS = 200
MAX_RESULT_CHARS = 16_000
VISIBLE_REGIONS = "SELECT DISTINCT region_id FROM dbo.fact_sales"


class QueryFailed(Exception):
    """A database failure. Deliberately carries no database text."""


class QueryError(QueryFailed):
    """Raised before any row is read (syntax, unknown names): its text names only what the query
    itself wrote, so the model may see it and fix the query."""


class SqlRunner(Protocol):
    async def query(self, sql: str, max_rows: int) -> tuple[list[str], list[tuple]]: ...


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
