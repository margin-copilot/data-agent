"""SqlRunner on Azure SQL. Each query exchanges the caller's token (OBO) and opens its own
connection, with pooling off, so no connection is ever shared across users; then it closes it."""

import asyncio
import logging
import struct
import time
from collections.abc import Callable
from typing import Any

from margin_copilot_shared.security.context import current_request
from margin_copilot_shared.security.obo import OboClient

from data_agent.application.run_sql import QueryError, QueryFailed

SQL_SCOPES = ["https://database.windows.net/.default"]
ACCESS_TOKEN = 1256  # SQL_COPT_SS_ACCESS_TOKEN
# The free-offer database is resuming from auto-pause (40613). mssql-python 1.17.0 reports it
# without the number, so the message text counts too.
WAKING = ("40613", "is not currently available")
WAKE_DELAYS = (5, 10, 20, 30)
LOGIN_TIMEOUT = 30
# Raised while compiling, before any row is read. Errors while reading rows (divide by zero,
# conversions) can carry or reveal row values, so they stay "query failed".
COMPILE_ERRORS = (
    "Invalid column name",
    "Invalid object name",
    "Incorrect syntax near",
    "Ambiguous column name",
    "could not be bound",
    "is invalid in the select list",
    "is not a recognized built-in function name",
)
QUERY_TIMEOUT = 30
log = logging.getLogger(__name__)


def pack_token(token: str) -> bytes:
    raw = token.encode("utf-16-le")
    return struct.pack(f"<I{len(raw)}s", len(raw), raw)


def compile_error(text: str) -> str | None:
    message = text.rsplit("]", 1)[-1].strip()
    return message[:300] if any(sign in message for sign in COMPILE_ERRORS) else None


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

    async def query(self, sql: str, max_rows: int) -> tuple[list[str], list[tuple]]:
        return await asyncio.to_thread(self._query, current_request().token, sql, max_rows)

    def _query(self, user_token: str, sql: str, max_rows: int) -> tuple[list[str], list[tuple]]:
        connection = self._open(self._obo.exchange(user_token, SQL_SCOPES))
        try:
            connection.timeout = QUERY_TIMEOUT  # before cursor(): the cursor copies it
            cursor = connection.cursor()
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
        for delay in (*WAKE_DELAYS, None):
            try:
                return self._connect(self._dsn, attrs_before=attrs, timeout=LOGIN_TIMEOUT)
            except Exception as error:
                if delay is None or not any(sign in str(error) for sign in WAKING):
                    log.warning("connect failed: %s: %.300s", type(error).__name__, error)
                    raise QueryFailed from None
                self._sleep(delay)
