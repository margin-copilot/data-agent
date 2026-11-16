"""The A2A server in one process: real a2a-sdk client, real tokens, the tool reads the caller."""

import asyncio

import httpx
import pytest
from a2a.client import ClientCallContext, ClientConfig, create_client
from a2a.client.errors import A2AClientError
from a2a.helpers import get_message_text, new_text_message
from a2a.types import Role, SendMessageRequest
from margin_copilot_shared.testing.issuer import LocalIssuer

from data_agent.adapters.inbound.a2a_server import create_a2a_app
from data_agent.adapters.outbound.directory import FakeDirectory

DATA_AGENT = "data-agent-client-id"
ORCHESTRATOR = "orchestrator-client-id"
URL = "http://data-agent/"


@pytest.fixture
def issuer():
    return LocalIssuer()


@pytest.fixture
def http(issuer):
    validator = issuer.validator(audience=DATA_AGENT, allowed_callers=frozenset({ORCHESTRATOR}))
    app = create_a2a_app(FakeDirectory(), validator, URL)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app))


async def send(http, token):
    config = ClientConfig(httpx_client=http, streaming=False)
    client = await create_client(URL, client_config=config)
    request = SendMessageRequest(message=new_text_message("who am I?", role=Role.ROLE_USER))
    context = ClientCallContext(service_parameters={"Authorization": f"Bearer {token}"})
    async for event in client.send_message(request, context=context):
        if event.WhichOneof("payload") == "message":
            return get_message_text(event.message)
    return None


async def test_tool_sees_the_caller_from_the_token(issuer, http):
    token = issuer.issue(audience=DATA_AGENT, azp=ORCHESTRATOR, oid="north-mgr")
    assert await send(http, token) == "data-agent sees user north-mgr"


async def test_concurrent_callers_each_see_themselves(issuer, http):
    oids = [f"user-{n}" for n in range(10)]
    tokens = [issuer.issue(audience=DATA_AGENT, azp=ORCHESTRATOR, oid=oid) for oid in oids]
    replies = await asyncio.gather(*(send(http, token) for token in tokens))
    assert replies == [f"data-agent sees user {oid}" for oid in oids]


async def test_token_from_an_unlisted_caller_is_403(issuer, http):
    token = issuer.issue(audience=DATA_AGENT, azp="cli-client-id")
    with pytest.raises(A2AClientError, match="403"):
        await send(http, token)


async def test_token_for_another_service_is_401(issuer, http):
    token = issuer.issue(audience=ORCHESTRATOR, azp=ORCHESTRATOR)
    with pytest.raises(A2AClientError, match="401"):
        await send(http, token)


async def test_agent_card_needs_no_token(http):
    response = await http.get(f"{URL}.well-known/agent-card.json")
    assert response.status_code == 200
    assert response.json()["name"] == "data-agent"
