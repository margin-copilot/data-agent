import httpx
from margin_copilot_shared.security.context import request_scope
from margin_copilot_shared.testing.telemetry import recorded_spans, texts_in
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware

from data_agent.application.run_sql import SqlTool
from data_agent.main import create_app
from tests.fakes import FakeRunner
from tests.test_azure_sql import USER, FakeDriver, runner


async def test_a_rejected_query_is_marked_and_not_recorded():
    with recorded_spans() as spans:
        await SqlTool(FakeRunner()).run_sql("SELECT * FROM rls.user_region")
    (ran,) = [s for s in spans if s.name == "data_agent.run_sql"]
    assert ran.attributes["margin.sql.rejected"] is True
    assert "rls.user_region" not in texts_in(spans)


async def test_rows_are_counted_never_recorded():
    tool = SqlTool(FakeRunner(columns=["region_id", "margin"], rows=[("N", 21.37)]))
    with recorded_spans() as spans:
        await tool.run_sql("SELECT region_id, margin FROM fact_sales")
    (ran,) = [s for s in spans if s.name == "data_agent.run_sql"]
    assert ran.attributes["margin.sql.rows"] == 1
    assert "21.37" not in texts_in(spans) and "SELECT" not in texts_in(spans)


async def test_connecting_is_its_own_span():
    sql_runner, _ = runner(FakeDriver())
    with recorded_spans() as spans, request_scope(USER):
        await sql_runner.query("SELECT region_id FROM fact_sales", 5)
    by_name = {s.name: s for s in spans}
    assert by_name["data_agent.query"].attributes["margin.sql.rows"] == 1
    assert (
        by_name["data_agent.connect"].parent.span_id == by_name["data_agent.query"].context.span_id
    )


async def test_the_app_is_wrapped_and_untraced_without_a_connection_string(monkeypatch):
    monkeypatch.setenv("ADAPTERS", "fake")
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    app = create_app()
    assert isinstance(app, OpenTelemetryMiddleware)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://d") as c:
        assert (await c.post("/", json={})).status_code == 401
