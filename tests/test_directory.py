import httpx
import pytest
from margin_copilot_shared.security.context import RequestContext, request_scope
from margin_copilot_shared.security.tokens import Caller

from data_agent.adapters.outbound.directory import GRAPH_SCOPES, GraphDirectory


class StubObo:
    def __init__(self):
        self.calls = []

    def exchange(self, user_token, scopes):
        self.calls.append((user_token, list(scopes)))
        return "graph-token"


def user_scope(token="user-token"):
    caller = Caller(oid="u", tid="t", azp="orch", scopes=frozenset({"access_as_user"}))
    return request_scope(RequestContext(caller=caller, token=token))


async def test_graph_is_called_with_the_obo_token_not_the_incoming_one():
    seen = {}

    def graph(request):
        seen["auth"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"displayName": "North Pricing Manager"})

    obo = StubObo()
    directory = GraphDirectory(obo, httpx.AsyncClient(transport=httpx.MockTransport(graph)))
    with user_scope():
        assert await directory.display_name() == "North Pricing Manager"
    assert obo.calls == [("user-token", GRAPH_SCOPES)]
    assert seen["auth"] == "Bearer graph-token"
    assert seen["url"].startswith("https://graph.microsoft.com/v1.0/me")


async def test_graph_error_is_raised():
    transport = httpx.MockTransport(lambda request: httpx.Response(401))
    directory = GraphDirectory(StubObo(), httpx.AsyncClient(transport=transport))
    with user_scope(), pytest.raises(httpx.HTTPStatusError):
        await directory.display_name()
