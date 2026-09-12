import pytest
from unittest.mock import patch

from app.models.schemas import Shop
from app.services.products import ImportService
from app.shopify.client import ShopifyAPIClient, ShopifyAPIError


class FakeUserErrorCreateGraphQLClient(ShopifyAPIClient):
    def __init__(self):
        super().__init__("test.myshopify.com", "test-token")

    async def graphql(self, query, variables=None):
        return {
            "productCreate": {
                "product": None,
                "userErrors": [
                    {
                        "field": ["options"],
                        "message": (
                            "Product options input is required "
                            "when updating variants"
                        ),
                    }
                ],
            }
        }


class FakeSuccessGraphQLClient(ShopifyAPIClient):
    def __init__(self):
        super().__init__("test.myshopify.com", "test-token")

    async def graphql(self, query, variables=None):
        return {
            "productCreate": {
                "product": {
                    "id": "gid://shopify/Product/999",
                    "handle": "test-product",
                },
                "userErrors": [],
            }
        }


@pytest.mark.asyncio
async def test_import_run_marks_user_errors_as_failed(db_session):
    shop = Shop(
        shop_domain="test-shop.myshopify.com",
        shop_name="Test Shop",
        access_token_encrypted="encrypted-token",
        scope="read_products,write_products",
        is_active=True,
    )
    db_session.add(shop)
    await db_session.commit()
    await db_session.refresh(shop)

    service = ImportService(
        db_session=db_session,
        shop=shop,
        api_client=FakeUserErrorCreateGraphQLClient(),
    )
    payload = {
        "content": "title,variant_sku\nShirt,MIA-KIDS-TS-001\n",
        "title": "Options Error Import",
        "status": "DRAFT",
        "mapping": {"title": "title", "sku": "variant_sku"},
    }

    result = await service.create_import_from_csv(
        shop_id=shop.id,
        payload=payload,
    )

    import_data = result["import"]
    assert import_data["status"] == "completed_with_errors"
    assert import_data["summary"]["failed"] == 1
    assert import_data["summary"]["created"] == 0
    assert import_data["details"][0]["status"] == "failed"
    assert "Shopify productCreate failed" in (
        import_data["details"][0]["error"] or ""
    )
    assert import_data["details"][0]["shopify_product_id"] is None


@pytest.mark.asyncio
async def test_import_run_persists_shopify_product_id_on_success(db_session):
    shop = Shop(
        shop_domain="test-shop.myshopify.com",
        shop_name="Test Shop",
        access_token_encrypted="encrypted-token",
        scope="read_products,write_products",
        is_active=True,
    )
    db_session.add(shop)
    await db_session.commit()
    await db_session.refresh(shop)

    service = ImportService(
        db_session=db_session,
        shop=shop,
        api_client=FakeSuccessGraphQLClient(),
    )
    payload = {
        "content": "title\nShirt\n",
        "title": "Success Import",
        "status": "DRAFT",
        "mapping": {"title": "title"},
    }

    result = await service.create_import_from_csv(
        shop_id=shop.id,
        payload=payload,
    )

    import_data = result["import"]
    assert import_data["status"] == "completed"
    assert import_data["summary"]["failed"] == 0
    assert import_data["summary"]["created"] == 1
    assert import_data["details"][0]["status"] == "created"
    assert (
        import_data["details"][0]["shopify_product_id"]
        == "gid://shopify/Product/999"
    )


def test_import_preview_accepts_list_tags(client):
    response = client.post(
        "/api/imports/preview",
        json={
            "content": "title,price\nShirt,10",
            "title": "Test Import",
            "tags": ["kids", "clothing", "cotton"],
        },
    )

    assert response.status_code == 200


def test_import_preview_ignores_blank_tags(client):
    response = client.post(
        "/api/imports/preview",
        json={
            "content": "title,price\nShirt,10",
            "title": "Test Import",
            "tags": ["", "kids", "  ", "clothing", "cotton", "  "],
        },
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_import_run_variant_row_creates_product_options(db_session):
    shop = Shop(
        shop_domain="test-shop.myshopify.com",
        shop_name="Test Shop",
        access_token_encrypted="encrypted-token",
        scope="read_products,write_products",
        is_active=True,
    )
    db_session.add(shop)
    await db_session.commit()
    await db_session.refresh(shop)

    service = ImportService(
        db_session=db_session,
        shop=shop,
        api_client=FakeSuccessGraphQLClient(),
    )
    payload = {
        "content": "title,variant_sku,variant_price\nShirt,MIA-KIDS-TS-001,19.99\n",
        "title": "Variant Import",
        "status": "DRAFT",
        "mapping": {
            "title": "title",
            "sku": "variant_sku",
            "price": "variant_price",
        },
    }

    result = await service.create_import_from_csv(
        shop_id=shop.id,
        payload=payload,
    )

    import_data = result["import"]
    assert import_data["status"] == "completed"
    assert import_data["summary"]["failed"] == 0
    assert import_data["summary"]["created"] == 1
    assert import_data["details"][0]["status"] == "created"
    assert (
        import_data["details"][0]["shopify_product_id"]
        == "gid://shopify/Product/999"
    )

    mapped = import_data["details"][0]["mapped"]
    assert "productOptions" in mapped
    assert any(option.get("name") == "Title" for option in mapped["productOptions"])
    assert mapped["variants"] == [
        {
            "sku": "MIA-KIDS-TS-001",
            "price": "19.99",
            "selectedOptions": [
                {"name": "Title", "value": "Default Title"}
            ],
        }
    ]


def test_is_shopify_validation_error_detects_user_errors():
    from app.routers.imports import _is_shopify_validation_error

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


class FakeNoProductIdGraphQLClient(ShopifyAPIClient):
    def __init__(self):
        super().__init__("test.myshopify.com", "test-token")

    async def graphql(self, query, variables=None):
        return {
            "productCreate": {
                "product": None,
                "userErrors": [],
            }
        }


@pytest.mark.asyncio
async def test_import_run_does_not_falsely_succeed_when_shopify_returns_no_product_id(db_session):
    shop = Shop(
        shop_domain="test-shop.myshopify.com",
        shop_name="Test Shop",
        access_token_encrypted="encrypted-token",
        scope="read_products,write_products",
        is_active=True,
    )
    db_session.add(shop)
    await db_session.commit()
    await db_session.refresh(shop)

    service = ImportService(
        db_session=db_session,
        shop=shop,
        api_client=FakeNoProductIdGraphQLClient(),
    )
    payload = {
        "content": "title\nShirt\n",
        "title": "No ID Import",
        "status": "DRAFT",
        "mapping": {"title": "title"},
    }

    result = await service.create_import_from_csv(
        shop_id=shop.id,
        payload=payload,
    )

    import_data = result["import"]
    assert import_data["status"] == "completed_with_errors"
    assert import_data["summary"]["failed"] == 1
    assert import_data["summary"]["created"] == 0
    assert import_data["details"][0]["status"] == "failed"
    assert (
        import_data["details"][0]["error"]
        == "Shopify create_product succeeded but returned no product id"
    )
    assert import_data["details"][0]["shopify_product_id"] is None
