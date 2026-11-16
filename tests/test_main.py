import httpx
import pytest

from data_agent.main import create_app


async def test_fake_mode_serves_the_card_and_requires_tokens(monkeypatch):
    monkeypatch.setenv("ADAPTERS", "fake")
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://d") as c:
        assert (await c.get("/.well-known/agent-card.json")).status_code == 200
        assert (await c.post("/", json={})).status_code == 401


def test_adapters_must_be_set_explicitly(monkeypatch):
    monkeypatch.delenv("ADAPTERS", raising=False)
    with pytest.raises(KeyError):
        create_app()
    monkeypatch.setenv("ADAPTERS", "prod")
    with pytest.raises(ValueError, match="azure or fake"):
        create_app()
