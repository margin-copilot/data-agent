"""Directory port adapters: Microsoft Graph /me on the caller's OBO token, and a fake."""

import asyncio

import httpx
from margin_copilot_shared.security.context import current_request
from margin_copilot_shared.security.obo import OboClient

GRAPH_ME = "https://graph.microsoft.com/v1.0/me?$select=displayName"
GRAPH_SCOPES = ["https://graph.microsoft.com/User.Read"]


class GraphDirectory:
    def __init__(self, obo: OboClient, http: httpx.AsyncClient) -> None:
        self._obo = obo
        self._http = http

    async def display_name(self) -> str:
        token = await asyncio.to_thread(self._obo.exchange, current_request().token, GRAPH_SCOPES)
        response = await self._http.get(GRAPH_ME, headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        return str(response.json()["displayName"])


class FakeDirectory:
    async def display_name(self) -> str:
        return f"user {current_request().caller.oid}"
