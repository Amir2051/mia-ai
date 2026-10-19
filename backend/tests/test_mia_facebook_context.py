import asyncio
import json

from sqlalchemy import select


def test_mia_reads_connected_facebook_pages_and_returns_openable_links(client, monkeypatch):
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import Shop, SocialAccount
    from app.services.meta import MetaClient

    shop_domain = "mia-facebook-context-test.myshopify.com"

    async def seed():
        async with AsyncSessionLocal() as db:
            shop = (await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))).scalar_one_or_none()
            if shop is None:
                shop = Shop(shop_domain=shop_domain, access_token_encrypted="test-placeholder", is_active=True)
                db.add(shop)
                await db.flush()
            existing = await db.execute(select(SocialAccount).where(
                SocialAccount.shop_id == shop.id,
                SocialAccount.external_account_id == "123456789",
            ))
            account = existing.scalar_one_or_none()
            if account is None:
                db.add(SocialAccount(
                    shop_id=shop.id,
                    provider="meta",
                    account_type="facebook_page",
                    external_account_id="123456789",
                    account_name="SafeNestT Test Page",
                    access_token_encrypted="encrypted-placeholder",
                    metadata_json=json.dumps({"category": "Software", "link": "https://www.facebook.com/safenestt.test"}),
                    is_active=True,
                ))
            else:
                account.account_name = "SafeNestT Test Page"
                account.access_token_encrypted = "encrypted-placeholder"
                account.metadata_json = json.dumps({"link": "https://www.facebook.com/safenestt.test"})
                account.is_active = True
            await db.commit()

    async def fake_feed(self, page_id, page_access_token):
        assert page_id == "123456789"
        assert page_access_token == "test-page-token"
        return {"data": [{"id": "123456789_77", "message": "Test page post content", "created_time": "2026-10-18T12:00:00+0000", "permalink_url": "https://www.facebook.com/safenestt.test/posts/77"}]}

    monkeypatch.setattr("app.routers.mia.decrypt_token", lambda value: "test-page-token")
    monkeypatch.setattr(MetaClient, "page_feed", fake_feed)
    asyncio.run(seed())

    async def authenticated_shop():
        return CurrentUser(shop_domain=shop_domain)

    client.app.dependency_overrides[get_optional_shop] = authenticated_shop
    try:
        response = client.post("/api/mia/chat", json={"message": "Can you read my Facebook pages?"})
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["social_data"]["connected"] is True
    assert payload["social_data"]["pages"][0]["name"] == "SafeNestT Test Page"
    assert payload["social_data"]["pages"][0]["posts"][0]["message"] == "Test page post content"
    assert "https://www.facebook.com/safenestt.test" in payload["answer"]
    assert "https://www.facebook.com/safenestt.test/posts/77" in payload["answer"]
    assert "Test page post content" in payload["answer"]


def test_mia_does_not_claim_facebook_access_when_no_page_is_connected(client):
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import Shop

    shop_domain = "mia-no-facebook-context-test.myshopify.com"

    async def seed():
        async with AsyncSessionLocal() as db:
            shop = (await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))).scalar_one_or_none()
            if shop is None:
                db.add(Shop(shop_domain=shop_domain, access_token_encrypted="test-placeholder", is_active=True))
                await db.commit()

    asyncio.run(seed())

    async def authenticated_shop():
        return CurrentUser(shop_domain=shop_domain)

    client.app.dependency_overrides[get_optional_shop] = authenticated_shop
    try:
        response = client.post("/api/mia/chat", json={"message": "Show my Facebook pages"})
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["social_data"]["connected"] is False
    assert "Connect Meta" in payload["answer"]
