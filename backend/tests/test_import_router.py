from app.routers.imports import _is_shopify_validation_error, ImportCreateResponse
from app.shopify.client import ShopifyAPIError


def test_is_shopify_validation_error_detects_user_errors():
    assert _is_shopify_validation_error(
        ShopifyAPIError(
            "Shopify productCreate failed",
            status_code=200,
            response={"userErrors": [{"message": "Product options input is required"}]},
        )
    ) is True

    assert _is_shopify_validation_error(
        ShopifyAPIError(
            "upstream failed",
            status_code=502,
            response={"userErrors": [{"message": "Product options input is required"}]},
        )
    ) is False

    assert _is_shopify_validation_error(
        ShopifyAPIError("timeout", status_code=504, response={})
    ) is False

    assert _is_shopify_validation_error(
        ShopifyAPIError("shopify error", status_code=200, response={})
    ) is False


def test_import_create_response_carries_validation_error_fields():
    response = ImportCreateResponse(
        data=None,
        connected=True,
        error="Shopify productCreate failed",
        userErrors=[{"field": ["options"], "message": "Product options input is required"}],
    )

    assert response.connected is True
    assert response.error is not None
    assert response.userErrors is not None
    assert response.userErrors[0]["message"].startswith("Product options input is required")
