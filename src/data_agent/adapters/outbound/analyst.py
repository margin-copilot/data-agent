"""Analyst port adapters: a Foundry model that answers through run_sql, and a fake."""

from typing import Annotated, Any, Literal

from agent_framework import Agent, tool
from margin_copilot_shared.contracts import AgentReply
from margin_copilot_shared.security.context import current_request
from pydantic import BaseModel, Field, ValidationError

from data_agent.adapters.outbound.instructions import instructions
from data_agent.application.run_sql import NoDatabaseAccess, SqlTool
from data_agent.domain.refusal import refusal_for, regions_named

NO_REGIONS = "You don't have access to any region's data."
NO_DATABASE_ACCESS = (
    "Your account doesn't have access to Riad Market's sales data. "
    "Sign in with an account that does."
)


class DataAnswer(BaseModel):
    """Facts the model reports; code turns them into the refusal label (domain/refusal.py)."""

    answer: str
    regions_asked: list[Literal["N", "C", "S"]]
    found_data: bool


class AnalystFailed(Exception):
    pass


class FoundryAnalyst:
    def __init__(self, client: Any, sql: SqlTool) -> None:
        self._client = client
        self._sql = sql

    async def answer(self, question: str) -> AgentReply:
        try:
            regions = await self._sql.visible_regions()
        except NoDatabaseAccess:  # retrying won't help: say so instead of "try again"
            return AgentReply(answer=NO_DATABASE_ACCESS, refusal="no_access")
        if not regions:
            return AgentReply(answer=NO_REGIONS, refusal="no_access")
        agent = Agent(
            client=self._client,
            instructions=instructions(regions),
            tools=[run_sql_tool(self._sql), margin_tool(self._sql)],
            default_options={"response_format": DataAnswer, "reasoning": {"effort": "medium"}},
        )
        response = await agent.run(question)
        try:
            result = response.value
        except ValidationError:
            result = None
        if result is None:
            raise AnalystFailed("the model gave no structured answer")
        asked = regions_named(question) or result.regions_asked
        refusal = refusal_for(asked, result.found_data, regions)
        return AgentReply(answer=result.answer, refusal=refusal)


def run_sql_tool(sql: SqlTool) -> Any:
    @tool(name="run_sql")
    async def run_sql(
        query: Annotated[str, Field(description="Exactly one T-SQL SELECT statement.")],
    ) -> str:
        """Run one read-only SELECT as the signed-in user. Returns CSV rows, "rejected: <why>"
        or "query failed"."""
        return await sql.run_sql(query)

    return run_sql


def margin_tool(sql: SqlTool) -> Any:
    @tool(name="margin")
    async def margin(
        start: Annotated[str, Field(description="First day, YYYY-MM-DD.")],
        end: Annotated[str, Field(description="Last day, YYYY-MM-DD (inclusive).")],
        region: Annotated[
            Literal["N", "C", "S"] | None, Field(description="One region, or null for all you see.")
        ] = None,
        category: Annotated[
            Literal["Bakery", "Beverages", "Dairy", "Grocery", "Household", "Produce"] | None,
            Field(description="One category, or null for all."),
        ] = None,
        sku: Annotated[str | None, Field(description="One product's sku, or null.")] = None,
    ) -> str:
        """Net sales, COGS and margin % (one decimal) for a period, optionally for one region,
        category or product. Use it for every margin figure."""
        return await sql.margin(start, end, region, category, sku)

    return margin


class FakeAnalyst:
    async def answer(self, question: str) -> AgentReply:
        return AgentReply(answer=f"fake data-agent answers as user {current_request().caller.oid}")
