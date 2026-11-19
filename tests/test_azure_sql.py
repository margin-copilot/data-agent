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
from data_agent.application.run_sql import QueryFailed

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

    def execute(self, sql):
        self.connection.executed.append(sql)
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
