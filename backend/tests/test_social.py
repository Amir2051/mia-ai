import asyncio
from sqlalchemy import select


def test_social_disconnect_is_scoped_to_the_current_shop(client):
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import Shop, SocialAccount

    current_domain = "social-owner-test.myshopify.com"
    other_domain = "social-other-test.myshopify.com"

    async def seed():
        async with AsyncSessionLocal() as db:
            shops = {}
            for domain in (current_domain, other_domain):
                shop = (await db.execute(select(Shop).where(Shop.shop_domain == domain))).scalar_one_or_none()
                if shop is None:
                    shop = Shop(shop_domain=domain, access_token_encrypted="test-placeholder", is_active=True)
                    db.add(shop)
                    await db.flush()
                shops[domain] = shop
            rows = {}
            for domain, shop in shops.items():
                row = (await db.execute(select(SocialAccount).where(
                    SocialAccount.shop_id == shop.id,
                    SocialAccount.external_account_id == "page-" + domain,
                ))).scalar_one_or_none()
                if row is None:
                    row = SocialAccount(
                        shop_id=shop.id, provider="meta", account_type="facebook_page",
                        external_account_id="page-" + domain, account_name=domain,
                        access_token_encrypted="encrypted-test-token", metadata_json="{}", is_active=True,
                    )
                    db.add(row)
                    await db.flush()
                rows[domain] = row.id
            await db.commit()
            return rows

    ids = asyncio.run(seed())

    async def authenticated_shop():
        return CurrentUser(shop_domain=current_domain)

    client.app.dependency_overrides[get_optional_shop] = authenticated_shop
    try:
        forbidden = client.delete("/api/social/accounts/" + str(ids[other_domain]))
        assert forbidden.status_code == 404
        disconnected = client.delete("/api/social/accounts/" + str(ids[current_domain]))
        assert disconnected.status_code == 200
        assert disconnected.json()["status"] == "disconnected"

        async def read_rows():
            async with AsyncSessionLocal() as db:
                own = (await db.execute(select(SocialAccount).where(SocialAccount.id == ids[current_domain]))).scalar_one()
                other = (await db.execute(select(SocialAccount).where(SocialAccount.id == ids[other_domain]))).scalar_one()
                return own, other

        own, other = asyncio.run(read_rows())
        assert own.is_active is False
        assert own.access_token_encrypted == ""
        assert other.is_active is True
        assert other.access_token_encrypted == "encrypted-test-token"
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)


def test_instagram_publishing_rejects_non_https_or_credentialed_image_urls(client):
    response = client.post("/api/social/meta/instagram/publish", json={
        "account_id": 1,
        "image_url": "http://example.com/image.jpg",
        "caption": "Hello",
    })
    assert response.status_code == 422

    response = client.post("/api/social/meta/instagram/publish", json={
        "account_id": 1,
        "image_url": "https://user:password@example.com/image.jpg",
        "caption": "Hello",
    })
    assert response.status_code == 422


def test_instagram_publishing_rejects_overlong_caption(client):
    response = client.post("/api/social/meta/instagram/publish", json={
        "account_id": 1,
        "image_url": "https://example.com/image.jpg",
        "caption": "x" * 2201,
    })
    assert response.status_code == 422


def test_meta_business_login_uses_configured_permission_set_without_scope(monkeypatch):
    from urllib.parse import parse_qs, urlparse
    from app.services.meta import MetaClient
    from app.shopify.config import settings

    monkeypatch.setattr(settings, "meta_app_id", "test-meta-app")
    monkeypatch.setattr(settings, "meta_login_config_id", "business-config-123")
    monkeypatch.setattr(settings, "meta_redirect_uri", "https://mia.example.test/api/social/meta/callback")
    monkeypatch.setattr(settings, "shopify_app_url", "https://mia.example.test")
    url = MetaClient().authorization_url("state123")
    params = parse_qs(urlparse(url).query)
    assert params["config_id"] == ["business-config-123"]
    assert params["override_default_response_type"] == ["true"]
    assert params["response_type"] == ["code"]
    assert params["redirect_uri"] == ["https://mia.example.test/api/social/meta/callback"]
    assert "scope" not in params


def test_meta_legacy_login_still_uses_configured_scopes(monkeypatch):
    from urllib.parse import parse_qs, urlparse
    from app.services.meta import MetaClient
    from app.shopify.config import settings

    monkeypatch.setattr(settings, "meta_app_id", "test-meta-app")
    monkeypatch.setattr(settings, "meta_login_config_id", "")
    monkeypatch.setattr(settings, "meta_oauth_scopes", "pages_show_list,pages_read_engagement")
    url = MetaClient().authorization_url("state123")
    params = parse_qs(urlparse(url).query)
    assert params["scope"] == ["pages_show_list,pages_read_engagement"]
    assert "config_id" not in params


def test_meta_oauth_code_exchange_does_not_require_existing_access_token(monkeypatch):
    import asyncio
    from app.services import meta as meta_module
    from app.services.meta import MetaClient
    from app.shopify.config import settings

    class FakeResponse:
        status_code = 200
        text = '{"access_token":"fresh-user-token"}'

        def json(self):
            return {"access_token": "fresh-user-token"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None):
            assert url.endswith('/oauth/access_token')
            assert params['code'] == 'authorization-code'
            assert 'access_token' not in params
            return FakeResponse()

    monkeypatch.setattr(meta_module.httpx, 'AsyncClient', FakeAsyncClient)
    monkeypatch.setattr(settings, 'meta_app_id', 'test-app-id')
    monkeypatch.setattr(settings, 'meta_app_secret', 'test-app-secret')
    monkeypatch.setattr(settings, 'meta_redirect_uri', 'https://example.test/callback')
    result = asyncio.run(MetaClient().exchange_code('authorization-code'))
    assert result['access_token'] == 'fresh-user-token'
