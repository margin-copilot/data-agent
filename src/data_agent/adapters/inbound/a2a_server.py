"""A2A server for the data agent, behind the shared token middleware. Replies are one data part
in the shared AgentReply contract."""

import logging
from collections.abc import Awaitable, Callable

from a2a.helpers import new_data_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from margin_copilot_shared.security.middleware import AuthMiddleware
from margin_copilot_shared.security.tokens import TokenValidator
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from data_agent.application.analyst import Analyst

log = logging.getLogger(__name__)


class DataAgentFailed(Exception):
    def __init__(self) -> None:
        super().__init__("data-agent failed")  # a2a-sdk sends str(exception) to the caller


class DataAgentExecutor(AgentExecutor):
    def __init__(self, analyst: Analyst) -> None:
        self._analyst = analyst

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            reply = await self._analyst.answer(context.get_user_input())
        except Exception as error:
            log.warning("answer failed: %s", type(error).__name__)
            raise DataAgentFailed from None
        await event_queue.enqueue_event(new_data_message(reply.model_dump()))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("data-agent replies in one message; nothing to cancel")


def agent_card(public_url: str) -> AgentCard:
    return AgentCard(
        name="data-agent",
        description="Answers questions from Riad Market sales data as the signed-in user.",
        version="0.2.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=public_url, protocol_version="1.0")
        ],
        skills=[
            AgentSkill(
                id="sales-data",
                name="Sales data",
                description="Sales, costs, prices, promotions and competitor prices, read with "
                "one checked SELECT per query as the signed-in user.",
                tags=["sql", "pricing"],
            )
        ],
    )


def create_a2a_app(
    analyst: Analyst,
    validator: TokenValidator,
    public_url: str,
    warm: Callable[[], Awaitable[object]] | None = None,
) -> Starlette:
    """`warm` (POST /warm, D-044): a cheap query as the caller that resumes a paused database."""

    async def warm_up(request: Request) -> Response:
        if warm is not None:
            try:
                await warm()
            except Exception as error:  # best effort: the caller gets nothing either way
                log.warning("warm failed: %s", type(error).__name__)
        return Response(status_code=204)

    card = agent_card(public_url)
    handler = DefaultRequestHandler(
        agent_executor=DataAgentExecutor(analyst),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    return Starlette(
        routes=[
            *create_agent_card_routes(card),
            Route("/warm", warm_up, methods=["POST"]),
            *create_jsonrpc_routes(handler, "/"),
        ],
        middleware=[
            Middleware(
                AuthMiddleware,
                validator=validator,
                public_paths=frozenset({AGENT_CARD_WELL_KNOWN_PATH}),
            )
        ],
    )
