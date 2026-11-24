"""The A2A server in one process: real a2a-sdk client, real tokens, the shared reply contract."""

import asyncio

import httpx
import pytest
from a2a.client import ClientCallContext, ClientConfig, create_client
from a2a.client.errors import A2AClientError
from a2a.helpers import get_data_parts, new_text_message
from a2a.types import Role, SendMessageRequest
from a2a.utils.errors import A2AError
from margin_copilot_shared.testing.issuer import LocalIssuer

from data_agent.adapters.inbound.a2a_server import create_a2a_app
from data_agent.adapters.outbound.analyst import FakeAnalyst
from data_agent.application.run_sql import VISIBLE_REGIONS, NoDatabaseAccess, SqlTool

DATA_AGENT = "data-agent-client-id"
ORCHESTRATOR = "orchestrator-client-id"
URL = "http://data-agent/"


@pytest.fixture
def issuer():
    return LocalIssuer()


class BrokenAnalyst:
    async def answer(self, question):
        raise RuntimeError("Divide by zero error encountered. (8134)")


def serve(issuer, analyst):
    validator = issuer.validator(audience=DATA_AGENT, allowed_callers=frozenset({ORCHESTRATOR}))
    app = create_a2a_app(analyst, validator, URL)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app))


@pytest.fixture
def http(issuer):
    return serve(issuer, FakeAnalyst())


async def send(http, token):
    config = ClientConfig(httpx_client=http, streaming=False)
    client = await create_client(URL, client_config=config)
    request = SendMessageRequest(message=new_text_message("Dairy margin?", role=Role.ROLE_USER))
    context = ClientCallContext(service_parameters={"Authorization": f"Bearer {token}"})
    async for event in client.send_message(request, context=context):
        if event.WhichOneof("payload") == "message":
            return get_data_parts(event.message.parts)
    return None


def reply(oid):
    return [{"answer": f"fake data-agent answers as user {oid}", "refusal": None, "citations": []}]


async def test_the_reply_is_one_data_part_in_the_shared_contract(issuer, http):
    token = issuer.issue(audience=DATA_AGENT, azp=ORCHESTRATOR, oid="north-mgr")
    assert await send(http, token) == reply("north-mgr")


async def test_a_failure_reaches_the_caller_as_a_fixed_message(issuer):
    token = issuer.issue(audience=DATA_AGENT, azp=ORCHESTRATOR, oid="north-mgr")
    with pytest.raises(A2AError) as failed:
        await send(serve(issuer, BrokenAnalyst()), token)
    assert "data-agent failed" in str(failed.value)
    assert "8134" not in str(failed.value)


async def test_concurrent_callers_each_see_themselves(issuer, http):
    oids = [f"user-{n}" for n in range(10)]
    tokens = [issuer.issue(audience=DATA_AGENT, azp=ORCHESTRATOR, oid=oid) for oid in oids]
    replies = await asyncio.gather(*(send(http, token) for token in tokens))
    assert replies == [reply(oid) for oid in oids]


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


class RegionsRunner:
    def __init__(self, error=None):
        self.error, self.queries = error, []

    async def query(self, sql, max_rows, params=()):
        self.queries.append(sql)
        if self.error:
            raise self.error
        return ["region_id"], [("N",)]


def serve_warm(issuer, runner):
    validator = issuer.validator(audience=DATA_AGENT, allowed_callers=frozenset({ORCHESTRATOR}))
    app = create_a2a_app(FakeAnalyst(), validator, URL, warm=SqlTool(runner).visible_regions)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=URL)


async def test_warm_runs_the_regions_query_as_the_caller(issuer):
    runner = RegionsRunner()
    token = issuer.issue(audience=DATA_AGENT, azp=ORCHESTRATOR, oid="north-mgr")
    async with serve_warm(issuer, runner) as http:
        response = await http.post("/warm", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 204 and runner.queries == [VISIBLE_REGIONS]


async def test_warm_without_database_access_is_quiet(issuer, caplog):
    token = issuer.issue(audience=DATA_AGENT, azp=ORCHESTRATOR, oid="personal")
    async with serve_warm(issuer, RegionsRunner(NoDatabaseAccess())) as http:
        response = await http.post("/warm", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 204 and "warm failed: NoDatabaseAccess" in caplog.text


async def test_warm_needs_a_token(issuer):
    async with serve_warm(issuer, RegionsRunner()) as http:
        assert (await http.post("/warm")).status_code == 401
