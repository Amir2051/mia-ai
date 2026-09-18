from fastapi import HTTPException


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
        return object()
    monkeypatch.setattr(seo, "_shop", fake_shop)
    monkeypatch.setattr(seo, "_client", lambda shop: fake)
    async def fake_get_product(client, product_id):
        return {"id": product_id, "title": "New"}
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
        return object()
    monkeypatch.setattr(seo, "_shop", fake_shop)
    monkeypatch.setattr(seo, "_client", lambda shop: fake)
    async def fake_get_product(client, product_id):
        return {"id": product_id, "title": "New"}
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
