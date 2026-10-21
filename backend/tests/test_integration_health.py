import asyncio
from types import SimpleNamespace

from sqlalchemy import select


def test_integration_health_live_checks_shopify_and_does_not_probe_paid_ai(client, monkeypatch):
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import Shop, SocialAccount
    from app.routers import settings as settings_router
    from app.shopify.config import settings as app_settings

    shop_domain = "mia-integration-health-test.myshopify.com"

    async def seed():
        async with AsyncSessionLocal() as db:
            shop = (await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))).scalar_one_or_none()
            if shop is None:
                shop = Shop(
                    shop_domain=shop_domain,
                    access_token_encrypted="placeholder",
                    scope=app_settings.shopify_scopes,
                    is_active=True,
                )
                db.add(shop)
                await db.flush()
            account = (await db.execute(
                select(SocialAccount).where(
                    SocialAccount.shop_id == shop.id,
                    SocialAccount.provider == "meta",
                )
            )).scalar_one_or_none()
            if account is None:
                db.add(SocialAccount(
                    shop_id=shop.id,
                    provider="meta",
                    account_type="facebook_page",
                    external_account_id="page-1",
                    account_name="Demo Page",
                    access_token_encrypted="placeholder",
                    is_active=True,
                ))
            await db.commit()
            return shop.id, shop.scope

    shop_id, scope = asyncio.run(seed())

    async def authenticated_shop():
        return CurrentUser(shop_domain=shop_domain)

    async def fake_shop(current, db):
        return SimpleNamespace(id=shop_id, shop_domain=shop_domain, scope=scope, is_active=True), "test-access-token"

    class FakeShopifyClient:
        def __init__(self, shop_domain, access_token):
            assert shop_domain == shop_domain_value
            assert access_token == "test-access-token"

        async def graphql(self, query):
            assert "shop { name myshopifyDomain }" in query
            return {"shop": {"name": "Demo Store", "myshopifyDomain": shop_domain}}

    shop_domain_value = shop_domain
    monkeypatch.setattr(settings_router, "_get_shop_from_current", fake_shop)
    monkeypatch.setattr(settings_router, "ShopifyAPIClient", FakeShopifyClient)
    client.app.dependency_overrides[get_optional_shop] = authenticated_shop
    try:
        response = client.get("/api/settings/health")
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["overall_status"] == "healthy"
    assert payload["database"]["status"] == "healthy"
    assert payload["shopify"]["status"] == "verified"
    assert payload["shopify"]["shop_name"] == "Demo Store"
    assert payload["shopify"]["missing_scopes"] == []
    assert payload["ai"]["status"] in {"configured_not_tested", "not_configured"}
    meta = next(item for item in payload["social"] if item["provider"] == "meta")
    assert meta["saved_accounts"] == 1
    assert meta["status"] == "saved_account_not_verified"


def test_integration_health_surfaces_missing_shopify_scopes(client, monkeypatch):
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import Shop
    from app.routers import settings as settings_router
    from app.shopify.config import settings as app_settings

    shop_domain = "mia-integration-health-scope-test.myshopify.com"
    async def seed():
        async with AsyncSessionLocal() as db:
            shop = (await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))).scalar_one_or_none()
            if shop is None:
                shop = Shop(
                    shop_domain=shop_domain,
                    access_token_encrypted="placeholder",
                    scope="read_products",
                    is_active=True,
                )
                db.add(shop)
                await db.commit()
            return shop.id

    shop_id = asyncio.run(seed())
    async def authenticated_shop():
        return CurrentUser(shop_domain=shop_domain)

    async def fake_shop(current, db):
        return SimpleNamespace(id=shop_id, shop_domain=shop_domain, scope="read_products", is_active=True), "test-access-token"

    class FakeShopifyClient:
        def __init__(self, shop_domain, access_token):
            pass
        async def graphql(self, query):
            return {"shop": {"name": "Demo Store", "myshopifyDomain": shop_domain}}

    monkeypatch.setattr(app_settings, "shopify_scopes", "read_products,read_orders")
    monkeypatch.setattr(settings_router, "_get_shop_from_current", fake_shop)
    monkeypatch.setattr(settings_router, "ShopifyAPIClient", FakeShopifyClient)
    client.app.dependency_overrides[get_optional_shop] = authenticated_shop
    try:
        response = client.get("/api/settings/health")
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["overall_status"] == "degraded"
    assert payload["shopify"]["status"] == "verified_with_permission_gaps"
    assert payload["shopify"]["missing_scopes"] == ["read_orders"]


def test_integration_health_database_failure_is_reported_without_leaking_details(client, monkeypatch):
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.models.database import get_db

    async def authenticated_shop():
        return CurrentUser(shop_domain="mia-db-health-test.myshopify.com")

    async def broken_db():
        class BrokenSession:
            async def execute(self, statement):
                raise RuntimeError("private database connection details")

            async def rollback(self):
                return None

        yield BrokenSession()

    client.app.dependency_overrides[get_optional_shop] = authenticated_shop
    client.app.dependency_overrides[get_db] = broken_db
    try:
        response = client.get("/api/settings/health")
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)
        client.app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["overall_status"] == "degraded"
    assert payload["database"]["status"] == "unavailable"
    assert payload["shopify"]["status"] == "not_checked"
    assert "private database connection details" not in response.text
