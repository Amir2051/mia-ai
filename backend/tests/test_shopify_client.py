import base64
import hashlib
import hmac

import pytest

from app.services.products import ProductService
from app.shopify.client import ShopifyAPIClient, ShopifyAPIError
from app.shopify.config import settings


class FakeGraphQLClient(ShopifyAPIClient):
    def __init__(self):
        super().__init__("test.myshopify.com", "test-token")

    async def graphql(self, query, variables=None):
        return {
            "productCreate": {
                "product": {
                    "id": "gid://shopify/Product/123",
                    "handle": "test-product",
                },
                "userErrors": [],
            }
        }


class FakeUpdateGraphQLClient(ShopifyAPIClient):
    def __init__(self):
        super().__init__("test.myshopify.com", "test-token")

    async def graphql(self, query, variables=None):
        return {
            "productUpdate": {
                "product": {
                    "id": "gid://shopify/Product/123",
                    "handle": "updated-product",
                },
                "userErrors": [],
            }
        }


class FakeUserErrorCreateClient(ShopifyAPIClient):
    def __init__(self):
        super().__init__("test.myshopify.com", "test-token")

    async def graphql(self, query, variables=None):
        return {
            "productCreate": {
                "product": None,
                "userErrors": [
                    {
                        "field": ["title"],
                        "message": "Title is invalid",
                    }
                ],
            }
        }


class FakeUserErrorUpdateClient(ShopifyAPIClient):
    def __init__(self):
        super().__init__("test.myshopify.com", "test-token")

    async def graphql(self, query, variables=None):
        return {
            "productUpdate": {
                "product": None,
                "userErrors": [
                    {
                        "field": ["title"],
                        "message": "Title is invalid",
                    }
                ],
            }
        }


class FakePaginationClient(ShopifyAPIClient):
    def __init__(self):
        super().__init__("test.myshopify.com", "test-token")

    async def graphql(self, query, variables=None):
        return {
            "products": {
                "edges": [
                    {
                        "cursor": "cursor-1",
                        "node": {
                            "id": "gid://shopify/Product/1",
                            "title": "Product One",
                            "handle": "product-one",
                            "status": "ACTIVE",
                            "totalInventory": 10,
                            "variants": {
                                "nodes": [
                                    {
                                        "id": (
                                            "gid://shopify/"
                                            "ProductVariant/1"
                                        ),
                                        "title": "Default Title",
                                        "sku": "SKU-1",
                                        "price": "25.00",
                                        "inventoryQuantity": 10,
                                    }
                                ]
                            },
                        },
                    }
                ],
                "pageInfo": {
                    "hasNextPage": True,
                    "endCursor": "cursor-1",
                },
            }
        }


class FakeGraphQLErrorClient(ShopifyAPIClient):
    def __init__(self):
        super().__init__("test.myshopify.com", "test-token")

    async def graphql(self, query, variables=None):
        raise ShopifyAPIError(
            "Shopify GraphQL error",
            status_code=200,
            response={
                "errors": [
                    {
                        "message": "Something went wrong"
                    }
                ]
            },
        )


@pytest.mark.asyncio
async def test_create_product():
    client = FakeGraphQLClient()

    result = await client.create_product(
        {
            "title": "Test Product",
            "descriptionHtml": "<p>Test</p>",
        }
    )

    assert result["productCreate"]["product"]["id"] == (
        "gid://shopify/Product/123"
    )
    assert result["productCreate"]["product"]["handle"] == (
        "test-product"
    )


@pytest.mark.asyncio
async def test_update_product():
    client = FakeUpdateGraphQLClient()

    result = await client.update_product(
        "gid://shopify/Product/123",
        {
            "title": "Updated Product",
        },
    )

    assert result["productUpdate"]["product"]["id"] == (
        "gid://shopify/Product/123"
    )
    assert result["productUpdate"]["product"]["handle"] == (
        "updated-product"
    )


@pytest.mark.asyncio
async def test_archive_product():
    client = FakeUpdateGraphQLClient()

    result = await client.archive_product(
        "gid://shopify/Product/123"
    )

    assert result["productUpdate"]["product"]["id"] == (
        "gid://shopify/Product/123"
    )


@pytest.mark.asyncio
async def test_list_products():
    client = FakePaginationClient()

    result = await client.list_products(
        query="",
        first=20,
        after=None,
    )

    assert "products" in result
    assert result["products"]["pageInfo"]["hasNextPage"] is True
    assert result["products"]["pageInfo"]["endCursor"] == (
        "cursor-1"
    )

    assert len(result["products"]["edges"]) == 1
    assert (
        result["products"]["edges"][0]["node"]["title"]
        == "Product One"
    )


@pytest.mark.asyncio
async def test_import_product():
    client = FakeGraphQLClient()

    result = await client.import_product(
        {
            "title": "Imported Product",
            "description": "Imported description",
            "supplier": "Test Supplier",
            "category": "Test Category",
            "tags": "tag1, tag2",
        }
    )

    assert result["productCreate"]["product"]["id"] == (
        "gid://shopify/Product/123"
    )


@pytest.mark.asyncio
async def test_create_product_raises_on_user_errors():
    client = FakeUserErrorCreateClient()

    with pytest.raises(
        ShopifyAPIError,
        match="Shopify productCreate failed",
    ):
        await client.create_product(
            {
                "title": "Invalid Product",
            }
        )


@pytest.mark.asyncio
async def test_update_product_raises_on_user_errors():
    client = FakeUserErrorUpdateClient()

    with pytest.raises(
        ShopifyAPIError,
        match="Shopify productUpdate failed",
    ):
        await client.update_product(
            "gid://shopify/Product/123",
            {
                "title": "Invalid Product",
            },
        )


def test_normalize_shop_domain():
    assert (
        ShopifyAPIClient.normalize_shop_domain(
            "https://example.myshopify.com/"
        )
        == "example.myshopify.com"
    )

    assert (
        ShopifyAPIClient.normalize_shop_domain(
            "http://example.myshopify.com/"
        )
        == "example.myshopify.com"
    )

    assert (
        ShopifyAPIClient.normalize_shop_domain(
            "example.myshopify.com/"
        )
        == "example.myshopify.com"
    )


def test_is_valid_shop_domain():
    assert ShopifyAPIClient.is_valid_shop_domain(
        "example.myshopify.com"
    )

    assert ShopifyAPIClient.is_valid_shop_domain(
        "https://example.myshopify.com/"
    )

    assert not ShopifyAPIClient.is_valid_shop_domain(
        "example.com"
    )


def test_parse_shop_domain():
    assert (
        ShopifyAPIClient.parse_shop_domain(
            "https://example.myshopify.com/"
        )
        == "example.myshopify.com"
    )

    with pytest.raises(
        ValueError,
        match="Invalid Shopify shop domain",
    ):
        ShopifyAPIClient.parse_shop_domain(
            "example.com"
        )


def test_token_required():
    client = ShopifyAPIClient(
        "example.myshopify.com"
    )

    with pytest.raises(
        ShopifyAPIError,
        match="Shopify access token is not configured",
    ):
        client._require_token()


def test_verify_webhook_hmac():
    original_secret = settings.shopify_api_secret

    try:
        secret = "test-secret"
        settings.shopify_api_secret = secret

        body = b'{"test":"payload"}'

        digest = hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()

        expected = base64.b64encode(
            digest
        ).decode("utf-8")

        assert ShopifyAPIClient.verify_webhook_hmac(
            body,
            expected,
        )

        assert not ShopifyAPIClient.verify_webhook_hmac(
            body,
            "invalid-signature",
        )

    finally:
        settings.shopify_api_secret = original_secret


def test_map_import_to_product():
    service = ProductService(
        ShopifyAPIClient(
            "example.myshopify.com",
            "test-token",
        )
    )

    result = service.map_import_to_product(
        {
            "title": "Test Product",
            "description": "A test product",
            "supplier": "Test Supplier",
            "category": "Test Category",
            "tags": "tag1, tag2",
        }
    )

    assert result["title"] == "Test Product"
    assert result["descriptionHtml"] == "A test product"
    assert result["vendor"] == "Test Supplier"
    assert result["productType"] == "Test Category"
    assert result["tags"] == ["tag1", "tag2"]
    assert result["status"] == "DRAFT"


def test_map_import_to_product_with_list_tags():
    service = ProductService(
        ShopifyAPIClient(
            "example.myshopify.com",
            "test-token",
        )
    )

    result = service.map_import_to_product(
        {
            "title": "Test Product",
            "tags": [
                "tag1",
                " tag2 ",
                "",
                "tag3",
            ],
        }
    )

    assert result["tags"] == [
        "tag1",
        "tag2",
        "tag3",
    ]


def test_map_import_to_product_without_optional_fields():
    service = ProductService(
        ShopifyAPIClient(
            "example.myshopify.com",
            "test-token",
        )
    )

    result = service.map_import_to_product(
        {
            "title": "Test Product",
        }
    )

    assert result["title"] == "Test Product"
    assert result["status"] == "DRAFT"
    assert "descriptionHtml" not in result
    assert "vendor" not in result
    assert "productType" not in result
