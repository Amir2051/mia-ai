import base64
import hashlib
import hmac
import json

import pytest
from app.shopify.config import settings as router_settings


def test_webhook_requires_headers(client):
    response = client.post(
        "/api/webhooks",
        content=b'{"id":123}',
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Missing webhook headers"


def test_webhook_without_shopify_secret(client):
    original_secret = router_settings.shopify_api_secret
    router_settings.shopify_api_secret = ""

    try:
        response = client.post(
            "/api/webhooks",
            headers={
                "X-Shopify-Topic": "products/create",
                "X-Shopify-Shop-Domain": "example.myshopify.com",
                "X-Shopify-Event-Id": "event-123",
            },
            content=json.dumps({"id": 123}).encode(),
        )

        assert response.status_code == 503
        assert response.json()["detail"] == "Webhook verification is not configured"
    finally:
        router_settings.shopify_api_secret = original_secret


def test_webhook_rejects_missing_signature_when_secret_configured(client):
    original_secret = router_settings.shopify_api_secret
    router_settings.shopify_api_secret = "test-secret"

    try:
        response = client.post(
            "/api/webhooks",
            headers={
                "X-Shopify-Topic": "products/create",
                "X-Shopify-Shop-Domain": "example.myshopify.com",
                "X-Shopify-Event-Id": "event-123",
            },
            content=b'{"id":123}',
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid webhook signature"
    finally:
        router_settings.shopify_api_secret = original_secret


def test_webhook_rejects_invalid_signature(client):
    original_secret = router_settings.shopify_api_secret
    router_settings.shopify_api_secret = "test-secret"

    try:
        response = client.post(
            "/api/webhooks",
            headers={
                "X-Shopify-Topic": "products/create",
                "X-Shopify-Shop-Domain": "example.myshopify.com",
                "X-Shopify-Event-Id": "event-123",
                "X-Shopify-Hmac-SHA256": "bad-signature",
            },
            content=b'{"id":123}',
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid webhook signature"
    finally:
        router_settings.shopify_api_secret = original_secret


def test_webhook_accepts_valid_signature(client):
    original_secret = router_settings.shopify_api_secret
    router_settings.shopify_api_secret = "test-secret"

    body = b'{"id":123,"title":"Test Product"}'
    signature = base64.b64encode(
        hmac.new(
            b"test-secret",
            body,
            hashlib.sha256,
        ).digest()
    ).decode()

    try:
        response = client.post(
            "/api/webhooks",
            headers={
                "X-Shopify-Topic": "products/create",
                "X-Shopify-Shop-Domain": "example.myshopify.com",
                "X-Shopify-Event-Id": "event-123",
                "X-Shopify-Hmac-SHA256": signature,
            },
            content=body,
        )

        assert response.status_code == 200
        assert response.json()["status"] == "received"
    finally:
        router_settings.shopify_api_secret = original_secret


def _signed_headers(topic, shop, event_id, body, secret="test-secret"):
    signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return {
        "X-Shopify-Topic": topic,
        "X-Shopify-Shop-Domain": shop,
        "X-Shopify-Event-Id": event_id,
        "X-Shopify-Hmac-SHA256": signature,
        "Content-Type": "application/json",
    }


def test_gdpr_compliance_webhooks_accept_valid_hmac(client):
    import asyncio
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import Shop

    async def seed():
        async with AsyncSessionLocal() as db:
            db.add(Shop(shop_domain="example.myshopify.com", is_active=True))
            await db.commit()

    asyncio.run(seed())
    router_settings.shopify_api_secret = "test-secret"
    try:
        for index, topic in enumerate(("customers/data_request", "customers/redact", "shop/redact"), 1):
            body = json.dumps({
                "shop_id": 123,
                "shop_domain": "example.myshopify.com",
                "customer": {"id": 456, "email": "customer@example.com", "phone": "+15555555555"},
                "orders_requested": [789],
            }).encode()
            response = client.post("/api/webhooks", headers=_signed_headers(topic, "example.myshopify.com", f"gdpr-{index}", body), content=body)
            assert response.status_code == 200, (topic, response.text)
            assert response.json()["processed"] is True
    finally:
        router_settings.shopify_api_secret = "test-secret"


def test_gdpr_webhooks_reject_invalid_hmac(client):
    router_settings.shopify_api_secret = "test-secret"
    body = b'{"shop_id":123}'
    for topic in ("customers/data_request", "customers/redact", "shop/redact"):
        headers = _signed_headers(topic, "example.myshopify.com", f"bad-{topic}", body)
        headers["X-Shopify-Hmac-SHA256"] = "invalid"
        response = client.post("/api/webhooks", headers=headers, content=body)
        assert response.status_code == 401


def test_shop_redact_deletes_all_shop_owned_records(client):
    import asyncio
    from sqlalchemy import select
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import AppSetting, AuditLog, ProductImport, Shop, ShopSession, SyncJob, WebhookEvent

    async def seed():
        async with AsyncSessionLocal() as db:
            shop = Shop(shop_domain="erase-me.myshopify.com", is_active=True)
            db.add(shop)
            await db.flush()
            shop_id = shop.id
            db.add_all([
                ShopSession(shop_id=shop_id, session_token="session-erase", is_valid=True),
                ProductImport(shop_id=shop_id, source="test", status="pending", sync_status="pending"),
                SyncJob(shop_id=shop_id, entity="products", status="queued"),
                AppSetting(shop_id=shop_id, key="test", value_json="{}"),
                WebhookEvent(shop_id=shop_id, topic="products/update", event_id="seed-event", payload_json="{}"),
                AuditLog(shop_id=shop_id, action="shopify.products.update", entity_type="product", entity_id="1", details_json="{}"),
            ])
            await db.commit()
            return shop_id

    async def counts(shop_id):
        async with AsyncSessionLocal() as db:
            values = {}
            for model in (Shop, ShopSession, ProductImport, SyncJob, AppSetting, WebhookEvent, AuditLog):
                result = await db.execute(select(model).where(model.shop_id == shop_id) if model is not Shop else select(model).where(model.id == shop_id))
                values[model.__name__] = len(result.scalars().all())
            return values

    shop_id = asyncio.run(seed())
    router_settings.shopify_api_secret = "test-secret"
    body = b'{"shop_id":123,"shop_domain":"erase-me.myshopify.com"}'
    response = client.post("/api/webhooks", headers=_signed_headers("shop/redact", "erase-me.myshopify.com", "erase-event", body), content=body)
    assert response.status_code == 200
    assert all(value == 0 for value in asyncio.run(counts(shop_id)).values())


def test_customer_redact_removes_legacy_pii_records(client):
    import asyncio
    from sqlalchemy import select
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import AuditLog, Shop, WebhookEvent

    async def seed():
        async with AsyncSessionLocal() as db:
            shop = Shop(shop_domain="customer-redact.myshopify.com", is_active=True)
            db.add(shop)
            await db.flush()
            db.add(WebhookEvent(shop_id=shop.id, topic="orders/create", event_id="legacy-1", payload_json='{"customer":{"id":456,"email":"customer@example.com"}}'))
            db.add(AuditLog(shop_id=shop.id, action="legacy", entity_type="customer", entity_id="456", details_json='{"email":"customer@example.com"}'))
            await db.commit()
            return shop.id

    async def remaining(shop_id):
        async with AsyncSessionLocal() as db:
            events = (await db.execute(select(WebhookEvent).where(WebhookEvent.shop_id == shop_id))).scalars().all()
            audits = (await db.execute(select(AuditLog).where(AuditLog.shop_id == shop_id))).scalars().all()
            return [(e.payload_json, e.event_id) for e in events], [(a.details_json, a.entity_id) for a in audits]

    shop_id = asyncio.run(seed())
    router_settings.shopify_api_secret = "test-secret"
    body = b'{"customer":{"id":456,"email":"customer@example.com"}}'
    response = client.post("/api/webhooks", headers=_signed_headers("customers/redact", "customer-redact.myshopify.com", "redact-event", body), content=body)
    assert response.status_code == 200
    events, audits = asyncio.run(remaining(shop_id))
    assert events == [("{}", "redact-event")]
    assert audits == [("{}", "456")]
