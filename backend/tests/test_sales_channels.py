import pytest

from app.shopify.client import ShopifyAPIClient, ShopifyAPIError


class FakePublicationClient(ShopifyAPIClient):
    def __init__(self, response):
        super().__init__("test.myshopify.com", "test-token")
        self.response = response
        self.calls = []

    async def graphql(self, query, variables=None):
        self.calls.append((query, variables))
        return self.response


@pytest.mark.asyncio
async def test_list_publications_forwards_pagination():
    response = {"publications": {"edges": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}
    client = FakePublicationClient(response)

    assert await client.list_publications(first=25, after="cursor-1") == response
    query, variables = client.calls[0]
    assert "publications(first: $first, after: $after)" in query
    assert variables == {"first": 25, "after": "cursor-1"}


@pytest.mark.asyncio
async def test_publish_product_to_selected_channels():
    response = {"publishablePublish": {"userErrors": [], "publishable": {"id": "gid://shopify/Product/123"}}}
    client = FakePublicationClient(response)

    result = await client.set_product_publications(
        "gid://shopify/Product/123",
        ["gid://shopify/Publication/1", "gid://shopify/Publication/2"],
    )

    assert result == response
    query, variables = client.calls[0]
    assert "publishablePublish" in query
    assert variables == {
        "id": "gid://shopify/Product/123",
        "input": [
            {"publicationId": "gid://shopify/Publication/1"},
            {"publicationId": "gid://shopify/Publication/2"},
        ],
    }


@pytest.mark.asyncio
async def test_unpublish_product_uses_unpublish_mutation():
    response = {"publishableUnpublish": {"userErrors": []}}
    client = FakePublicationClient(response)

    await client.set_product_publications(
        "gid://shopify/Product/123",
        ["gid://shopify/Publication/1"],
        publish=False,
    )

    assert "publishableUnpublish" in client.calls[0][0]


@pytest.mark.asyncio
async def test_publication_mutation_validates_ids_and_user_errors():
    client = FakePublicationClient({"publishablePublish": {"userErrors": []}})
    with pytest.raises(ValueError, match="product_id must be a Shopify Product GID"):
        await client.set_product_publications("123", ["gid://shopify/Publication/1"])
    with pytest.raises(ValueError, match="Select at least one sales channel"):
        await client.set_product_publications("gid://shopify/Product/123", [])
    with pytest.raises(ValueError, match="publication_ids must contain Shopify Publication GIDs"):
        await client.set_product_publications("gid://shopify/Product/123", ["bad-id"])

    client.response = {"publishablePublish": {"userErrors": [{"field": ["input"], "message": "Publication unavailable"}]}}
    with pytest.raises(ShopifyAPIError, match="Shopify publishablePublish failed"):
        await client.set_product_publications("gid://shopify/Product/123", ["gid://shopify/Publication/1"])


def test_sales_channel_list_requires_shopify_session(client):
    response = client.get("/api/products/sales-channels")
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing Shopify session"
