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
    asyncio.run(seo.generate_marketing(ai, {"id": "gid://1", "title": "T", "descriptionHtml": "A verified product description with enough detail to support grounded campaign copy.", "images": []}))
    assert ai.model == "vendor/strong-model"


def test_explicit_model_overrides_configured_marketing_model(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_marketing_model", "vendor/strong-model")
    ai = _CaptureAI()
    asyncio.run(seo.generate_marketing(ai, {"id": "gid://1", "title": "T", "descriptionHtml": "A verified product description with enough detail to support grounded campaign copy.", "images": []}, model="caller/explicit"))
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

    prod = {"id": "gid://1", "title": "T", "productType": "Lighting", "descriptionHtml": "A verified product description with enough detail to support grounded campaign copy.", "tags": ["LED"], "images": []}
    res, _, _ = asyncio.run(seo.generate_marketing(_NoTagsAI(), prod))
    assert res.hashtags, "hashtags should be backfilled from product when the model omits them"


def test_generated_ad_fields_are_bounded_and_use_safe_cta_without_url():
    import asyncio
    from app.services.seo import generate_marketing

    class _AdAI:
        async def chat_json(self, messages, model=None, temperature=0.7):
            return ({
                "facebook": {"copy": "Facebook copy", "cta": "Shop Now"},
                "instagram": {"copy": "Instagram copy", "cta": "Shop Now"},
                "tiktok": {"copy": "TikTok copy", "cta": "Shop Now"},
                "email": {"subject": "Subject", "body": "Body"},
                "ad": {"primary_text": "p" * 1200, "headline": "H" * 60, "description": "D" * 120, "cta": "Shop Now"},
                "creative_prompt": "Use the real product image", "hashtags": ["#Example"],
            }, "test-model", 1.0)

    result, _, _ = asyncio.run(generate_marketing(_AdAI(), {"id": "p1", "title": "Example product", "descriptionHtml": "A verified product description with enough detail to support grounded campaign copy.", "images": []}))
    assert len(result.ad["primary_text"]) <= 1000
    assert len(result.ad["headline"]) <= 40
    assert len(result.ad["description"]) <= 90
    assert result.ad["cta"] == "Learn More"
    assert result.facebook["cta"] == "Learn More"
    assert result.instagram["cta"] == "Learn More"
    assert result.tiktok["cta"] == "Learn More"


def test_insufficient_product_data_skips_ai_and_uses_grounded_fallback():
    import asyncio
    from app.services.seo import generate_marketing

    class _MustNotCallAI:
        async def chat_json(self, *args, **kwargs):
            raise AssertionError("AI must not invent product details when source data is missing")

    product = {
        "id": "p1", "title": "The Inventory Not Tracked Snowboard",
        "productType": "Snowboard", "vendor": "SafeNestT", "descriptionHtml": "",
        "tags": [], "images": [],
    }
    result, model, _ = asyncio.run(generate_marketing(_MustNotCallAI(), product))
    combined = " ".join([result.facebook["copy"], result.instagram["copy"], result.tiktok["copy"], result.ad["primary_text"]]).lower()
    assert model == "deterministic-fallback-insufficient-product-details"
    assert "surveillance" not in combined and "tracking" not in combined and "sensor" not in combined
    assert "newarrival" not in " ".join(result.hashtags).lower()
    assert result.ad["cta"] == "Learn More"
