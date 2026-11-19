"""The data agent's port: turn a question into a reply, as the signed-in user."""

from typing import Protocol

from margin_copilot_shared.contracts import AgentReply


class Analyst(Protocol):
    async def answer(self, question: str) -> AgentReply: ...
