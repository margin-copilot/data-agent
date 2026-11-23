"""SqlRunner on Azure SQL. Each query exchanges the caller's token (OBO) and opens its own
connection, with pooling off, so no connection is ever shared across users; then it closes it."""

import asyncio
import logging
import re
import struct
import time
from collections.abc import Callable
from typing import Any

from margin_copilot_shared.security.context import current_request
from margin_copilot_shared.security.obo import OboClient
from margin_copilot_shared.telemetry import span

from data_agent.application.run_sql import NoDatabaseAccess, QueryError, QueryFailed

SQL_SCOPES = ["https://database.windows.net/.default"]
ACCESS_TOKEN = 1256  # SQL_COPT_SS_ACCESS_TOKEN
# The free-offer database is resuming from auto-pause (40613). mssql-python 1.17.0 reports it
# without the number, so the message text counts too.
WAKING = ("40613", "is not currently available")
WAKE_DELAYS = (5, 10, 20, 30)
LOGIN_FAILED = "Login failed for user"  # 18456: the user's account isn't a database user
LOGIN_TIMEOUT = 30
# Whole messages SQL Server raises while compiling, before any row is read: they quote only what
# the query wrote. Anything else (divide by zero, conversions) can carry or reveal row values, so
# it stays "query failed"; matching whole messages keeps crafted row values from passing as one.
COMPILE_ERRORS = [
    re.compile(pattern)
    for pattern in (
        r"Invalid column name '[^']*'\.",
        r"Invalid object name '[^']*'\.",
        r"Incorrect syntax near (the keyword )?'[^']*'\.",
        r"Ambiguous column name '[^']*'\.",
        r'The multi-part identifier "[^"]*" could not be bound\.',
        r"Column '[^']*' is invalid in the select list because it is not contained in either an "
        r"aggregate function or the GROUP BY clause\.",
        r"'[^']*' is not a recognized built-in function name\.",
    )
]
QUERY_TIMEOUT = 30
log = logging.getLogger(__name__)


def pack_token(token: str) -> bytes:
    raw = token.encode("utf-16-le")
    return struct.pack(f"<I{len(raw)}s", len(raw), raw)


def compile_error(text: str) -> str | None:
    message = text.split("[SQL Server]")[-1].strip()
    return message if any(pattern.fullmatch(message) for pattern in COMPILE_ERRORS) else None


def default_connect() -> Callable[..., Any]:
    import mssql_python  # native driver: only azure mode loads it

    mssql_python.pooling(enabled=False)
    return mssql_python.connect


class AzureSqlRunner:
    def __init__(
        self,
        server: str,
        database: str,
        obo: OboClient,
        connect: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._dsn = f"Server=tcp:{server},1433;Database={database};Encrypt=yes"
        self._obo = obo
        self._connect = connect or default_connect()
        self._sleep = sleep

    async def query(
        self, sql: str, max_rows: int, params: tuple = ()
    ) -> tuple[list[str], list[tuple]]:
        token = current_request().token
        with span("data_agent.query") as queried:
            try:
                columns, rows = await asyncio.to_thread(self._query, token, sql, max_rows, params)
            except QueryFailed as error:  # raised below: a compile error's text can echo the SQL
                queried.set_attribute("margin.sql.failed", type(error).__name__)
                failure = error
            else:
                queried.set_attribute("margin.sql.rows", len(rows))
                return columns, rows
        raise failure

    def _query(
        self, user_token: str, sql: str, max_rows: int, params: tuple
    ) -> tuple[list[str], list[tuple]]:
        connection = self._open(self._obo.exchange(user_token, SQL_SCOPES))
        try:
            connection.timeout = QUERY_TIMEOUT  # before cursor(): the cursor copies it
            cursor = connection.cursor()
            if params:
                cursor.execute(sql, params)  # bound by the driver, never spliced into the text
            else:
                cursor.execute(sql)
            columns = [column[0] for column in cursor.description]
            return columns, [tuple(row) for row in cursor.fetchmany(max_rows)]
        except Exception as error:
            if reason := compile_error(str(error)):
                raise QueryError(reason) from None
            log.warning("query failed: %s", type(error).__name__)  # the text can carry row data
            raise QueryFailed from None
        finally:
            connection.close()

    def _open(self, token: str) -> Any:
        attrs = {ACCESS_TOKEN: pack_token(token)}
        with span("data_agent.connect"):
            for delay in (*WAKE_DELAYS, None):
                try:
                    return self._connect(self._dsn, attrs_before=attrs, timeout=LOGIN_TIMEOUT)
                except Exception as error:
                    if LOGIN_FAILED in str(error):
                        log.warning("connect failed: no database login for this user")
                        raise NoDatabaseAccess from None
                    if delay is None or not any(sign in str(error) for sign in WAKING):
                        log.warning("connect failed: %s: %.300s", type(error).__name__, error)
                        raise QueryFailed from None
                    self._sleep(delay)
