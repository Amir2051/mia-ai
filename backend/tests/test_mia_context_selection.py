import asyncio
from types import SimpleNamespace

from app.routers import mia


def test_revenue_question_fetches_orders_only_and_marks_other_data_unqueried(monkeypatch):
    shop = SimpleNamespace(shop_domain="mia-context-selection.myshopify.com")
    calls = []

    async def forbidden_products(*args, **kwargs):
        raise AssertionError("Revenue questions should not fetch the full product catalog")

    class FakeOrderService:
        def __init__(self, client):
            calls.append("orders")

        async def list_orders(self, query="", first=250, after=None):
            return {
                "orders": {
                    "edges": [{
                        "node": {
                            "id": "gid://shopify/Order/1",
                            "name": "#1001",
                            "createdAt": "2026-10-20T12:00:00Z",
                            "totalPriceSet": {"shopMoney": {"amount": "12.50", "currencyCode": "USD"}},
                            "displayFinancialStatus": "PAID",
                            "displayFulfillmentStatus": "FULFILLED",
                            "lineItems": {"edges": [{"node": {"title": "Blue Mug", "quantity": 1, "sku": "MUG-1"}}]},
                        }
                    }],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }

    class ForbiddenCustomerService:
        def __init__(self, client):
            raise AssertionError("Revenue questions should not fetch customer profiles")

    class FakeClient:
        async def list_publications(self, first=100):
            raise AssertionError("Revenue questions should not fetch sales channels")

    monkeypatch.setattr(mia, "_all_products", forbidden_products)
    monkeypatch.setattr(mia, "OrderService", FakeOrderService)
    monkeypatch.setattr(mia, "CustomerService", ForbiddenCustomerService)
    monkeypatch.setattr(mia, "_client", lambda _shop: FakeClient())

    context = asyncio.run(mia._context(shop, "What is my store revenue?"))

    assert calls == ["orders"]
    assert context["requested"] == {
        "products": False, "orders": True, "customers": False, "publications": False
    }
    assert context["available"] == {
        "products": False, "orders": True, "customers": False, "publications": False
    }
    assert context["revenue"] == 12.5
    assert context["counts"]["orders"] == 1
    assert context["counts"]["products"] is None
    assert context["counts"]["customers"] is None

    facts = mia._store_facts(context, None)
    assert "Orders loaded: 1" in facts
    assert "Product catalog was not queried" in facts
    assert "Customer records were not queried" in facts
    assert "Sales channels were not queried" in facts


def test_product_count_question_does_not_fetch_orders_customers_or_channels(monkeypatch):
    shop = SimpleNamespace(shop_domain="mia-product-context-selection.myshopify.com")

    async def fake_products(_shop, warnings=None):
        return [{
            "id": "gid://shopify/Product/1",
            "title": "Blue Mug",
            "status": "ACTIVE",
            "totalInventory": 4,
            "descriptionHtml": "<p>A blue mug</p>",
            "variants": {"edges": [{"node": {"sku": "MUG-1", "price": "12.50", "inventoryQuantity": 4}}]},
            "tags": ["mug"],
            "vendor": "Mia",
            "productType": "Drinkware",
            "createdAt": "2026-10-01T00:00:00Z",
            "updatedAt": "2026-10-02T00:00:00Z",
        }]

    class ForbiddenOrderService:
        def __init__(self, client):
            raise AssertionError("Product-count questions should not fetch orders")

    class ForbiddenCustomerService:
        def __init__(self, client):
            raise AssertionError("Product-count questions should not fetch customer profiles")

    class FakeClient:
        async def list_publications(self, first=100):
            raise AssertionError("Product-count questions should not fetch sales channels")

    monkeypatch.setattr(mia, "_all_products", fake_products)
    monkeypatch.setattr(mia, "OrderService", ForbiddenOrderService)
    monkeypatch.setattr(mia, "CustomerService", ForbiddenCustomerService)
    monkeypatch.setattr(mia, "_client", lambda _shop: FakeClient())

    context = asyncio.run(mia._context(shop, "How many products do I have?"))

    assert context["requested"] == {
        "products": True, "orders": False, "customers": False, "publications": False
    }
    assert context["available"]["products"] is True
    assert context["counts"]["products"] == 1
    assert context["counts"]["inventory_units"] == 4
    assert context["counts"]["orders"] is None
    assert context["revenue"] is None
    assert context["sales_channels"] == []


def test_date_specific_sales_query_uses_store_timezone_and_filters_orders(monkeypatch):
    shop = SimpleNamespace(
        shop_domain="mia-date-context-selection.myshopify.com",
        scope="read_orders",
    )
    captured = {}

    class FakeClient:
        async def graphql(self, query):
            assert "ianaTimezone" in query
            return {"shop": {"ianaTimezone": "America/New_York"}}

    class FakeOrderService:
        def __init__(self, client):
            pass

        async def list_orders(self, query="", first=250, after=None):
            captured["query"] = query
            return {"orders": {"edges": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}

    async def forbidden_products(*args, **kwargs):
        raise AssertionError("Date-specific sales questions should not load products")

    monkeypatch.setattr(mia, "_client", lambda _shop: FakeClient())
    monkeypatch.setattr(mia, "OrderService", FakeOrderService)
    monkeypatch.setattr(mia, "CustomerService", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Should not load customers")))
    monkeypatch.setattr(mia, "_all_products", forbidden_products)

    context = asyncio.run(mia._context(shop, "How much sales did I make yesterday?"))

    assert captured["query"].startswith("created_at:>='")
    assert "created_at:<' " not in captured["query"]
    assert "created_at:<'" in captured["query"]
    assert context["order_range"].startswith("yesterday, ")
    assert context["available"]["orders"] is True
    assert context["requested"]["products"] is False


def test_ai_fallback_answers_sales_from_orders_without_inventing_product_count():
    context = {
        "shop": "mia-fallback.myshopify.com",
        "requested": {"products": False, "orders": True, "customers": False, "publications": False},
        "available": {"products": False, "orders": True, "customers": False, "publications": False},
        "products": [],
        "orders": [{"amount": "12.50", "currency": "USD"}],
        "revenue": 12.5,
        "warnings": ["No date range was requested. Totals may cover only recent history."],
    }

    answer = mia._rule_answer("What is my store revenue?", context)

    assert "USD 12.50" in answer
    assert "not net sales" in answer
    assert "0 products" not in answer
    assert "Coverage note" in answer


def test_ai_fallback_does_not_report_unavailable_orders_as_zero():
    context = {
        "shop": "mia-fallback.myshopify.com",
        "requested": {"products": False, "orders": True, "customers": False, "publications": False},
        "available": {"products": False, "orders": False, "customers": False, "publications": False},
        "products": [],
        "orders": [],
        "revenue": None,
        "warnings": ["Order data could not be loaded."],
    }

    answer = mia._rule_answer("What is my store revenue?", context)

    assert "couldn't retrieve order data" in answer
    assert "0.00" not in answer


def test_mia_context_endpoint_selects_only_dashboard_datasets_by_default(client, monkeypatch):
    from app.auth.dependencies import CurrentUser, get_optional_shop

    calls = []

    async def authenticated_shop():
        return CurrentUser(shop_domain="mia-context-endpoint-test.myshopify.com")

    async def fake_shop(current, db):
        return SimpleNamespace(shop_domain=current.shop_domain)

    async def fake_context(shop, message="", selected_id=None):
        calls.append(message)
        return {"shop": shop.shop_domain, "requested": mia._context_plan(message, selected_id)}

    monkeypatch.setattr(mia, "_shop", fake_shop)
    monkeypatch.setattr(mia, "_context", fake_context)
    client.app.dependency_overrides[get_optional_shop] = authenticated_shop
    try:
        dashboard = client.get("/api/mia/context")
        mia_snapshot = client.get("/api/mia/context?include_orders=true")
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)

    assert dashboard.status_code == 200, dashboard.text
    assert mia_snapshot.status_code == 200, mia_snapshot.text
    assert calls == ["products inventory", "products inventory orders"]
    assert dashboard.json()["requested"] == {
        "products": True, "orders": False, "customers": False, "publications": False
    }
    assert mia_snapshot.json()["requested"] == {
        "products": True, "orders": True, "customers": False, "publications": False
    }


def test_ai_fallback_calculates_units_sold_from_line_items_before_revenue_fallback():
    context = {
        "shop": "mia-fallback.myshopify.com",
        "requested": {"products": True, "orders": True, "customers": False, "publications": False},
        "available": {"products": True, "orders": True, "customers": False, "publications": False},
        "products": [{"title": "Blue Mug"}],
        "orders": [
            {"line_items": [{"title": "Blue Mug", "quantity": 3}]},
            {"line_items": [{"title": "Red Mug", "quantity": 1}]},
        ],
        "revenue": 40.0,
        "order_range": "last 7 rolling days (America/New_York)",
        "warnings": [],
    }

    answer = mia._rule_answer("Which products sold the most?", context)

    assert "4 units sold" in answer
    assert "Blue Mug (3)" in answer
    assert "Red Mug (1)" in answer
    assert "USD 40" not in answer
