from app.services.analytics import (
    compute_aov,
    compute_dashboard,
    compute_recent_sales,
    compute_top_products,
    filter_orders_by_date,
)


def test_compute_dashboard_returns_totals():
    orders_response = {
        "orders": {
            "edges": [
                {"node": {"id": "gid://shopify/Order/1", "name": "Order 1", "createdAt": "2026-09-01T00:00:00Z", "totalPriceSet": {"shopMoney": {"amount": "10.00", "currencyCode": "USD"}}}}
            ]
        },
        "customers": {
            "edges": [
                {"node": {"id": "gid://shopify/Customer/1", "displayName": "Customer A", "numberOfOrders": 1}}
            ]
        },
    }
    dashboard = compute_dashboard(orders_response, orders_response)
    assert dashboard["orders"] == 1
    assert dashboard["customers"] == 1
    assert dashboard["revenue"] == 10.0
    assert dashboard["average_order_value"] == 10.0


def test_compute_dashboard_accepts_empty_customer_response():
    orders_response = {
        "orders": {
            "edges": [
                {"node": {"id": "gid://shopify/Order/1", "totalPriceSet": {"shopMoney": {"amount": "12.50", "currencyCode": "USD"}}}}
            ]
        }
    }
    dashboard = compute_dashboard(orders_response, {})
    assert dashboard["orders"] == 1
    assert dashboard["customers"] == 0
    assert dashboard["revenue"] == 12.5
    assert dashboard["average_order_value"] == 12.5


def test_compute_top_products_ranks_by_units_and_revenue():
    orders_response = {"orders": {"edges": [
        {"node": {"id": "gid://shopify/Order/1", "lineItems": {"edges": [{"node": {"title": "Dress", "sku": "SKU-1", "quantity": 2, "originalUnitPriceSet": {"shopMoney": {"amount": "20.00", "currencyCode": "USD"}}}}]}}},
        {"node": {"id": "gid://shopify/Order/2", "lineItems": {"edges": [{"node": {"title": "Dress", "sku": "SKU-1", "quantity": 1, "originalUnitPriceSet": {"shopMoney": {"amount": "20.00", "currencyCode": "USD"}}}}]}}},
    ]}}
    top_products = compute_top_products(orders_response)
    assert len(top_products) == 1
    assert top_products[0]["title"] == "Dress"
    assert top_products[0]["sku"] == "SKU-1"
    assert top_products[0]["units_sold"] == 3
    assert top_products[0]["revenue"] == 60.0


def test_compute_recent_sales_orders_by_date():
    orders_response = {"orders": {"edges": [
        {"node": {"id": "gid://shopify/Order/1", "name": "Order 1", "createdAt": "2026-09-01T01:00:00Z", "totalPriceSet": {"shopMoney": {"amount": "5.00", "currencyCode": "USD"}}}},
        {"node": {"id": "gid://shopify/Order/2", "name": "Order 2", "createdAt": "2026-09-02T02:00:00Z", "totalPriceSet": {"shopMoney": {"amount": "15.00", "currencyCode": "USD"}}}},
    ]}}
    recent = compute_recent_sales(orders_response, limit=2)
    assert len(recent) == 2
    assert recent[0]["name"] == "Order 2"
    assert recent[1]["name"] == "Order 1"
    assert recent[0]["amount"] == 15.0
    assert recent[1]["amount"] == 5.0


def test_compute_aov_returns_average():
    orders_response = {"orders": {"edges": [
        {"node": {"id": "gid://shopify/Order/1", "totalPriceSet": {"shopMoney": {"amount": "10.00", "currencyCode": "USD"}}}},
        {"node": {"id": "gid://shopify/Order/2", "totalPriceSet": {"shopMoney": {"amount": "30.00", "currencyCode": "USD"}}}},
    ]}}
    assert compute_aov(orders_response) == 20.0


def test_compute_aov_returns_none_when_empty():
    assert compute_aov({"orders": {"edges": []}}) is None


def test_filter_orders_by_date_limits_range():
    orders_response = {"orders": {"edges": [
        {"node": {"id": "gid://shopify/Order/1", "createdAt": "2026-09-01T00:00:00Z", "totalPriceSet": {"shopMoney": {"amount": "5.00", "currencyCode": "USD"}}}},
        {"node": {"id": "gid://shopify/Order/2", "createdAt": "2026-09-03T00:00:00Z", "totalPriceSet": {"shopMoney": {"amount": "15.00", "currencyCode": "USD"}}}},
    ]}}
    filtered = filter_orders_by_date(orders_response, start="2026-09-02T00:00:00Z", end="2026-09-02T23:59:59Z")
    assert filtered["count"] == 0
