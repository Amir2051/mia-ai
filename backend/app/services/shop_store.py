from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import Shop


class ShopStore:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_shop(
        self,
        shop_domain: str,
    ) -> Optional[Shop]:
        result = await self.db.execute(
            select(Shop).where(
                Shop.shop_domain == shop_domain
            )
        )

        return result.scalar_one_or_none()

    async def upsert_shop(
        self,
        shop_domain: str,
        **fields,
    ) -> Shop:
        shop = await self.get_shop(shop_domain)

        if not shop:
            shop = Shop(
                shop_domain=shop_domain,
                **fields,
            )

            self.db.add(shop)

        else:
            for key, value in fields.items():
                setattr(shop, key, value)

        return shop
