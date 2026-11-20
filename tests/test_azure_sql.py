"""AzureSqlRunner against a fake driver: the token, the per-query connection, the error hiding."""

import struct
import sys
import types

import pytest
from margin_copilot_shared.security.context import RequestContext, request_scope
from margin_copilot_shared.security.tokens import Caller

from data_agent.adapters.outbound.azure_sql import (
    ACCESS_TOKEN,
    QUERY_TIMEOUT,
    SQL_SCOPES,
    AzureSqlRunner,
    default_connect,
)
from data_agent.application.run_sql import QueryError, QueryFailed

USER = RequestContext(Caller("north-mgr", "tid", "orchestrator", frozenset()), token="user-token")


class FakeObo:
    def __init__(self):
        self.calls = []

    def exchange(self, user_token, scopes):
        self.calls.append((user_token, list(scopes)))
        return "sql-token"


class FakeCursor:
    def __init__(self, connection, error=None):
        self.connection, self.error = connection, error
        self.description = [("region_id",), ("margin",)]

    def execute(self, sql, params=()):
        self.connection.executed.append((sql, params))
        if self.error:
            raise self.error

    def fetchmany(self, size):
        self.connection.fetched = size
        return [("N", 21.3)]


class FakeConnection:
    def __init__(self, query_error=None):
        self.query_error, self.executed, self.closed = query_error, [], False
        self.timeout, self.timeout_when_cursor_opened = 0, None

    def cursor(self):
        self.timeout_when_cursor_opened = self.timeout
        return FakeCursor(self, self.query_error)

    def close(self):
        self.closed = True


class FakeDriver:
    def __init__(self, connect_errors=(), query_error=None):
        self.connect_errors, self.query_error = list(connect_errors), query_error
        self.connects, self.connections = [], []

    def connect(self, dsn, attrs_before, timeout):
        self.connects.append((dsn, attrs_before, timeout))
        if self.connect_errors:
            raise self.connect_errors.pop(0)
        self.connections.append(FakeConnection(self.query_error))
        return self.connections[-1]


def runner(driver, sleeps=None):
    sleep = sleeps.append if sleeps is not None else (lambda _: None)
    obo = FakeObo()
    return AzureSqlRunner("srv.database.windows.net", "riad", obo, driver.connect, sleep), obo


async def test_each_query_runs_on_its_own_connection_with_the_users_obo_token():
    driver = FakeDriver()
    sql, obo = runner(driver)
    with request_scope(USER):
        assert await sql.query("SELECT 1", 201) == (["region_id", "margin"], [("N", 21.3)])
        await sql.query("SELECT 2", 201)
    assert obo.calls == [("user-token", SQL_SCOPES)] * 2
    dsn, attrs, _ = driver.connects[0]
    assert dsn == "Server=tcp:srv.database.windows.net,1433;Database=riad;Encrypt=yes"
    raw = "sql-token".encode("utf-16-le")
    assert attrs == {ACCESS_TOKEN: struct.pack(f"<I{len(raw)}s", len(raw), raw)}
    assert len(driver.connections) == 2
    assert all(c.closed for c in driver.connections)
    assert driver.connections[0].timeout_when_cursor_opened == QUERY_TIMEOUT
    assert driver.connections[0].fetched == 201


async def test_query_errors_become_query_failed_without_their_text():
    driver = FakeDriver(query_error=RuntimeError("Divide by zero error encountered. (8134)"))
    sql, _ = runner(driver)
    with request_scope(USER), pytest.raises(QueryFailed) as failed:
        await sql.query("SELECT 1/0", 201)
    assert str(failed.value) == ""
    assert failed.value.__cause__ is None and failed.value.__context__ is not None
    assert driver.connections[0].closed


@pytest.mark.parametrize(
    "message",
    [
        "Database is not currently available (40613)",
        # mssql-python 1.17.0's text for 40613 carries no error number (seen live, milestone 4).
        "Driver Error: General error; DDBC Error: [Microsoft][SQL Server]Database 'riad' on server "
        "'srv.database.windows.net' is not currently available.  Please retry the connection.",
    ],
)
async def test_a_waking_database_is_retried(message):
    driver = FakeDriver(connect_errors=[RuntimeError(message)])
    sleeps = []
    sql, _ = runner(driver, sleeps)
    with request_scope(USER):
        await sql.query("SELECT 1", 201)
    assert sleeps == [5]
    assert len(driver.connects) == 2


async def test_other_connect_errors_fail_at_once():
    driver = FakeDriver(connect_errors=[RuntimeError("Client IP not allowed (40615)")])
    sleeps = []
    sql, _ = runner(driver, sleeps)
    with request_scope(USER), pytest.raises(QueryFailed):
        await sql.query("SELECT 1", 201)
    assert sleeps == []


def test_the_real_driver_is_used_with_pooling_off(monkeypatch):
    calls = []
    fake = types.SimpleNamespace(pooling=lambda **kw: calls.append(kw), connect=object())
    monkeypatch.setitem(sys.modules, "mssql_python", fake)
    assert default_connect() is fake.connect
    assert calls == [{"enabled": False}]


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        (
            "Driver Error: x; DDBC Error: [Microsoft][SQL Server]Invalid column name 'regionid'.",
            "Invalid column name 'regionid'.",
        ),
        (
            "Driver Error: x; DDBC Error: [Microsoft][SQL Server]Incorrect syntax near 'FORM'.",
            "Incorrect syntax near 'FORM'.",
        ),
        (
            "Driver Error: x; DDBC Error: [Microsoft][SQL Server]Column 'dbo.fact_sales.sku' is "
            "invalid in the select list because it is not contained in either an aggregate "
            "function or the GROUP BY clause.",
            "Column 'dbo.fact_sales.sku' is invalid in the select list because it is "
            "not contained in either an aggregate function or the GROUP BY clause.",
        ),
    ],
)
async def test_errors_before_any_row_is_read_go_back_to_the_model(message, reason):
    driver = FakeDriver(query_error=RuntimeError(message))
    sql, _ = runner(driver)
    with request_scope(USER), pytest.raises(QueryError) as error:
        await sql.query("SELECT regionid FROM fact_sales", 201)
    assert str(error.value) == reason


@pytest.mark.parametrize(
    "message",
    [
        "Driver Error: x; DDBC Error: [Microsoft][SQL Server]Divide by zero error encountered.",
        "Driver Error: x; DDBC Error: [Microsoft][SQL Server]Conversion failed when converting the "
        "varchar value 'secret' to data type int.",
        # Row values crafted to look like a compile error must not pass as one.
        "Driver Error: x; DDBC Error: [Microsoft][SQL Server]Conversion failed when converting the "
        "varchar value 'S|2072.00 could not be bound' to data type int.",
        "Driver Error: x; DDBC Error: [Microsoft][SQL Server]Conversion failed when converting the "
        "varchar value 'Invalid column name 'x'.' to data type int.",
    ],
)
async def test_errors_while_reading_rows_stay_silent(message):
    driver = FakeDriver(query_error=RuntimeError(message))
    sql, _ = runner(driver)
    with request_scope(USER), pytest.raises(QueryFailed) as failed:
        await sql.query("SELECT 1/0 FROM fact_sales", 201)
    assert not isinstance(failed.value, QueryError) and str(failed.value) == ""


async def test_parameters_are_bound_by_the_driver_not_spliced_into_the_sql():
    driver = FakeDriver()
    sql, _ = runner(driver)
    with request_scope(USER):
        await sql.query("SELECT 1 FROM dbo.fact_sales WHERE region_id = ?", 201, ("N",))
    assert driver.connections[0].executed == [
        ("SELECT 1 FROM dbo.fact_sales WHERE region_id = ?", ("N",))
    ]
