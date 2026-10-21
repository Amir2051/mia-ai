from app.shopify.client import ShopifyAPIError
from app.shopify.http_errors import shopify_http_error


def test_expired_shopify_token_maps_to_retryable_401():
    error = shopify_http_error(ShopifyAPIError("Shopify API error", status_code=401))

    assert error.status_code == 401
    assert error.headers["X-Shopify-Retry-Invalid-Session-Request"] == "1"
    assert "credentials expired or were revoked" in error.detail


def test_graphql_access_denied_maps_to_permission_error():
    error = shopify_http_error(
        ShopifyAPIError(
            "Shopify GraphQL error",
            status_code=200,
            response={
                "errors": [
                    {
                        "message": "Access denied for publications field.",
                        "extensions": {"code": "ACCESS_DENIED"},
                    }
                ]
            },
        )
    )

    assert error.status_code == 403
    assert "granted permissions" in error.detail


def test_transient_shopify_failure_maps_to_safe_gateway_error():
    error = shopify_http_error(
        ShopifyAPIError("timeout details should not be exposed", status_code=504),
        fallback_detail="Custom safe fallback",
    )

    assert error.status_code == 502
    assert error.detail == "Custom safe fallback"
    assert "timeout details" not in error.detail
