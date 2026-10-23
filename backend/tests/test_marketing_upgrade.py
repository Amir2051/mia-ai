import asyncio

from app.services import seo
from app.services.seo import marketing_prompt
from app.shopify.config import settings


class _CaptureAI:
    def __init__(self):
        self.model = "unset"

    async def chat_json(self, messages, model=None, temperature=0.7):
        self.model = model
        return (
            {
                "facebook": {"copy": "a", "cta": "Shop"},
                "instagram": {"copy": "b", "cta": "Shop"},
                "tiktok": {"copy": "c", "cta": "Shop"},
                "email": {"subject": "s", "body": "body"},
                "ad": {"primary_text": "p", "headline": "h", "description": "d", "cta": "Shop"},
                "creative_prompt": "cp",
                "hashtags": ["#x"],
            },
            model or "resolved",
            1.0,
        )


def test_marketing_uses_configured_model_when_caller_passes_none(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_marketing_model", "vendor/strong-model")
    ai = _CaptureAI()
    asyncio.run(seo.generate_marketing(ai, {"id": "gid://1", "title": "T", "images": []}))
    assert ai.model == "vendor/strong-model"


def test_explicit_model_overrides_configured_marketing_model(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_marketing_model", "vendor/strong-model")
    ai = _CaptureAI()
    asyncio.run(seo.generate_marketing(ai, {"id": "gid://1", "title": "T", "images": []}, model="caller/explicit"))
    assert ai.model == "caller/explicit"


def test_merchant_brand_notes_surface_in_marketing_prompt():
    msgs = marketing_prompt({"title": "T"}, merchant_notes=["Brand voice: playful and bold"])
    blob = " ".join(m["content"] for m in msgs)
    assert "playful and bold" in blob


def test_marketing_backfills_hashtags_when_model_returns_none():
    import asyncio
    from app.services import seo

    class _NoTagsAI:
        async def chat_json(self, messages, model=None, temperature=0.7):
            return (
                {"facebook": {"copy": "a", "cta": "Shop"}, "instagram": {"copy": "b", "cta": "Shop"},
                 "tiktok": {"copy": "c", "cta": "Shop"}, "email": {"subject": "s", "body": "body"},
                 "ad": {"primary_text": "p", "headline": "h", "description": "d", "cta": "Shop"},
                 "creative_prompt": "cp", "hashtags": []},
                "m", 1.0,
            )

    prod = {"id": "gid://1", "title": "T", "productType": "Lighting", "tags": ["LED"], "images": []}
    res, _, _ = asyncio.run(seo.generate_marketing(_NoTagsAI(), prod))
    assert res.hashtags, "hashtags should be backfilled from product when the model omits them"
