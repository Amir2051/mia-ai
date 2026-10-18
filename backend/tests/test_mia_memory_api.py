import asyncio

from sqlalchemy import select


def test_conversation_round_trip_is_scoped_to_authenticated_shop(client):
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import Shop

    shop_domain = "mia-memory-test.myshopify.com"

    async def seed_shop():
        async with AsyncSessionLocal() as db:
            existing = await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))
            shop = existing.scalar_one_or_none()
            if shop is None:
                db.add(Shop(shop_domain=shop_domain, access_token_encrypted="test-placeholder", is_active=True))
                await db.commit()

    asyncio.run(seed_shop())

    async def authenticated_shop():
        return CurrentUser(shop_domain=shop_domain)

    client.app.dependency_overrides[get_optional_shop] = authenticated_shop
    try:
        initial = client.get("/api/mia/conversation")
        assert initial.status_code == 200
        assert initial.json()["messages"] == []

        saved = client.put("/api/mia/conversation", json={"messages": [
            {"role": "user", "content": "How do I improve product SEO?"},
            {"role": "mia", "content": "Start with clear search intent and useful product details."},
        ]})
        assert saved.status_code == 200
        assert saved.json()["saved"] is True
        assert saved.json()["message_count"] == 2

        restored = client.get("/api/mia/conversation")
        assert restored.status_code == 200
        assert restored.json()["messages"] == [
            {"role": "user", "content": "How do I improve product SEO?"},
            {"role": "assistant", "content": "Start with clear search intent and useful product details."},
        ]
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)
