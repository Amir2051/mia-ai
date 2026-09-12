import asyncio
import csv
import json
import os
import sys
from io import StringIO

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.models.database import Base
from app.models.schemas import ProductImport, Shop
from app.services.products import ImportService, ProductService
from app.shopify.client import ShopifyAPIClient


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./dev.db",
)

TARGET_IMPORT_ID = 3


async def main() -> None:
    engine = create_async_engine(DATABASE_URL)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as db:
        result = await db.execute(
            select(ProductImport).where(ProductImport.id == TARGET_IMPORT_ID)
        )
        record = result.scalar_one_or_none()
        if record is None:
            print(f"Import {TARGET_IMPORT_ID} not found")
            return

        shop_result = await db.execute(
            select(Shop).where(Shop.id == record.shop_id)
        )
        shop = shop_result.scalar_one_or_none()
        if not shop or not shop.access_token_encrypted:
            print("Shop or access token missing")
            return

        from app.security.tokens import decrypt_token
        access_token = decrypt_token(shop.access_token_encrypted)
        client = ShopifyAPIClient(
            shop_domain=shop.shop_domain,
            access_token=access_token,
        )

        content = record.description or ""
        mapping = {}
        parsed = ImportService(db_session=db, shop=shop).parse_csv(content)
        service = ImportService(db_session=db, shop=shop, api_client=client)
        validation = service.validate_import_rows(parsed["rows"], mapping)

        print("VALID_ROWS_JSON=" + json.dumps(validation.get("valid", []), default=str))

        if not validation.get("valid"):
            print("NO_VALID_ROWS")
            return

        item = validation["valid"][0]
        mapped = dict(item["mapped"])
        mapped["status"] = "DRAFT"

        print("MAPPED_PAYLOAD=" + json.dumps(mapped, default=str))

        raw = await client.graphql(
            """
            mutation CreateProduct($product: ProductCreateInput!) {
                productCreate(product: $product) {
                    product {
                        id
                        handle
                        title
                        status
                    }
                    userErrors {
                        field
                        message
                    }
                }
            }
            """,
            {"product": mapped},
        )

        print("SHOPIFY_RAW_RESPONSE=" + json.dumps(raw, default=str))


if __name__ == "__main__":
    from sqlalchemy import select
    asyncio.run(main())
