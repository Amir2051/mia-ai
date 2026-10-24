import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select


def test_page_capabilities_requires_publish_task():
    from app.services.meta import page_capabilities
    caps = page_capabilities({"tasks": ["CREATE_CONTENT", "ANALYZE"], "instagram_business_account": {"id": "ig1"}})
    assert caps["can_publish_facebook"] is True
    assert caps["instagram_linked"] is True
    assert caps["can_publish_instagram"] is True


def test_page_capabilities_insufficient_tasks_not_ready():
    from app.services.meta import page_capabilities
    caps = page_capabilities({"tasks": ["ANALYZE", "MODERATE"], "instagram_business_account": {"id": "ig1"}})
    assert caps["can_publish_facebook"] is False
    assert caps["can_publish_instagram"] is False
    assert caps["instagram_linked"] is True


def test_page_capabilities_handles_missing_fields():
    from app.services.meta import page_capabilities
    assert page_capabilities({})["can_publish_facebook"] is False
    assert page_capabilities(None)["can_publish_facebook"] is False
    assert page_capabilities({"tasks": None, "instagram_business_account": None})["instagram_linked"] is False
    assert set(page_capabilities({}).keys()) == {"tasks", "can_publish_facebook", "instagram_linked", "can_publish_instagram"}


def test_meta_client_version_falls_back_to_oauth_version(monkeypatch):
    from app.services.meta import MetaClient
    from app.shopify.config import settings
    monkeypatch.setattr(settings, "meta_graph_api_base_url", "https://graph.facebook.com")
    monkeypatch.setattr(settings, "meta_graph_api_version", "")
    monkeypatch.setattr(settings, "meta_oauth_version", "v24.0")
    assert MetaClient().base_url == "https://graph.facebook.com/v24.0"
    monkeypatch.setattr(settings, "meta_graph_api_version", "v20.0")
    assert MetaClient().base_url == "https://graph.facebook.com/v20.0"


def _seed_shop(domain):
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import Shop

    async def _run():
        async with AsyncSessionLocal() as db:
            shop = Shop(shop_domain=domain, access_token_encrypted="enc", is_active=True)
            db.add(shop)
            await db.commit()
            await db.refresh(shop)
            return shop.id
    return asyncio.run(_run())


def _seed_meta_account(domain, page_name, tasks):
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import Shop, SocialAccount
    from app.security.rls import set_shop_context
    from app.security.tokens import encrypt_token
    from app.services.meta import serialize_meta_metadata

    async def _run():
        async with AsyncSessionLocal() as db:
            shop = Shop(shop_domain=domain, access_token_encrypted="enc", is_active=True)
            db.add(shop)
            await db.flush()
            await set_shop_context(db, domain, shop.id)
            db.add(SocialAccount(
                shop_id=shop.id, provider="meta", account_type="facebook_page",
                external_account_id=page_name, account_name=page_name,
                access_token_encrypted=encrypt_token("pt"),
                metadata_json=serialize_meta_metadata({"tasks": tasks}), is_active=True,
            ))
            await db.commit()
            row = await db.execute(select(SocialAccount).where(SocialAccount.shop_id == shop.id))
            return row.scalar_one().id
    return asyncio.run(_run())


def test_meta_complete_reports_per_page_capability(client: TestClient, monkeypatch):
    import time
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.routers import social
    from app.services.meta import MetaClient
    domain = "meta-caps.myshopify.com"
    _seed_shop(domain)
    state = social._sign_state(domain, "nonce-a", int(time.time()))

    async def fake_exchange(self, code):
        return {"access_token": "user-token"}

    async def fake_me(self):
        return {"id": "u1", "name": "Owner"}

    async def fake_pages(self):
        return {"data": [
            {"id": "p1", "name": "Capable Page", "access_token": "pt1", "tasks": ["CREATE_CONTENT", "MANAGE"], "instagram_business_account": {"id": "ig1"}},
            {"id": "p2", "name": "Readonly Page", "access_token": "pt2", "tasks": ["ANALYZE"]},
        ]}
    monkeypatch.setattr(MetaClient, "exchange_code", fake_exchange)
    monkeypatch.setattr(MetaClient, "me", fake_me)
    monkeypatch.setattr(MetaClient, "pages", fake_pages)
    client.app.dependency_overrides[get_optional_shop] = lambda: CurrentUser(domain)
    try:
        r = client.post("/api/social/meta/complete", json={"code": "c", "state": state})
        assert r.status_code == 200, r.text
        assert r.json()["pages_saved"] == 2
        accounts = client.get("/api/social/accounts").json()["accounts"]
        by_name = {a["account_name"]: a for a in accounts}
        assert by_name["Capable Page"]["publish_ready"] is True
        assert by_name["Capable Page"]["capabilities"]["can_publish_instagram"] is True
        assert by_name["Readonly Page"]["publish_ready"] is False
        assert by_name["Readonly Page"]["capabilities"]["can_publish_facebook"] is False
    finally:
        client.app.dependency_overrides.clear()


def test_meta_complete_zero_pages_is_actionable(client: TestClient, monkeypatch):
    import time
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.routers import social
    from app.services.meta import MetaClient
    domain = "meta-zero.myshopify.com"
    _seed_shop(domain)
    state = social._sign_state(domain, "nonce-b", int(time.time()))

    async def fake_exchange(self, code):
        return {"access_token": "user-token"}

    async def fake_me(self):
        return {"id": "u1", "name": "Owner"}

    async def fake_pages(self):
        return {"data": []}
    monkeypatch.setattr(MetaClient, "exchange_code", fake_exchange)
    monkeypatch.setattr(MetaClient, "me", fake_me)
    monkeypatch.setattr(MetaClient, "pages", fake_pages)
    client.app.dependency_overrides[get_optional_shop] = lambda: CurrentUser(domain)
    try:
        r = client.post("/api/social/meta/complete", json={"code": "c", "state": state})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pages_saved"] == 0
        assert body["status"] == "no_pages"
        assert "Page" in body["message"]
    finally:
        client.app.dependency_overrides.clear()


def test_meta_complete_graph_error_surfaces(client: TestClient, monkeypatch):
    import time
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.routers import social
    from app.services.meta import MetaClient, MetaAPIError
    domain = "meta-err.myshopify.com"
    _seed_shop(domain)
    state = social._sign_state(domain, "nonce-c", int(time.time()))

    async def fake_exchange(self, code):
        return {"access_token": "user-token"}

    async def boom(self):
        raise MetaAPIError("Invalid OAuth access token", 401)
    monkeypatch.setattr(MetaClient, "exchange_code", fake_exchange)
    monkeypatch.setattr(MetaClient, "me", boom)
    client.app.dependency_overrides[get_optional_shop] = lambda: CurrentUser(domain)
    try:
        r = client.post("/api/social/meta/complete", json={"code": "c", "state": state})
        assert r.status_code == 401, r.text
    finally:
        client.app.dependency_overrides.clear()


def test_publish_facebook_blocked_without_publish_task(client: TestClient, monkeypatch):
    from app.auth.dependencies import CurrentUser, get_optional_shop
    domain = "meta-block.myshopify.com"
    account_id = _seed_meta_account(domain, "Readonly", ["ANALYZE"])
    client.app.dependency_overrides[get_optional_shop] = lambda: CurrentUser(domain)
    try:
        r = client.post("/api/social/meta/facebook/publish", json={"account_id": account_id, "message": "hi"})
        assert r.status_code == 409, r.text
        assert "approve" in r.json()["detail"].lower()
    finally:
        client.app.dependency_overrides.clear()


def test_social_accounts_isolated_per_merchant(client: TestClient, monkeypatch):
    from app.auth.dependencies import CurrentUser, get_optional_shop
    _seed_meta_account("iso-a.myshopify.com", "PageA", ["CREATE_CONTENT"])
    _seed_meta_account("iso-b.myshopify.com", "PageB", ["CREATE_CONTENT"])
    client.app.dependency_overrides[get_optional_shop] = lambda: CurrentUser("iso-a.myshopify.com")
    try:
        names = [a["account_name"] for a in client.get("/api/social/accounts").json()["accounts"]]
        assert "PageA" in names
        assert "PageB" not in names
    finally:
        client.app.dependency_overrides.clear()
