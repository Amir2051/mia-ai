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


def test_shop_redact_deletes_all_shop_owned_records(client, monkeypatch):
    import asyncio
    import os
    import uuid
    from datetime import datetime
    from sqlalchemy import delete, select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from app.models.schemas import (
        AppSetting,
        AuditLog,
        MiaMemory,
        ProductImport,
        PublishingAction,
        ScheduledReport,
        Shop,
        ShopSession,
        SocialAccount,
        SyncJob,
        WebhookEvent,
    )

    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    runtime_url = os.getenv("MIA_RLS_TEST_RUNTIME_URL")
    if not admin_url or not runtime_url:
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL and MIA_RLS_TEST_RUNTIME_URL")
        pytest.skip("Dedicated PostgreSQL admin and runtime URLs are not configured")

    suffix = uuid.uuid4().hex
    shop_domain = f"erase-me-{suffix}.myshopify.com"

    async def seed():
        engine = create_async_engine(admin_url, pool_pre_ping=True)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as db:
                shop = Shop(shop_domain=shop_domain, is_active=True)
                db.add(shop)
                await db.flush()
                shop_id = shop.id
                db.add_all([
                    ShopSession(shop_id=shop_id, session_token=f"session-erase-{suffix}", is_valid=True),
                    ProductImport(shop_id=shop_id, source="test", status="pending", sync_status="pending"),
                    SyncJob(shop_id=shop_id, entity="products", status="queued"),
                    AppSetting(shop_id=shop_id, key="test", value_json="{}"),
                    WebhookEvent(shop_id=shop_id, topic="products/update", event_id=f"seed-event-{suffix}", payload_json="{}"),
                    AuditLog(shop_id=shop_id, action="shopify.products.update", entity_type="product", entity_id="1", details_json="{}"),
                    SocialAccount(
                        shop_id=shop_id,
                        provider="meta",
                        account_type="facebook_page",
                        external_account_id=f"page-erase-{suffix}",
                        account_name="Erase Me Page",
                        access_token_encrypted="encrypted-test-token",
                    ),
                    ScheduledReport(
                        shop_id=shop_id,
                        name="Erase scheduled report",
                        frequency="daily",
                        schedule_time="09:00",
                        timezone="UTC",
                        next_run_at=datetime(2030, 1, 1),
                    ),
                    MiaMemory(
                        shop_id=shop_id,
                        conversation_json='[{"message":"erase merchant conversation"}]',
                        knowledge_json='[{"note":"erase merchant note"}]',
                    ),
                    PublishingAction(
                        shop_id=shop_id,
                        action_type="product.publish",
                        idempotency_key=f"erase-publishing-action-{suffix}",
                        request_hash="a" * 64,
                        payload_json='{"product_id":"123","note":"erase merchant payload"}',
                    ),
                ])
                await db.commit()
                return shop_id
        finally:
            await engine.dispose()

    async def counts(shop_id):
        engine = create_async_engine(admin_url, pool_pre_ping=True)
        try:
            async with AsyncSession(engine) as db:
                values = {}
                for model in (
                    Shop,
                    ShopSession,
                    ProductImport,
                    SyncJob,
                    AppSetting,
                    WebhookEvent,
                    AuditLog,
                    SocialAccount,
                    ScheduledReport,
                    MiaMemory,
                    PublishingAction,
                ):
                    result = await db.execute(
                        select(model).where(model.shop_id == shop_id)
                        if model is not Shop
                        else select(model).where(model.id == shop_id)
                    )
                    values[model.__name__] = len(result.scalars().all())
                return values
        finally:
            await engine.dispose()

    async def cleanup(shop_id):
        """Remove this test's committed fixtures even when the webhook assertion fails."""
        engine = create_async_engine(admin_url, pool_pre_ping=True)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as db:
                for model in (
                    ShopSession,
                    ProductImport,
                    SyncJob,
                    AppSetting,
                    WebhookEvent,
                    AuditLog,
                    SocialAccount,
                    ScheduledReport,
                    MiaMemory,
                    PublishingAction,
                ):
                    await db.execute(delete(model).where(model.shop_id == shop_id))
                await db.execute(delete(Shop).where(Shop.id == shop_id))
                await db.commit()
        finally:
            await engine.dispose()

    shop_id = asyncio.run(seed())

    # The shared client fixture defaults to an isolated SQLite database. Route
    # this request through the same restricted PostgreSQL runtime role used by
    # the RLS integration suite, while the admin connection above is used only
    # to seed and inspect rows that RLS must hide from other tenants.
    import app.models.database as db_mod

    runtime_engine = None
    original_secret = router_settings.shopify_api_secret
    router_settings.shopify_api_secret = "test-secret"
    try:
        runtime_engine = create_async_engine(runtime_url, pool_pre_ping=True)
        runtime_session_local = sessionmaker(
            bind=runtime_engine,
            class_=AsyncSession,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        monkeypatch.setattr(db_mod, "engine", runtime_engine)
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", runtime_session_local)

        body = json.dumps({"shop_id": 123, "shop_domain": shop_domain}).encode()
        response = client.post(
            "/api/webhooks",
            headers=_signed_headers("shop/redact", shop_domain, f"erase-event-{suffix}", body),
            content=body,
        )
        assert response.status_code == 200, response.text
        assert response.json().get("processed") is True, response.json()
        remaining = asyncio.run(counts(shop_id))
        leftovers = {name: count for name, count in remaining.items() if count}
        assert not leftovers, leftovers
    finally:
        router_settings.shopify_api_secret = original_secret
        if runtime_engine is not None:
            asyncio.run(runtime_engine.dispose())
        asyncio.run(cleanup(shop_id))


def test_customer_redact_removes_legacy_pii_records(client):
    import asyncio
    from sqlalchemy import select
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import AuditLog, Shop, WebhookEvent

    target_email = "cust_omer@example.com"
    near_match_email = "custXomer@example.com"

    async def seed():
        async with AsyncSessionLocal() as db:
            shop = Shop(shop_domain="customer-redact.myshopify.com", is_active=True)
            db.add(shop)
            await db.flush()
            db.add_all([
                WebhookEvent(
                    shop_id=shop.id,
                    topic="orders/create",
                    event_id="legacy-target",
                    payload_json=json.dumps({
                        "customer": {"id": 456, "email": target_email.upper()}
                    }),
                ),
                WebhookEvent(
                    shop_id=shop.id,
                    topic="orders/create",
                    event_id="legacy-near-match",
                    payload_json=json.dumps({
                        "customer": {"id": 987, "email": near_match_email}
                    }),
                ),
                AuditLog(
                    shop_id=shop.id,
                    action="legacy.customer",
                    entity_type="customer",
                    entity_id="456",
                    details_json=json.dumps({"email": target_email.upper()}),
                ),
                AuditLog(
                    shop_id=shop.id,
                    action="legacy.email_entity",
                    entity_type="customer",
                    entity_id=target_email.upper(),
                    details_json="{}",
                ),
                AuditLog(
                    shop_id=shop.id,
                    action="legacy.near_match",
                    entity_type="customer",
                    entity_id=near_match_email,
                    details_json=json.dumps({"email": near_match_email}),
                ),
            ])
            await db.commit()
            return shop.id

    async def remaining(shop_id):
        async with AsyncSessionLocal() as db:
            events = (await db.execute(
                select(WebhookEvent).where(WebhookEvent.shop_id == shop_id)
            )).scalars().all()
            audits = (await db.execute(
                select(AuditLog).where(AuditLog.shop_id == shop_id)
            )).scalars().all()
            return [(e.payload_json, e.event_id) for e in events], [
                (a.action, a.details_json, a.entity_id) for a in audits
            ]

    shop_id = asyncio.run(seed())
    router_settings.shopify_api_secret = "test-secret"
    body = json.dumps({
        "customer": {"id": 456, "email": target_email}
    }).encode()
    response = client.post(
        "/api/webhooks",
        headers=_signed_headers(
            "customers/redact",
            "customer-redact.myshopify.com",
            "redact-event",
            body,
        ),
        content=body,
    )
    assert response.status_code == 200

    events, audits = asyncio.run(remaining(shop_id))
    # The actual customer's legacy event is erased, while an address that
    # differs only at the SQL LIKE wildcard position remains untouched.
    assert {event_id for _, event_id in events} == {
        "legacy-near-match",
        "redact-event",
    }
    assert any(
        action == "legacy.near_match"
        and near_match_email in details_json
        and entity_id == near_match_email
        for action, details_json, entity_id in audits
    )
    assert any(
        action == "privacy.customer_redact" and entity_id is None
        for action, _, entity_id in audits
    )
    assert len(audits) == 2
