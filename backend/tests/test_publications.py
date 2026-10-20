from fastapi.testclient import TestClient
from fastapi import HTTPException

from app.routers.publications import _shopify_error
from app.shopify.client import ShopifyAPIError


def test_sales_channels_endpoint_requires_shopify_session(client: TestClient):
    response = client.get("/api/sales-channels")
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing Shopify session"


def test_sales_channels_permission_errors_are_actionable():
    error = ShopifyAPIError(
        "Shopify GraphQL error",
        status_code=200,
        response={"errors": [{"message": "Access denied for publications field. Requires read_publications scope."}]},
    )
    mapped = _shopify_error(error)
    assert isinstance(mapped, HTTPException)
    assert mapped.status_code == 403
    assert "read_publications" in mapped.detail
