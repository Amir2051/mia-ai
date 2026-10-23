from fastapi import HTTPException
from types import SimpleNamespace


def test_apply_requires_confirmation(client, monkeypatch):
    from app.routers import seo
    from app.auth.dependencies import CurrentUser, get_current_shop

    client.app.dependency_overrides[get_current_shop] = lambda: CurrentUser("store.myshopify.com")
    monkeypatch.setattr(seo, "_shop", lambda current, db: None)
    response = client.post("/api/seo/apply", json={"product_id": "gid://shopify/Product/1", "changes": {"title": "New"}})
    assert response.status_code == 400
    assert "confirmation" in response.json()["detail"].lower()
    client.app.dependency_overrides.clear()


def test_apply_rejects_disallowed_fields(client, monkeypatch):
    from app.routers import seo
    from app.auth.dependencies import CurrentUser, get_current_shop

    client.app.dependency_overrides[get_current_shop] = lambda: CurrentUser("store.myshopify.com")
    monkeypatch.setattr(seo, "_shop", lambda current, db: object())
    response = client.post("/api/seo/apply", json={"product_id": "gid://shopify/Product/1", "confirmed": True, "changes": {"price": "999"}})
    assert response.status_code == 400
    assert "permitted" in response.json()["detail"].lower()
    client.app.dependency_overrides.clear()


def test_apply_updates_only_approved_fields(client, monkeypatch):
    from app.routers import seo
    from app.auth.dependencies import CurrentUser, get_current_shop

    class FakeClient:
        async def update_product(self, product_id, changes):
            self.changes = changes
            return {"productUpdate": {"product": {"id": product_id}, "userErrors": []}}

    fake = FakeClient()
    client.app.dependency_overrides[get_current_shop] = lambda: CurrentUser("store.myshopify.com")
    async def fake_shop(current, db):
        return SimpleNamespace(id=1, shop_domain="store.myshopify.com")
    async def fake_audit(*args, **kwargs):
        return True
    monkeypatch.setattr(seo, "record_audit_event", fake_audit)
    monkeypatch.setattr(seo, "_shop", fake_shop)
    monkeypatch.setattr(seo, "_client", lambda shop: fake)
    async def fake_get_product(client, product_id):
        product = {"id": product_id, "title": "Old", "handle": "old", "tags": [], "seo_title": None, "seo_description": None}
        changes = getattr(client, "changes", {})
        for key, value in changes.items():
            if key == "seo":
                product["seo_title"] = value.get("title", product["seo_title"])
                product["seo_description"] = value.get("description", product["seo_description"])
            else:
                product[key] = value
        return product
    monkeypatch.setattr(seo, "_get_product", fake_get_product)
    response = client.post("/api/seo/apply", json={
        "product_id": "gid://shopify/Product/1",
        "confirmed": True,
        "changes": {"title": "New", "tags": ["seo"], "price": "999", "status": "ACTIVE"},
    })
    assert response.status_code == 200
    assert fake.changes == {"title": "New", "tags": ["seo"]}
    client.app.dependency_overrides.clear()


def test_apply_normalizes_handle_when_explicitly_enabled(client, monkeypatch):
    from app.routers import seo
    from app.auth.dependencies import CurrentUser, get_current_shop

    class FakeClient:
        async def update_product(self, product_id, changes):
            self.changes = changes
            return {"productUpdate": {"product": {"id": product_id}, "userErrors": []}}

    fake = FakeClient()
    client.app.dependency_overrides[get_current_shop] = lambda: CurrentUser("store.myshopify.com")
    async def fake_shop(current, db):
        return SimpleNamespace(id=1, shop_domain="store.myshopify.com")
    async def fake_audit(*args, **kwargs):
        return True
    monkeypatch.setattr(seo, "record_audit_event", fake_audit)
    monkeypatch.setattr(seo, "_shop", fake_shop)
    monkeypatch.setattr(seo, "_client", lambda shop: fake)
    async def fake_get_product(client, product_id):
        product = {"id": product_id, "title": "Old", "handle": "old", "tags": [], "seo_title": None, "seo_description": None}
        changes = getattr(client, "changes", {})
        for key, value in changes.items():
            if key == "seo":
                product["seo_title"] = value.get("title", product["seo_title"])
                product["seo_description"] = value.get("description", product["seo_description"])
            else:
                product[key] = value
        return product
    monkeypatch.setattr(seo, "_get_product", fake_get_product)
    response = client.post("/api/seo/apply", json={
        "product_id": "gid://shopify/Product/1",
        "confirmed": True,
        "apply_handle": True,
        "changes": {"handle": "  My New Product!! "},
    })
    assert response.status_code == 200
    assert fake.changes == {"handle": "my-new-product"}
    client.app.dependency_overrides.clear()


def test_apply_rejects_empty_explicit_handle(client, monkeypatch):
    from app.routers import seo
    from app.auth.dependencies import CurrentUser, get_current_shop
    client.app.dependency_overrides[get_current_shop] = lambda: CurrentUser("store.myshopify.com")
    monkeypatch.setattr(seo, "_shop", lambda current, db: object())
    response = client.post("/api/seo/apply", json={
        "product_id": "gid://shopify/Product/1", "confirmed": True,
        "apply_handle": True, "changes": {"handle": "!!!"},
    })
    assert response.status_code == 422
    assert "handle" in response.json()["detail"].lower()
    client.app.dependency_overrides.clear()

# handle application is explicitly opt-in; normalization is covered above.



def test_apply_surfaces_shopify_user_errors_instead_of_claiming_success(client, monkeypatch):
    from app.routers import seo
    from app.auth.dependencies import CurrentUser, get_current_shop

    class FakeClient:
        async def update_product(self, product_id, changes):
            return {
                "productUpdate": {
                    "product": {"id": product_id},
                    "userErrors": [{"field": ["seo", "title"], "message": "SEO title is invalid"}],
                }
            }

    client.app.dependency_overrides[get_current_shop] = lambda: CurrentUser("store.myshopify.com")
    async def fake_shop(current, db):
        return SimpleNamespace(id=1, shop_domain="store.myshopify.com")
    monkeypatch.setattr(seo, "_shop", fake_shop)
    monkeypatch.setattr(seo, "_client", lambda shop: FakeClient())
    async def fake_get_product(client, product_id):
        return {"id": product_id, "title": "Existing"}
    monkeypatch.setattr(seo, "_get_product", fake_get_product)

    response = client.post("/api/seo/apply", json={
        "product_id": "gid://shopify/Product/1",
        "confirmed": True,
        "changes": {"seo": {"title": "New SEO title"}},
    })

    assert response.status_code == 422
    assert "Shopify rejected" in response.json()["detail"]
    client.app.dependency_overrides.clear()


def test_marketing_history_is_scoped_to_current_shop(client):
    import asyncio
    from sqlalchemy import select
    from app.auth.dependencies import CurrentUser, get_current_shop
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import AuditLog, Shop

    async def seed():
        async with AsyncSessionLocal() as db:
            first = Shop(shop_domain="history-one.myshopify.com", access_token_encrypted="one", is_active=True)
            second = Shop(shop_domain="history-two.myshopify.com", access_token_encrypted="two", is_active=True)
            db.add_all([first, second])
            await db.flush()
            db.add_all([
                AuditLog(shop_id=first.id, action="seo.preview", entity_type="product", entity_id="p1", details_json='{"status":"preview_ready","product_title":"One"}'),
                AuditLog(shop_id=second.id, action="seo.applied", entity_type="product", entity_id="p2", details_json='{"status":"applied","product_title":"Two"}'),
            ])
            await db.commit()
            return first.id

    first_id = asyncio.run(seed())
    client.app.dependency_overrides[get_current_shop] = lambda: CurrentUser("history-one.myshopify.com")
    try:
        response = client.get("/api/seo/history")
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["action"] == "seo.preview"
    assert items[0]["details"]["product_title"] == "One"


def test_apply_reports_partial_success_when_image_alt_text_is_rejected(client, monkeypatch):
    from app.routers import seo
    from app.auth.dependencies import CurrentUser, get_current_shop

    class FakeClient:
        async def graphql(self, query, variables):
            return {
                "productUpdateMedia": {
                    "media": [],
                    "mediaUserErrors": [{"field": ["media"], "message": "Image alt text rejected"}],
                }
            }

    fake = FakeClient()
    events = []

    async def fake_shop(current, db):
        return SimpleNamespace(id=1, shop_domain="store.myshopify.com")

    async def fake_audit(*args, **kwargs):
        events.append(kwargs)
        return True

    async def fake_get_product(client, product_id):
        return {
            "id": product_id,
            "title": "Garden Kit",
            "handle": "garden-kit",
            "tags": [],
            "seo_title": "Garden Kit",
            "seo_description": "A garden kit",
            "images": [{"id": "gid://shopify/Media/1", "alt": "old alt"}],
        }

    client.app.dependency_overrides[get_current_shop] = lambda: CurrentUser("store.myshopify.com")
    monkeypatch.setattr(seo, "_shop", fake_shop)
    monkeypatch.setattr(seo, "_client", lambda shop: fake)
    monkeypatch.setattr(seo, "_get_product", fake_get_product)
    monkeypatch.setattr(seo, "record_audit_event", fake_audit)
    try:
        response = client.post("/api/seo/apply", json={
            "product_id": "gid://shopify/Product/1",
            "confirmed": True,
            "changes": {"image_alt_text": ["new alt"]},
        })
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    assert payload["partial_success"] is True
    assert payload["failed_fields"] == ["image_alt_text"]
    assert "could not be confirmed" in payload["message"]
    assert any(event.get("action") == "seo.partial_success" for event in events)


def test_get_product_filters_non_image_media():
    import asyncio
    from app.routers import seo

    class FakeClient:
        async def graphql(self, query, variables):
            assert "mediaContentType" in query
            return {"product": {
                "id": variables["id"], "title": "Trail Pack", "handle": "trail-pack",
                "descriptionHtml": "<p>Trail pack</p>", "productType": "Backpack", "vendor": "Example",
                "tags": [], "seo": {}, "media": {"nodes": [
                    {"id": "gid://shopify/MediaImage/1", "mediaContentType": "IMAGE", "alt": "", "preview": {"image": {"url": "https://cdn.example/1.jpg", "altText": ""}}},
                    {"id": "gid://shopify/Video/2", "mediaContentType": "VIDEO", "alt": None, "preview": {"image": None}},
                ]},
            }}

    product = asyncio.run(seo._get_product(FakeClient(), "gid://shopify/Product/1"))
    assert len(product["images"]) == 1
    assert product["images"][0]["id"] == "gid://shopify/MediaImage/1"


def test_shopify_rejection_uses_supported_http_status_constant(client, monkeypatch):
    from app.routers import seo
    from app.auth.dependencies import CurrentUser, get_current_shop
    from types import SimpleNamespace

    class FakeClient:
        async def update_product(self, product_id, changes):
            return {"productUpdate": {"product": {"id": product_id}, "userErrors": [{"message": "Rejected"}]}}

    client.app.dependency_overrides[get_current_shop] = lambda: CurrentUser("store.myshopify.com")
    async def fake_shop(current, db):
        return SimpleNamespace(id=1, shop_domain="store.myshopify.com")
    async def fake_audit(*args, **kwargs):
        return True
    async def fake_get_product(_client, product_id):
        return {"id": product_id, "title": "Existing", "images": []}
    monkeypatch.setattr(seo, "_shop", fake_shop)
    monkeypatch.setattr(seo, "_client", lambda shop: FakeClient())
    monkeypatch.setattr(seo, "record_audit_event", fake_audit)
    monkeypatch.setattr(seo, "_get_product", fake_get_product)
    try:
        response = client.post("/api/seo/apply", json={"product_id": "gid://shopify/Product/1", "confirmed": True, "changes": {"seo": {"title": "New SEO title"}}})
    finally:
        client.app.dependency_overrides.clear()
    assert response.status_code == 422
    assert "Shopify rejected" in response.json()["detail"]
