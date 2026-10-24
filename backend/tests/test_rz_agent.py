import pytest

from app.services.rz_agent import RZAgentError, RZAgentService
from app.shopify.config import settings


@pytest.mark.asyncio
async def test_rz_provider_is_opt_in_by_default(monkeypatch):
    monkeypatch.setattr(settings, "rz_agent_enabled", False)
    with pytest.raises(RZAgentError, match="disabled"):
        await RZAgentService().chat_text([{"role": "user", "content": "Hello"}])


@pytest.mark.asyncio
async def test_rz_provider_requires_https(monkeypatch):
    monkeypatch.setattr(settings, "rz_agent_enabled", True)
    monkeypatch.setattr(settings, "rz_agent_base_url", "http://rz-agent.example")
    monkeypatch.setattr(settings, "rz_agent_api_key", "test-key")
    with pytest.raises(RZAgentError, match="HTTPS"):
        await RZAgentService().chat_text([{"role": "user", "content": "Hello"}])


@pytest.mark.asyncio
async def test_rz_provider_requires_url_and_key(monkeypatch):
    monkeypatch.setattr(settings, "rz_agent_enabled", True)
    monkeypatch.setattr(settings, "rz_agent_base_url", "")
    monkeypatch.setattr(settings, "rz_agent_api_key", "")
    with pytest.raises(RZAgentError, match="not fully configured"):
        await RZAgentService().chat_text([{"role": "user", "content": "Hello"}])
