from app.services.analytics import (
    compute_aov,
    compute_dashboard,
    compute_recent_sales,
    compute_top_products,
    filter_orders_by_date,
    filter_orders_response,
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


def _orders(*created_at):
    return {"orders": {"edges": [
        {"node": {"id": f"gid://shopify/Order/{i}", "createdAt": c,
                  "totalPriceSet": {"shopMoney": {"amount": "10.00", "currencyCode": "USD"}}}}
        for i, c in enumerate(created_at, start=1)
    ]}}


def test_filter_orders_response_without_range_returns_input():
    data = _orders("2026-10-01T10:00:00Z")
    assert filter_orders_response(data) is data


def test_filter_orders_response_includes_whole_end_day():
    data = _orders("2026-09-30T23:00:00Z", "2026-10-01T09:00:00Z", "2026-10-16T21:14:00Z", "2026-10-17T00:30:00Z")

    filtered = filter_orders_response(data, start="2026-10-01", end="2026-10-16")

    ids = [edge["node"]["id"] for edge in filtered["orders"]["edges"]]
    assert ids == ["gid://shopify/Order/2", "gid://shopify/Order/3"]
    assert compute_dashboard(filtered, {})["revenue"] == 20.0


def test_dashboard_excludes_cancelled_and_unpaid_orders_from_net_sales():
    response = {"orders": {"edges": [
        {"node": {
            "id": "paid", "displayFinancialStatus": "PAID",
            "totalPriceSet": {"shopMoney": {"amount": "100.00", "currencyCode": "USD"}},
            "currentSubtotalPriceSet": {"shopMoney": {"amount": "80.00", "currencyCode": "USD"}},
        }},
        {"node": {
            "id": "pending", "displayFinancialStatus": "PENDING",
            "totalPriceSet": {"shopMoney": {"amount": "200.00", "currencyCode": "USD"}},
            "currentSubtotalPriceSet": {"shopMoney": {"amount": "200.00", "currencyCode": "USD"}},
        }},
        {"node": {
            "id": "cancelled", "displayFinancialStatus": "PAID", "cancelledAt": "2026-10-18T12:00:00Z",
            "totalPriceSet": {"shopMoney": {"amount": "50.00", "currencyCode": "USD"}},
            "currentSubtotalPriceSet": {"shopMoney": {"amount": "0.00", "currencyCode": "USD"}},
        }},
        {"node": {
            "id": "refunded", "displayFinancialStatus": "REFUNDED",
            "totalPriceSet": {"shopMoney": {"amount": "50.00", "currencyCode": "USD"}},
            "currentSubtotalPriceSet": {"shopMoney": {"amount": "0.00", "currencyCode": "USD"}},
        }},
    ]}}

    dashboard = compute_dashboard(response, customers_count=12)

    assert dashboard["revenue"] == 80.0
    assert dashboard["net_sales"] == 80.0
    assert dashboard["gross_order_value"] == 350.0
    assert dashboard["orders"] == 4
    assert dashboard["paid_orders"] == 1
    assert dashboard["unpaid_orders"] == 1
    assert dashboard["refunded_orders"] == 1
    assert dashboard["cancelled_orders"] == 1
    assert dashboard["customers"] == 12
    assert dashboard["average_order_value"] == 80.0


def test_dashboard_does_not_report_unavailable_customer_count_as_zero():
    response = {"orders": {"edges": []}}
    assert compute_dashboard(response)["customers"] is None


def test_top_products_uses_current_quantity_discounted_price_and_paid_orders_only():
    response = {"orders": {"edges": [
        {"node": {
            "id": "paid", "displayFinancialStatus": "PAID",
            "lineItems": {"edges": [{"node": {
                "title": "Garden Kit", "sku": "GK-1", "quantity": 3, "currentQuantity": 2,
                "originalUnitPriceSet": {"shopMoney": {"amount": "10.00", "currencyCode": "USD"}},
                "discountedUnitPriceSet": {"shopMoney": {"amount": "8.00", "currencyCode": "USD"}},
            }}]},
        }},
        {"node": {
            "id": "pending", "displayFinancialStatus": "PENDING",
            "lineItems": {"edges": [{"node": {
                "title": "Garden Kit", "sku": "GK-1", "quantity": 20,
                "originalUnitPriceSet": {"shopMoney": {"amount": "10.00", "currencyCode": "USD"}},
            }}]},
        }},
        {"node": {
            "id": "cancelled", "displayFinancialStatus": "PAID", "cancelledAt": "2026-10-18T12:00:00Z",
            "lineItems": {"edges": [{"node": {
                "title": "Garden Kit", "sku": "GK-1", "quantity": 99,
                "originalUnitPriceSet": {"shopMoney": {"amount": "10.00", "currencyCode": "USD"}},
            }}]},
        }},
    ]}}

    top = compute_top_products(response)

    assert len(top) == 1
    assert top[0]["units_sold"] == 2
    assert top[0]["revenue"] == 16.0


def test_filter_orders_by_date_includes_the_entire_end_date():
    response = _orders("2026-10-01T00:00:00Z", "2026-10-02T23:59:59Z", "2026-10-03T00:00:00Z")
    filtered = filter_orders_by_date(response, start="2026-10-02", end="2026-10-02")
    assert filtered["count"] == 1
