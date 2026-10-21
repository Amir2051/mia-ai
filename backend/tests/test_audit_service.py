import asyncio
import json

from sqlalchemy import select


def test_audit_event_is_persisted_with_merchant_scope_and_metadata(client):
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import AuditLog, Shop
    from app.services.audit import record_audit_event

    shop_domain = "audit-service-test.myshopify.com"

    async def run():
        async with AsyncSessionLocal() as db:
            shop = Shop(
                shop_domain=shop_domain,
                access_token_encrypted="placeholder",
                is_active=True,
            )
            db.add(shop)
            await db.flush()
            recorded = await record_audit_event(
                db,
                shop_id=shop.id,
                shop_domain=shop_domain,
                action="seo.preview",
                entity_type="product",
                entity_id="gid://shopify/Product/123",
                details={
                    "status": "preview_ready",
                    "product_title": "Demo product",
                    "seo_score": 92,
                },
            )
            rows = (await db.execute(
                select(AuditLog).where(AuditLog.shop_id == shop.id)
            )).scalars().all()
            return recorded, rows

    recorded, rows = asyncio.run(run())

    assert recorded is True
    assert len(rows) == 1
    assert rows[0].action == "seo.preview"
    assert rows[0].entity_id == "gid://shopify/Product/123"
    details = json.loads(rows[0].details_json)
    assert details["status"] == "preview_ready"
    assert details["seo_score"] == 92
