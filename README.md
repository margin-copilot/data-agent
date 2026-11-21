# Margin Copilot: data-agent

Margin Copilot data agent: answers questions about Riad Market's sales data with read-only SQL, run as the signed-in user.

Part of **Margin Copilot**, a secure multi-agent pricing assistant on Azure, built as a personal learning project.

**Start here:** [margin-copilot/docs](https://github.com/margin-copilot/docs) explains how the whole system works and how the repos fit together.

## Contract

- A2A JSON-RPC at `/` (`SendMessage`), agent card at `/.well-known/agent-card.json`.
- Every call except the card needs a bearer token addressed to this service, with scope `access_as_user`, from an app on `ALLOWED_CALLERS`. In practice that's the orchestrator's OBO token.
- The reply is one data part in the shared `AgentReply` contract: `{"answer": "...", "refusal": null}`, where `refusal` can also be `out_of_scope`, `no_access` or `no_data`. A failure is a JSON-RPC error with the fixed message `data-agent failed`; the cause goes only to the log.
- Skill `sales-data`: gpt-5-mini on Microsoft Foundry (reasoning effort medium), through Agent Framework, answers with two tools:
  - `margin(start, end, region?, category?, sku?)`: net sales, cost of goods and margin % from one tested query, with checked arguments bound as parameters. The model uses it for every margin figure.
  - `run_sql`: one SELECT, parsed with sqlglot and checked against the eight pricing tables (`domain/sql_guard.py`); impossible dates such as 2026-02-29 are rejected before running. So are averages and divisions of whole-number columns (`units`, `sales_area_m2`, `discount_pct`, `supplier_funded_pct`) without a cast: T-SQL computes them as integers and drops the fraction.
  - Both run on Azure SQL with the caller's OBO token, on their own connection (pooling off), with a 30-second timeout and at most 200 rows.
  - The model sees rows, the guard's reason, errors raised before any row is read (syntax, unknown names: they only name what the query wrote), or `query failed`. Never the text of errors raised while reading rows, which can reveal rows that row-level security hides.
- Before the model runs, one query as the user finds the regions row-level security lets them see. The model reports which regions the question is about and whether it found the figures asked for; `domain/refusal.py` turns those facts into the refusal label, the same way every time. `out_of_scope` waits for the orchestrator's scope rules (milestone 7).

## Configuration

Names in `.env.example`. `ADAPTERS=azure|fake` (no default). Azure mode needs every one of: `TENANT_ID`, `CLIENT_ID` (this app registration), `AZURE_CLIENT_ID` (its managed identity, which also calls the model), `ALLOWED_CALLERS`, `PUBLIC_URL` (the URL in the agent card), `SQL_SERVER`, `SQL_DATABASE`, `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_MODEL`.

## Run and test

```bash
uv sync
uv run pytest -q
ADAPTERS=fake uv run uvicorn --factory data_agent.main:create_app --port 8000
```

Fake mode answers `fake data-agent answers as user <oid>` with no model and no database. Pushing to `main` publishes `ghcr.io/margin-copilot/data-agent:sha-<commit>` after a check that the SQL driver loads in the image. Deployment: the private infra repo. How identity and data access work: [security model](https://github.com/margin-copilot/docs/blob/main/security-model.md).

## License

MIT. See [LICENSE](LICENSE).
