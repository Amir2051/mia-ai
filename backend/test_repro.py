import asyncio
import json
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models.database import Base
from app.models.schemas import Shop, ProductImport
from app.services.products import ImportService
from app.shopify.client import ShopifyAPIClient
from app.shopify.config import settings

TMP_DB = "/tmp/mia_repro.db"
settings.database_url = f"sqlite+aiosqlite:///{TMP_DB}"

tmp_engine = create_async_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    future=True,
)
TmpSessionLocal = sessionmaker(
    bind=tmp_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

class MockShopifyClient(ShopifyAPIClient):
    def __init__(self):
        super().__init__("test.myshopify.com", "test-token")

    async def graphql(self, query, variables=None):
        return {
            "productCreate": {
                "product": {
                    "id": "gid://shopify/Product/999999",
                    "handle": "test-product",
                    "title": "Test Product",
                    "status": "DRAFT",
                },
                "userErrors": [],
            }
        }

async def main():
    async with tmp_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TmpSessionLocal() as db:
        shop = Shop(
            shop_domain="test-shop.myshopify.com",
            shop_name="Test Shop",
            access_token_encrypted="test",
            is_active=True,
        )
        db.add(shop)
        await db.flush()
        await db.refresh(shop)

        import_record = ProductImport(
            shop_id=shop.id,
            source="csv",
            title="Existing Import",
            description="title,price,sku\nTest Product,19.99,TEST-001\n",
            status="pending",
            sync_status="pending",
            error=None,
            shopify_product_id=None,
        )
        db.add(import_record)
        await db.flush()
        await db.refresh(import_record)
        await db.commit()

        csv_content = "title,price,sku\nTest Product,19.99,TEST-001\n"
        payload = {
            "content": csv_content,
            "mapping": {"title": "title", "price": "price", "sku": "sku"},
            "status": "DRAFT",
        }

        service = ImportService(
            db_session=db,
            shop=shop,
            api_client=MockShopifyClient(),
        )
        result = await service.create_import_from_csv(shop_id=shop.id, payload=payload)
        print("RESULT:", json.dumps(result, indent=2, default=str))

        await db.commit()

        from sqlalchemy import select
        res = await db.execute(select(ProductImport).where(ProductImport.id == result["import"]["id"]))
        record = res.scalar_one()
        print("RECORD id:", record.id)
        print("RECORD status:", record.status)
        print("RECORD sync_status:", record.sync_status)
        print("RECORD shopify_product_id:", record.shopify_product_id)
        print("RECORD error:", record.error)

if __name__ == "__main__":
    asyncio.run(main())
