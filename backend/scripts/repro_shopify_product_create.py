import asyncio
import json
import os

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from app.models.database import Base
from app.models.schemas import Shop
from app.shopify.client import ShopifyAPIClient
from app.security.tokens import decrypt_token


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./dev.db",
)

MAPPED_PAYLOAD = {
    "title": "Cotton T-Shirt",
    "descriptionHtml": "Soft cotton tee.",
    "vendor": "MIA",
    "productType": "Apparel",
    "tags": ["cotton", "tshirt"],
    "status": "DRAFT",
}


async def main() -> None:
    engine = create_async_engine(DATABASE_URL)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as db:
        result = await db.execute(
            select(Shop).where(Shop.id == 2)
        )
        shop = result.scalar_one_or_none()
        if not shop or not shop.access_token_encrypted:
            print("NO_SHOP_OR_TOKEN")
            return

        access_token = decrypt_token(shop.access_token_encrypted)
        client = ShopifyAPIClient(
            shop_domain=shop.shop_domain,
            access_token=access_token,
        )

        payload = {"product": MAPPED_PAYLOAD}
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
            payload,
        )

        product_create = raw.get("productCreate") or {}
        product = product_create.get("product") or {}
        user_errors = product_create.get("userErrors") or []

        print("TOP_LEVEL_ERRORS=" + json.dumps(raw.get("errors"), default=str))
        print("PRODUCT_CREATE=" + json.dumps(product_create, default=str))
        print("PRODUCT=" + json.dumps(product, default=str))
        print("USER_ERRORS=" + json.dumps(user_errors, default=str))
        print("PRODUCT_ID=" + json.dumps(product.get("id"), default=str))
        print("PRODUCT_TITLE=" + json.dumps(product.get("title"), default=str))
        print("PRODUCT_STATUS=" + json.dumps(product.get("status"), default=str))


if __name__ == "__main__":
    asyncio.run(main())
