import asyncio
import json

from sqlalchemy import select


def test_general_question_does_not_fetch_full_shopify_context(client, monkeypatch):
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import Shop
    from app.services.openrouter import OpenRouterService

    shop_domain = "mia-general-chat-routing-test.myshopify.com"

    async def seed_shop():
        async with AsyncSessionLocal() as db:
            shop = (await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))).scalar_one_or_none()
            if shop is None:
                db.add(Shop(shop_domain=shop_domain, access_token_encrypted="test-placeholder", is_active=True))
                await db.commit()

    asyncio.run(seed_shop())

    async def authenticated_shop():
        return CurrentUser(shop_domain=shop_domain)

    async def forbidden_context(_shop):
        raise AssertionError("General explanations must not download Shopify data")

    async def fake_chat(self, messages, **kwargs):
        assert not any("STORE DATA (live from Shopify)" in message["content"] for message in messages)
        return "SEO is the practice of improving how pages appear in search results.", "test-model", 12.0

    monkeypatch.setattr("app.routers.mia._context", forbidden_context)
    monkeypatch.setattr(OpenRouterService, "chat_text", fake_chat)
    client.app.dependency_overrides[get_optional_shop] = authenticated_shop
    try:
        response = client.post("/api/mia/chat", json={"message": "What is SEO?"})
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "SEO is the practice" in payload["answer"]
    assert payload["data_used"]["store_data_loaded"] is False
    assert payload["data_used"]["reason"].startswith("general conversation")


def test_forget_notes_does_not_fetch_shopify_context(client, monkeypatch):
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import MiaMemory, Shop

    shop_domain = "mia-forget-routing-test.myshopify.com"

    async def seed_shop_and_memory():
        async with AsyncSessionLocal() as db:
            shop = (await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))).scalar_one_or_none()
            if shop is None:
                shop = Shop(shop_domain=shop_domain, access_token_encrypted="test-placeholder", is_active=True)
                db.add(shop)
                await db.flush()
            memory = (await db.execute(select(MiaMemory).where(MiaMemory.shop_id == shop.id))).scalar_one_or_none()
            if memory is None:
                memory = MiaMemory(shop_id=shop.id, conversation_json="[]", knowledge_json=json.dumps(["Brand voice is friendly"]))
                db.add(memory)
            else:
                memory.knowledge_json = json.dumps(["Brand voice is friendly"])
            await db.commit()

    asyncio.run(seed_shop_and_memory())

    async def authenticated_shop():
        return CurrentUser(shop_domain=shop_domain)

    async def forbidden_context(_shop):
        raise AssertionError("Forgetting notes must not download Shopify data")

    monkeypatch.setattr("app.routers.mia._context", forbidden_context)
    client.app.dependency_overrides[get_optional_shop] = authenticated_shop
    try:
        response = client.post("/api/mia/chat", json={"message": "Forget all notes"})
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["memory_removed"] == 1
    assert payload["knowledge"] == []
    assert payload["data_used"]["memory_only"] is True


def test_ambiguous_product_request_asks_before_generating(client, monkeypatch):
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import Shop

    shop_domain = "mia-ambiguous-product-routing-test.myshopify.com"

    async def seed_shop():
        async with AsyncSessionLocal() as db:
            shop = (await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))).scalar_one_or_none()
            if shop is None:
                db.add(Shop(shop_domain=shop_domain, access_token_encrypted="test-placeholder", is_active=True))
                await db.commit()

    asyncio.run(seed_shop())

    async def authenticated_shop():
        return CurrentUser(shop_domain=shop_domain)

    async def fake_context(_shop):
        return {
            "shop": shop_domain,
            "counts": {"products": 2, "orders": 0, "customers": 0, "inventory_units": 5},
            "products": [
                {"id": "gid://shopify/Product/1", "title": "Blue Travel Mug", "inventory": 3},
                {"id": "gid://shopify/Product/2", "title": "Red Travel Mug", "inventory": 2},
            ],
            "sales_channels": [],
            "warnings": [],
            "revenue": 0,
            "customers": [],
            "orders": [],
            "low_stock": [],
        }

    async def forbidden_product_fetch(*args, **kwargs):
        raise AssertionError("Mia must clarify the ambiguous target before generating content")

    monkeypatch.setattr("app.routers.mia._context", fake_context)
    monkeypatch.setattr("app.routers.mia.ProductService.get_product", forbidden_product_fetch)
    client.app.dependency_overrides[get_optional_shop] = authenticated_shop
    try:
        response = client.post(
            "/api/mia/chat",
            json={
                "message": "Generate an SEO package for Travel Mug",
                "product_id": "gid://shopify/Product/1",
            },
        )
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["needs_clarification"] is True
    assert "I won't guess" in payload["answer"]
