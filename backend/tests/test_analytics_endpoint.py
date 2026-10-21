import asyncio

from sqlalchemy import select


def test_analytics_endpoint_returns_paid_net_sales_and_pushes_date_filter(client, monkeypatch):
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import Shop

    shop_domain = "mia-analytics-endpoint-test.myshopify.com"

    async def seed_shop():
        async with AsyncSessionLocal() as db:
            shop = (await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))).scalar_one_or_none()
            if shop is None:
                db.add(Shop(
                    shop_domain=shop_domain,
                    access_token_encrypted="test-placeholder",
                    scope="read_orders",
                    is_active=True,
                ))
                await db.commit()

    asyncio.run(seed_shop())

    async def authenticated_shop():
        return CurrentUser(shop_domain=shop_domain)

    async def fake_load_orders(shop, query=""):
        assert shop.shop_domain == shop_domain
        assert "created_at:>='2026-10-01T00:00:00Z'" in query
        assert "created_at:<='2026-10-02T23:59:59Z'" in query
        return {"orders": {"edges": [
            {"node": {
                "id": "paid", "name": "#1001", "createdAt": "2026-10-01T12:00:00Z",
                "displayFinancialStatus": "PAID",
                "totalPriceSet": {"shopMoney": {"amount": "120.00", "currencyCode": "USD"}},
                "currentSubtotalPriceSet": {"shopMoney": {"amount": "90.00", "currencyCode": "USD"}},
                "lineItems": {"edges": [{"node": {
                    "title": "Garden Kit", "sku": "GK-1", "quantity": 2, "currentQuantity": 2,
                    "discountedUnitPriceSet": {"shopMoney": {"amount": "45.00", "currencyCode": "USD"}},
                }}]},
            }},
            {"node": {
                "id": "pending", "name": "#1002", "createdAt": "2026-10-02T12:00:00Z",
                "displayFinancialStatus": "PENDING",
                "totalPriceSet": {"shopMoney": {"amount": "50.00", "currencyCode": "USD"}},
                "currentSubtotalPriceSet": {"shopMoney": {"amount": "50.00", "currencyCode": "USD"}},
            }},
        ]}}, True

    async def fake_customer_count(shop):
        return 17, "EXACT"

    monkeypatch.setattr("app.routers.analytics._load_orders", fake_load_orders)
    monkeypatch.setattr("app.routers.analytics._load_customer_count", fake_customer_count)
    client.app.dependency_overrides[get_optional_shop] = authenticated_shop
    try:
        response = client.get("/api/analytics/?start=2026-10-01&end=2026-10-02")
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["net_sales"] == 90.0
    assert payload["revenue"] == 90.0
    assert payload["gross_order_value"] == 170.0
    assert payload["orders"] == 2
    assert payload["paid_orders"] == 1
    assert payload["unpaid_orders"] == 1
    assert payload["customers"] == 17
    assert payload["customer_count_precision"] == "EXACT"
    assert payload["top_products"][0]["units_sold"] == 2
    assert payload["top_products"][0]["revenue"] == 90.0
    assert payload["coverage_warning"] is None


def test_analytics_endpoint_rejects_reversed_date_range_before_shopify_request(client, monkeypatch):
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import Shop

    shop_domain = "mia-analytics-invalid-range-test.myshopify.com"

    async def seed_shop():
        async with AsyncSessionLocal() as db:
            shop = (await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))).scalar_one_or_none()
            if shop is None:
                db.add(Shop(
                    shop_domain=shop_domain,
                    access_token_encrypted="test-placeholder",
                    scope="read_orders",
                    is_active=True,
                ))
                await db.commit()

    asyncio.run(seed_shop())

    async def authenticated_shop():
        return CurrentUser(shop_domain=shop_domain)

    async def forbidden_load(*args, **kwargs):
        raise AssertionError("Invalid date ranges must be rejected before Shopify is queried")

    monkeypatch.setattr("app.routers.analytics._load_orders", forbidden_load)
    client.app.dependency_overrides[get_optional_shop] = authenticated_shop
    try:
        response = client.get("/api/analytics/?start=2026-10-03&end=2026-10-02")
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)

    assert response.status_code == 422
    assert "start date" in response.json()["detail"]


def test_analytics_endpoint_exposes_period_comparison_for_default_dashboard(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.auth.dependencies import CurrentUser, get_optional_shop
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import Shop

    shop_domain = "mia-period-comparison-test.myshopify.com"
    now = datetime.now(timezone.utc)
    current_date = (now - timedelta(days=5)).isoformat()
    previous_date = (now - timedelta(days=40)).isoformat()

    async def seed_shop():
        async with AsyncSessionLocal() as db:
            shop = (await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))).scalar_one_or_none()
            if shop is None:
                db.add(Shop(
                    shop_domain=shop_domain,
                    access_token_encrypted="test-placeholder",
                    scope="read_orders,read_all_orders",
                    is_active=True,
                ))
                await db.commit()

    asyncio.run(seed_shop())

    async def authenticated_shop():
        return CurrentUser(shop_domain=shop_domain)

    async def fake_load_orders(shop, query=""):
        assert query == ""
        return {"orders": {"edges": [
            {"node": {
                "id": "current", "createdAt": current_date, "displayFinancialStatus": "PAID",
                "totalPriceSet": {"shopMoney": {"amount": "100.00", "currencyCode": "USD"}},
                "currentSubtotalPriceSet": {"shopMoney": {"amount": "80.00", "currencyCode": "USD"}},
            }},
            {"node": {
                "id": "previous", "createdAt": previous_date, "displayFinancialStatus": "PAID",
                "totalPriceSet": {"shopMoney": {"amount": "50.00", "currencyCode": "USD"}},
                "currentSubtotalPriceSet": {"shopMoney": {"amount": "45.00", "currencyCode": "USD"}},
            }},
        ]}}, True

    async def fake_customer_count(shop):
        return 1, "EXACT"

    monkeypatch.setattr("app.routers.analytics._load_orders", fake_load_orders)
    monkeypatch.setattr("app.routers.analytics._load_customer_count", fake_customer_count)
    client.app.dependency_overrides[get_optional_shop] = authenticated_shop
    try:
        response = client.get("/api/analytics/")
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)

    assert response.status_code == 200, response.text
    comparison = response.json()["period_comparison"]
    assert comparison["period_days"] == 30
    assert comparison["timezone"] == "UTC"
    assert comparison["current"]["orders"] == 1
    assert comparison["previous"]["orders"] == 1
    assert comparison["current"]["net_sales"] == 80.0
    assert comparison["previous"]["net_sales"] == 45.0
    assert comparison["change_percent"]["net_sales"] == 77.8
