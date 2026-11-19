"""Composition root. ADAPTERS=azure|fake picks the adapters; there is no default."""

import os

from agent_framework.foundry import FoundryChatClient
from azure.identity.aio import ManagedIdentityCredential
from margin_copilot_shared.security.obo import OboClient, managed_identity_assertion
from margin_copilot_shared.security.tokens import entra_validator
from margin_copilot_shared.testing.issuer import LocalIssuer
from starlette.applications import Starlette

from data_agent.adapters.inbound.a2a_server import create_a2a_app
from data_agent.adapters.outbound.analyst import FakeAnalyst, FoundryAnalyst
from data_agent.adapters.outbound.azure_sql import AzureSqlRunner
from data_agent.application.run_sql import SqlTool

TOOL_LIMITS = {"max_iterations": 8, "max_function_calls": 8}
MODEL_TIMEOUT = 60


def create_app() -> Starlette:
    env = os.environ
    adapters = env["ADAPTERS"]
    if adapters == "azure":
        tenant_id, client_id, identity = env["TENANT_ID"], env["CLIENT_ID"], env["AZURE_CLIENT_ID"]
        callers = frozenset(env["ALLOWED_CALLERS"].split(","))
        obo = OboClient(tenant_id, client_id, managed_identity_assertion(identity))
        sql = SqlTool(AzureSqlRunner(env["SQL_SERVER"], env["SQL_DATABASE"], obo))
        model = FoundryChatClient(
            project_endpoint=env["FOUNDRY_PROJECT_ENDPOINT"],
            model=env["FOUNDRY_MODEL"],
            credential=ManagedIdentityCredential(client_id=identity),
            function_invocation_configuration=TOOL_LIMITS,
            timeout=MODEL_TIMEOUT,
        )
        validator = entra_validator(tenant_id, client_id, callers)
        return create_a2a_app(FoundryAnalyst(model, sql), validator, env["PUBLIC_URL"])
    if adapters == "fake":
        # A fresh in-memory key per process: nobody outside this process can mint a token for it.
        validator = LocalIssuer().validator(
            audience="data-agent", allowed_callers=frozenset({"orchestrator"})
        )
        public_url = env.get("PUBLIC_URL", "http://localhost:8000/")
        return create_a2a_app(FakeAnalyst(), validator, public_url)
    raise ValueError(f"ADAPTERS must be azure or fake, not {adapters!r}")
