from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


SHOP_DOMAIN_SETTING = "app.shop_domain"
SHOP_ID_SETTING = "app.shop_id"


async def set_shop_context(db: AsyncSession, shop_domain: str, shop_id: int | None = None) -> None:
    """Bind the current authenticated merchant to the PostgreSQL transaction."""
    if not db.bind or db.bind.dialect.name != "postgresql":
        return

    domain = (shop_domain or "").strip().lower()
    if not domain:
        raise ValueError("shop_domain is required for database security context")

    await db.execute(
        text("select set_config(:setting, :value, true)"),
        {"setting": SHOP_DOMAIN_SETTING, "value": domain},
    )

    if shop_id is not None:
        await db.execute(
            text("select set_config(:setting, :value, true)"),
            {"setting": SHOP_ID_SETTING, "value": str(shop_id)},
        )
    else:
        await db.execute(
            text("select set_config(:setting, '', true)"),
            {"setting": SHOP_ID_SETTING},
        )
