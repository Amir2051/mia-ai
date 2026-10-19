import pytest

from app.services.orders import OrderService


PROTECTED_CUSTOMER_FIELDS = ("email", "shippingAddress", "customer {", "customer{")


class RecordingClient:
    def __init__(self):
        self.calls = []

    async def graphql(self, query, variables=None):
        self.calls.append((query, variables))
        return {"order": None}


def test_default_login_url_uses_validated_shopify_domain(client, monkeypatch):
    from app.shopify.config import settings

    monkeypatch.setattr(settings, "shopify_api_key", "public-client-id")
    response = client.get("/api/auth/login-url", params={"shop": "demo-store.myshopify.com"})

    assert response.status_code == 200
    assert response.json() == {
        "shop_domain": "demo-store.myshopify.com",
        "login_url": "https://demo-store.myshopify.com/admin/apps/public-client-id",
    }


def test_default_login_url_accepts_shopify_admin_store_handle(client, monkeypatch):
    from app.shopify.config import settings

    monkeypatch.setattr(settings, "shopify_api_key", "public-client-id")
    response = client.get("/api/auth/login-url", params={"shop": "shopify.com/store/safenestt"})

    assert response.status_code == 200
    assert response.json() == {
        "shop_domain": "safenestt.myshopify.com",
        "login_url": "https://safenestt.myshopify.com/admin/apps/public-client-id",
    }


def test_default_login_url_accepts_admin_shopify_full_url(client, monkeypatch):
    from app.shopify.config import settings

    monkeypatch.setattr(settings, "shopify_api_key", "public-client-id")
    response = client.get("/api/auth/login-url", params={"shop": "https://admin.shopify.com/store/safenestt"})

    assert response.status_code == 200
    assert response.json()["shop_domain"] == "safenestt.myshopify.com"


def test_default_login_url_rejects_non_shopify_hosts(client):
    response = client.get("/api/auth/login-url", params={"shop": "attacker.example"})

    assert response.status_code == 422


def test_social_configuration_requires_shopify_session(client):
    response = client.get("/api/social/configuration")

    assert response.status_code == 401


def test_order_detail_route_accepts_shopify_gid_with_slashes(client):
    # Shopify order ids look like gid://shopify/Order/123. Without a :path
    # converter the route 404s before the handler runs.
    response = client.get("/api/orders/gid://shopify/Order/123")

    assert response.status_code == 200
    assert response.json()["connected"] is False


@pytest.mark.asyncio
async def test_get_order_requests_fields_the_ui_renders():
    api = RecordingClient()

    await OrderService(api).get_order("gid://shopify/Order/123")

    query, variables = api.calls[0]
    assert variables == {"id": "gid://shopify/Order/123"}
    for field in ("createdAt", "displayFinancialStatus", "displayFulfillmentStatus", "totalPriceSet"):
        assert field in query


@pytest.mark.asyncio
async def test_get_order_avoids_protected_customer_fields():
    api = RecordingClient()

    await OrderService(api).get_order("gid://shopify/Order/123")

    query, _ = api.calls[0]
    for field in PROTECTED_CUSTOMER_FIELDS:
        assert field not in query


def test_product_update_route_accepts_shopify_gid_with_slashes(client):
    # Before the :path converter this returned 405 for every real product id,
    # so product edits from the app never reached Shopify.
    response = client.put("/api/products/gid://shopify/Product/101", json={"product": {"title": "New"}})

    assert response.status_code == 401


class InventoryClient:
    def __init__(self, locations):
        self.locations = locations
        self.mutations = []

    async def graphql(self, query, variables=None):
        if "productUpdate" in query:
            return {"productUpdate": {"product": {"id": "gid://shopify/Product/1"}, "userErrors": []}}
        if "productVariant(id" in query:
            return {"productVariant": {"id": variables["id"], "inventoryItem": {
                "id": "gid://shopify/InventoryItem/9",
                "inventoryLevels": {"nodes": [{"location": {"id": loc}} for loc in self.locations]},
            }}}
        if "inventorySetQuantities" in query:
            self.mutations.append(variables)
            return {"inventorySetQuantities": {"userErrors": []}}
        raise AssertionError(query)


async def _set_quantity(client, quantity=30):
    from app.services.products import ProductService

    await ProductService(client).update_product(
        "gid://shopify/Product/1",
        {"title": "T", "variants": [{"id": "gid://shopify/ProductVariant/5", "inventoryQuantity": quantity}]},
    )


@pytest.mark.asyncio
async def test_inventory_edit_sets_single_location():
    client = InventoryClient(["gid://shopify/Location/1"])

    await _set_quantity(client)

    quantities = client.mutations[0]["input"]["quantities"]
    assert quantities == [{"inventoryItemId": "gid://shopify/InventoryItem/9", "locationId": "gid://shopify/Location/1", "quantity": 30}]


@pytest.mark.asyncio
async def test_inventory_edit_refuses_to_multiply_across_locations():
    from app.shopify.client import ShopifyAPIError

    client = InventoryClient(["gid://shopify/Location/1", "gid://shopify/Location/2"])

    with pytest.raises(ShopifyAPIError, match="2 locations"):
        await _set_quantity(client)
    assert client.mutations == []
