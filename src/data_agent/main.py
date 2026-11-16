"""Composition root. ADAPTERS=azure|fake picks the adapters; there is no default."""

import os

import httpx
from margin_copilot_shared.security.obo import OboClient, managed_identity_assertion
from margin_copilot_shared.security.tokens import entra_validator
from margin_copilot_shared.testing.issuer import LocalIssuer
from starlette.applications import Starlette

from data_agent.adapters.inbound.a2a_server import create_a2a_app
from data_agent.adapters.outbound.directory import FakeDirectory, GraphDirectory


def create_app() -> Starlette:
    env = os.environ
    adapters = env["ADAPTERS"]
    public_url = env.get("PUBLIC_URL", "http://localhost:8000/")
    if adapters == "azure":
        tenant_id, client_id = env["TENANT_ID"], env["CLIENT_ID"]
        callers = frozenset(env["ALLOWED_CALLERS"].split(","))
        obo = OboClient(tenant_id, client_id, managed_identity_assertion(env["AZURE_CLIENT_ID"]))
        directory = GraphDirectory(obo, httpx.AsyncClient(timeout=10))
        return create_a2a_app(directory, entra_validator(tenant_id, client_id, callers), public_url)
    if adapters == "fake":
        # A fresh in-memory key per process: nobody outside this process can mint a token for it.
        validator = LocalIssuer().validator(
            audience="data-agent", allowed_callers=frozenset({"orchestrator"})
        )
        return create_a2a_app(FakeDirectory(), validator, public_url)
    raise ValueError(f"ADAPTERS must be azure or fake, not {adapters!r}")
