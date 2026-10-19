import asyncio
import json

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


def test_chat_retrieval_returns_actual_saved_notes_in_answer_without_mutation(client):
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import MiaMemory, Shop

    shop_domain = "mia-retrieval-test.myshopify.com"
    expected = [
        "Use a premium, friendly, clear, trustworthy brand voice",
        "SEO titles should prioritize product names and relevant keywords, not automatically include prices",
        "Use only verified facts in product descriptions",
        "Never invent materials, dimensions, performance claims, or benefits",
        "Write image alt text based only on what the actual image shows",
        "Explain proposed Shopify changes before making them. Do not edit Shopify without my explicit approval",
    ]

    async def seed_shop_and_memory():
        async with AsyncSessionLocal() as db:
            existing = await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))
            shop = existing.scalar_one_or_none()
            if shop is None:
                shop = Shop(shop_domain=shop_domain, access_token_encrypted="test-placeholder", is_active=True)
                db.add(shop)
                await db.flush()
            existing_memory = await db.execute(select(MiaMemory).where(MiaMemory.shop_id == shop.id))
            memory = existing_memory.scalar_one_or_none()
            if memory is None:
                memory = MiaMemory(shop_id=shop.id, conversation_json="[]", knowledge_json=json.dumps(expected))
                db.add(memory)
            else:
                memory.knowledge_json = json.dumps(expected)
            await db.commit()

    asyncio.run(seed_shop_and_memory())

    async def authenticated_shop():
        return CurrentUser(shop_domain=shop_domain)

    client.app.dependency_overrides[get_optional_shop] = authenticated_shop
    try:
        response = client.post("/api/mia/chat", json={"message": (
            "Read the six existing merchant memory notes for safenestt.myshopify.com "
            "and list each note exactly as stored. Do not save, modify, or delete any notes. "
            "Do not edit Shopify products."
        )})
        assert response.status_code == 200
        payload = response.json()
        assert payload["memory_saved"] is False
        assert payload["knowledge"] == expected
        assert payload["knowledge_count"] == 6
        for note in expected:
            assert note in payload["answer"]
        assert "No notes were saved, changed, or deleted" in payload["answer"]

        # Verify the endpoint itself didn't change note contents; query the row for this shop.
        async def read_shop_memory():
            async with AsyncSessionLocal() as db:
                shop_result = await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))
                shop = shop_result.scalar_one()
                memory_result = await db.execute(select(MiaMemory).where(MiaMemory.shop_id == shop.id))
                return memory_result.scalar_one().knowledge_json
        assert json.loads(asyncio.run(read_shop_memory())) == expected
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)


def test_new_memory_save_does_not_silently_drop_older_long_term_notes(client):
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import MiaMemory, Shop

    shop_domain = "mia-long-memory-test.myshopify.com"
    older_notes = [f"Long-term preference {i}: keep this saved note" for i in range(35)]
    important_old_note = "Never invent product materials or dimensions in Shopify listings"
    older_notes[0] = important_old_note

    async def seed_shop_and_memory():
        async with AsyncSessionLocal() as db:
            shop = (await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))).scalar_one_or_none()
            if shop is None:
                shop = Shop(shop_domain=shop_domain, access_token_encrypted="test-placeholder", is_active=True)
                db.add(shop)
                await db.flush()
            memory = (await db.execute(select(MiaMemory).where(MiaMemory.shop_id == shop.id))).scalar_one_or_none()
            if memory is None:
                memory = MiaMemory(shop_id=shop.id, conversation_json="[]", knowledge_json=json.dumps(older_notes))
                db.add(memory)
            else:
                memory.knowledge_json = json.dumps(older_notes)
            await db.commit()

    asyncio.run(seed_shop_and_memory())

    async def authenticated_shop():
        return CurrentUser(shop_domain=shop_domain)

    client.app.dependency_overrides[get_optional_shop] = authenticated_shop
    try:
        response = client.post("/api/mia/chat", json={"message": "Remember that our default response style should be concise and practical"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["memory_saved"] is True
        assert payload["saved_count"] == 1

        async def read_notes():
            async with AsyncSessionLocal() as db:
                shop = (await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))).scalar_one()
                memory = (await db.execute(select(MiaMemory).where(MiaMemory.shop_id == shop.id))).scalar_one()
                return json.loads(memory.knowledge_json)

        saved_notes = asyncio.run(read_notes())
        assert len(saved_notes) == 36
        assert saved_notes[0] == important_old_note
        assert saved_notes[-1] == "our default response style should be concise and practical"
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)
