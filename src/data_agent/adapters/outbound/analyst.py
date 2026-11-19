"""Analyst port adapters: a Foundry model that answers through run_sql, and a fake."""

from typing import Annotated, Any, Literal

from agent_framework import Agent, tool
from margin_copilot_shared.contracts import AgentReply
from margin_copilot_shared.security.context import current_request
from pydantic import BaseModel, Field, ValidationError

from data_agent.adapters.outbound.instructions import instructions
from data_agent.application.run_sql import SqlTool

NO_REGIONS = "You don't have access to any region's data."


class DataAnswer(BaseModel):
    answer: str
    refusal: Literal["out_of_scope", "no_access", "no_data"] | None


class AnalystFailed(Exception):
    pass


class FoundryAnalyst:
    def __init__(self, client: Any, sql: SqlTool) -> None:
        self._client = client
        self._sql = sql

    async def answer(self, question: str) -> AgentReply:
        regions = await self._sql.visible_regions()
        if not regions:
            return AgentReply(answer=NO_REGIONS, refusal="no_access")
        agent = Agent(
            client=self._client,
            instructions=instructions(regions),
            tools=[run_sql_tool(self._sql)],
            default_options={"response_format": DataAnswer, "reasoning": {"effort": "low"}},
        )
        response = await agent.run(question)
        try:
            result = response.value
        except ValidationError:
            result = None
        if result is None:
            raise AnalystFailed("the model gave no structured answer")
        return AgentReply(answer=result.answer, refusal=result.refusal)


def run_sql_tool(sql: SqlTool) -> Any:
    @tool(name="run_sql")
    async def run_sql(
        query: Annotated[str, Field(description="Exactly one T-SQL SELECT statement.")],
    ) -> str:
        """Run one read-only SELECT as the signed-in user. Returns CSV rows, "rejected: <why>"
        or "query failed"."""
        return await sql.run_sql(query)

    return run_sql


class FakeAnalyst:
    async def answer(self, question: str) -> AgentReply:
        return AgentReply(answer=f"fake data-agent answers as user {current_request().caller.oid}")
