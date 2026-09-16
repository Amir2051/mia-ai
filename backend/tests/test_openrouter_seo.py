import json

import pytest

from app.services.openrouter import OpenRouterError, OpenRouterService
from app.services.seo import SEOResult, SEOService, sanitize_html


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_openrouter_missing_key(monkeypatch):
    from app.shopify.config import settings
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    with pytest.raises(OpenRouterError, match="not configured"):
        await OpenRouterService().list_models()


@pytest.mark.asyncio
async def test_openrouter_timeout_retries(monkeypatch):
    import httpx
    from app.shopify.config import settings
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    calls = {"n": 0}

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def request(self, *args, **kwargs):
            calls["n"] += 1
            raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    with pytest.raises(OpenRouterError, match="request failed"):
        await OpenRouterService().list_models()
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_openrouter_api_failure(monkeypatch):
    from app.shopify.config import settings
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    service = OpenRouterService()
    async def fake_request(*args, **kwargs): return FakeResponse(503, {"error": "upstream"})
    monkeypatch.setattr(service, "_request", fake_request)
    with pytest.raises(OpenRouterError, match="HTTP 503"):
        await service.list_models()


@pytest.mark.asyncio
async def test_malformed_ai_json(monkeypatch):
    from app.shopify.config import settings
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    service = OpenRouterService()
    async def fake_request(*args, **kwargs):
        return FakeResponse(200, {"choices": [{"message": {"content": "not json"}}]})
    monkeypatch.setattr(service, "_request", fake_request)
    with pytest.raises(OpenRouterError, match="malformed JSON"):
        await service.chat_json([{"role": "user", "content": "test"}], model="test/model")


def test_schema_validation_and_html_sanitization():
    clean = sanitize_html('<p>Hello</p><script>alert(1)</script><a href="javascript:bad">x</a><strong>World</strong>')
    assert "script" not in clean
    assert "javascript" not in clean
    assert "<strong>World</strong>" in clean
    with pytest.raises(Exception):
        SEOResult.model_validate({"seo_score": 101})


@pytest.mark.asyncio
async def test_seo_generation_validates_result(monkeypatch):
    class FakeAI:
        async def chat_json(self, messages, model=None):
            return ({
                "seo_title": "Great Product | Store",
                "meta_description": "A useful product for shoppers looking for this category.",
                "optimized_title": "Great Product",
                "optimized_description_html": "<p>Useful <strong>product</strong> details.</p><script>x</script>",
                "keywords": ["great product"], "tags": ["featured"],
                "handle_suggestion": "great-product", "image_alt_text": ["Great product"],
                "social_title": "Meet Great Product", "social_description": "Discover Great Product.",
                "seo_score": 86, "issues": [], "recommendations": ["Use a primary keyword"],
            }, "test/free-model", 12.3)

    result, model, latency = await SEOService(FakeAI()).generate({"id": "1", "title": "Great Product"})
    assert result.seo_score == 86
    assert "script" not in result.optimized_description_html
    assert model == "test/free-model"
    assert latency == 12.3
