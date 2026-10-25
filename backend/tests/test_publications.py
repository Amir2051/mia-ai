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


def test_list_publications_query_uses_2026_07_fields():
    """Regression guard: the query must not request the removed Publication.name."""
    import asyncio
    from app.shopify.client import ShopifyAPIClient

    captured = {}
    api = ShopifyAPIClient(shop_domain="demo.myshopify.com", access_token="t")

    async def fake_graphql(query, variables=None, **kwargs):
        captured["query"] = query
        return {"publications": {"edges": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}

    api.graphql = fake_graphql
    asyncio.run(api.list_publications())
    q = captured["query"]
    assert "catalog" in q and "title" in q
    assert "node { id name" not in q


def test_channel_from_node_maps_catalog_title_to_name():
    from app.routers.publications import _channel_from_node

    node = {
        "id": "gid://shopify/Publication/1",
        "supportsFuturePublishing": True,
        "catalog": {"title": "Online Store"},
    }
    assert _channel_from_node(node) == {
        "id": "gid://shopify/Publication/1",
        "name": "Online Store",
        "supportsFuturePublishing": True,
    }


def test_channel_from_node_handles_null_or_missing_catalog():
    from app.routers.publications import _channel_from_node

    assert _channel_from_node({"id": "gid://shopify/Publication/2", "catalog": None})["name"] == "Unnamed channel"
    assert _channel_from_node({"id": "gid://shopify/Publication/3"})["name"] == "Unnamed channel"
    assert _channel_from_node({"id": "x"})["supportsFuturePublishing"] is False
    assert set(_channel_from_node({"id": "x"}).keys()) == {"id", "name", "supportsFuturePublishing"}


def test_shopify_error_generic_graphql_error_maps_to_502():
    error = ShopifyAPIError(
        "Shopify GraphQL error",
        status_code=200,
        response={"errors": [{"message": "Something upstream failed"}]},
    )
    try:
        _shopify_error(error)
        raised = None
    except HTTPException as exc:
        raised = exc
    assert raised is not None and raised.status_code == 502
