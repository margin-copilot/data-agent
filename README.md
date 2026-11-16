# Margin Copilot: data-agent

Margin Copilot data agent: read-only SQL over Fabric as the signed-in user.

Part of **Margin Copilot**, a secure multi-agent pricing assistant on Azure, built as a personal learning project.

**Start here:** [margin-copilot/docs](https://github.com/margin-copilot/docs) explains how the whole system works and how the repos fit together.

## Contract

- A2A JSON-RPC at `/` (`SendMessage`), agent card at `/.well-known/agent-card.json`.
- Every call except the card needs a bearer token addressed to this service, with scope `access_as_user`, from an app on `ALLOWED_CALLERS`. In practice that's the orchestrator's OBO token.
- Milestone 1 skill `whoami`: the tool exchanges the caller's token for a Microsoft Graph token (OBO) and replies `data-agent sees <display name>`.

## Configuration

Names in `.env.example`. `ADAPTERS=azure|fake` (no default), `TENANT_ID`, `CLIENT_ID` (this app registration), `AZURE_CLIENT_ID` (its managed identity), `ALLOWED_CALLERS`, `PUBLIC_URL` (the URL in the agent card).

## Run and test

```bash
uv sync
uv run pytest -q
ADAPTERS=fake uv run uvicorn --factory data_agent.main:create_app --port 8000
```

Pushing to `main` publishes `ghcr.io/margin-copilot/data-agent:sha-<commit>`. Deployment: the private infra repo. How the identity flow works: [security model](https://github.com/margin-copilot/docs/blob/main/security-model.md).

## License

MIT. See [LICENSE](LICENSE).
