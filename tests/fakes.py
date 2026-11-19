"""Test doubles: a scripted SQL runner and a scripted model (Agent Framework chat client)."""

from collections.abc import Sequence
from typing import Any

from agent_framework import BaseChatClient, ChatResponse, FunctionInvocationLayer, Message

from data_agent.application.run_sql import VISIBLE_REGIONS, QueryFailed


class FakeRunner:
    def __init__(self, regions=("N",), columns=("n",), rows=((1,),), fail=False) -> None:
        self.regions, self.columns, self.rows, self.fail = regions, list(columns), list(rows), fail
        self.queries: list[tuple[str, int]] = []

    async def query(self, sql: str, max_rows: int) -> tuple[list[str], list[tuple]]:
        self.queries.append((sql, max_rows))
        if sql == VISIBLE_REGIONS:
            return ["region_id"], [(region,) for region in self.regions]
        if self.fail:
            raise QueryFailed
        return self.columns, self.rows[:max_rows]


class ScriptedChatClient(FunctionInvocationLayer, BaseChatClient):
    """Replays scripted model turns and records what the model was sent. `_inner_get_response` is
    Agent Framework's private extension point (1.26.0); re-check it when upgrading."""

    def __init__(self, turns: Sequence[Message], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.turns, self.calls = list(turns), []

    async def _inner_get_response(self, *, messages, stream, options, **kwargs) -> ChatResponse:
        self.calls.append((list(messages), dict(options)))
        return ChatResponse(messages=[self.turns.pop(0)])
