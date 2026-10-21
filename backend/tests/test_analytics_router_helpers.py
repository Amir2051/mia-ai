from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.models.schemas import Shop
from app.routers.analytics import _coverage_warning, _shopify_order_query


def test_shopify_order_query_pushes_date_bounds_to_shopify():
    assert _shopify_order_query("2026-10-01", "2026-10-02") == (
        "created_at:>='2026-10-01T00:00:00Z' "
        "created_at:<='2026-10-02T23:59:59Z'"
    )


def test_shopify_order_query_rejects_invalid_or_reversed_ranges():
    with pytest.raises(HTTPException) as invalid:
        _shopify_order_query("not-a-date", "2026-10-02")
    assert invalid.value.status_code == 422

    with pytest.raises(HTTPException) as reversed_range:
        _shopify_order_query("2026-10-03", "2026-10-02")
    assert reversed_range.value.status_code == 422


def test_coverage_warning_explains_shopify_sixty_day_order_limit():
    shop = Shop(
        shop_domain="analytics-coverage-test.myshopify.com",
        access_token_encrypted="placeholder",
        scope="read_orders",
        is_active=True,
    )
    warning = _coverage_warning(shop, None, None, True)
    assert warning is not None
    assert "most recent 60 days" in warning
    assert "read_all_orders" in warning


def test_recent_range_without_all_orders_scope_does_not_show_old_history_warning():
    shop = Shop(
        shop_domain="analytics-recent-coverage-test.myshopify.com",
        access_token_encrypted="placeholder",
        scope="read_orders",
        is_active=True,
    )
    recent_start = (datetime.now(timezone.utc).date() - timedelta(days=7)).isoformat()
    assert _coverage_warning(shop, recent_start, None, True) is None


def test_full_order_scope_avoids_sixty_day_warning():
    shop = Shop(
        shop_domain="analytics-full-coverage-test.myshopify.com",
        access_token_encrypted="placeholder",
        scope="read_orders,read_all_orders",
        is_active=True,
    )
    assert _coverage_warning(shop, None, None, True) is None
