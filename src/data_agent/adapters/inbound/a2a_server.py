"""A2A server for the data agent, behind the shared token middleware."""

from a2a.helpers import new_text_message
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

from data_agent.application.whoami import Directory, whoami


class DataAgentExecutor(AgentExecutor):
    def __init__(self, directory: Directory) -> None:
        self._directory = directory

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        await event_queue.enqueue_event(new_text_message(await whoami(self._directory)))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("data-agent replies in one message; nothing to cancel")


def agent_card(public_url: str) -> AgentCard:
    return AgentCard(
        name="data-agent",
        description="Answers questions from Riad Market sales data as the signed-in user.",
        version="0.1.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=public_url, protocol_version="1.0")
        ],
        skills=[
            AgentSkill(
                id="whoami",
                name="Who am I",
                description="Reports the signed-in user as the directory sees them.",
                tags=["identity"],
            )
        ],
    )


def create_a2a_app(directory: Directory, validator: TokenValidator, public_url: str) -> Starlette:
    card = agent_card(public_url)
    handler = DefaultRequestHandler(
        agent_executor=DataAgentExecutor(directory),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    return Starlette(
        routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/")],
        middleware=[
            Middleware(
                AuthMiddleware,
                validator=validator,
                public_paths=frozenset({AGENT_CARD_WELL_KNOWN_PATH}),
            )
        ],
    )
