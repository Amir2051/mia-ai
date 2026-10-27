"""GDPR redaction must work under RLS without general audit-log DELETE rights."""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request
from starlette.responses import Response


def test_customer_and_shop_redaction_preserve_audit_log_least_privilege(monkeypatch):
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    runtime_url = os.getenv("MIA_RLS_TEST_RUNTIME_URL")
    if not admin_url or not runtime_url:
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL and MIA_RLS_TEST_RUNTIME_URL")
        pytest.skip("Dedicated PostgreSQL RLS URLs are not configured")

    from app.models.schemas import (
        AppSetting,
        AuditLog,
        ProductImport,
        Shop,
        ShopSession,
        SyncJob,
        WebhookEvent,
    )
    from app.routers.webhooks import receive_webhook
    from app.shopify.config import settings

    secret = "ci-gdpr-rls-test-secret"
    monkeypatch.setattr(settings, "shopify_api_secret", secret)

    def make_request(topic, domain, event_id, body):
        signature = base64.b64encode(
            hmac.new(secret.encode(), body, hashlib.sha256).digest()
        ).decode()
        headers = [
            (b"x-shopify-topic", topic.encode()),
            (b"x-shopify-shop-domain", domain.encode()),
            (b"x-shopify-event-id", event_id.encode()),
            (b"x-shopify-hmac-sha256", signature.encode()),
            (b"content-type", b"application/json"),
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/webhooks",
            "raw_path": b"/api/webhooks",
            "query_string": b"",
            "headers": headers,
            "server": ("testserver", 443),
            "client": ("127.0.0.1", 12345),
        }

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        return Request(scope, receive)

    async def scenario():
        admin_engine = create_async_engine(admin_url, pool_pre_ping=True)
        runtime_engine = create_async_engine(runtime_url, pool_pre_ping=True)
        admin_factory = sessionmaker(
            bind=admin_engine, class_=AsyncSession, expire_on_commit=False
        )
        runtime_factory = sessionmaker(
            bind=runtime_engine, class_=AsyncSession, expire_on_commit=False
        )
        suffix = uuid.uuid4().hex[:12]
        shop_a_domain = f"gdpr-a-{suffix}.myshopify.com"
        shop_b_domain = f"gdpr-b-{suffix}.myshopify.com"
        customer_id = str(900000000 + int(suffix[:6], 16) % 99999999)
        customer_email = f"customer-{suffix}@example.test"
        customer_payload = json.dumps({
            "customer": {"id": int(customer_id), "email": customer_email}
        })
        try:
            async with admin_factory() as admin:
                shop_a = Shop(
                    shop_domain=shop_a_domain,
                    access_token_encrypted="ci-gdpr-token-a",
                    is_active=True,
                )
                shop_b = Shop(
                    shop_domain=shop_b_domain,
                    access_token_encrypted="ci-gdpr-token-b",
                    is_active=True,
                )
                admin.add_all([shop_a, shop_b])
                await admin.flush()
                shop_a_id, shop_b_id = shop_a.id, shop_b.id
                admin.add_all([
                    ShopSession(shop_id=shop_a_id, session_token=f"session-a-{suffix}", state=f"state-a-{suffix}", is_valid=True),
                    ProductImport(shop_id=shop_a_id, source="gdpr-test", status="pending", sync_status="pending"),
                    SyncJob(shop_id=shop_a_id, entity="products", status="queued", details_json="{}"),
                    AppSetting(shop_id=shop_a_id, key=f"gdpr-setting-{suffix}", value_json="{}"),
                    WebhookEvent(shop_id=shop_a_id, topic="orders/create", event_id=f"legacy-pii-{suffix}", payload_json=customer_payload, processed=True),
                    AuditLog(shop_id=shop_a_id, action="legacy.customer", entity_type="customer", entity_id=customer_id, details_json=customer_payload),
                    AuditLog(shop_id=shop_a_id, action="legacy.non_pii", entity_type="product", entity_id="1", details_json="{}"),
                    AuditLog(shop_id=shop_b_id, action="other_shop.customer", entity_type="customer", entity_id=customer_id, details_json=customer_payload),
                ])
                await admin.commit()

            async with runtime_factory() as runtime:
                can_delete_audit = await runtime.scalar(
                    text("SELECT has_table_privilege(current_user, 'public.audit_logs', 'DELETE')")
                )
                assert can_delete_audit is False

                redact_body = json.dumps({
                    "customer": {"id": int(customer_id), "email": customer_email}
                }).encode()
                response = await receive_webhook(
                    make_request("customers/redact", shop_a_domain, f"customer-redact-{suffix}", redact_body),
                    Response(),
                    runtime,
                )
                assert response["processed"] is True

            async with admin_factory() as admin:
                shop_a_audits = (await admin.execute(
                    select(AuditLog).where(AuditLog.shop_id == shop_a_id)
                )).scalars().all()
                shop_b_audits = (await admin.execute(
                    select(AuditLog).where(AuditLog.shop_id == shop_b_id)
                )).scalars().all()
                assert not any(customer_email in (row.details_json or "") for row in shop_a_audits)
                assert not any(customer_id in (row.details_json or "") for row in shop_a_audits)
                assert any(row.action == "legacy.non_pii" for row in shop_a_audits)
                assert any(row.action == "other_shop.customer" for row in shop_b_audits)
                assert any(row.action == "privacy.customer_redact" for row in shop_a_audits)

            shop_redact_body = json.dumps({"shop_id": 123}).encode()
            async with runtime_factory() as runtime:
                response = await receive_webhook(
                    make_request("shop/redact", shop_a_domain, f"shop-redact-{suffix}", shop_redact_body),
                    Response(),
                    runtime,
                )
                assert response["processed"] is True

            async with admin_factory() as admin:
                remaining_shop = await admin.scalar(select(Shop).where(Shop.id == shop_a_id))
                other_shop = await admin.scalar(select(Shop).where(Shop.id == shop_b_id))
                assert remaining_shop is None
                assert other_shop is not None
                remaining_audits = (await admin.execute(
                    select(AuditLog).where(AuditLog.shop_id == shop_a_id)
                )).scalars().all()
                remaining_sessions = (await admin.execute(
                    select(ShopSession).where(ShopSession.shop_id == shop_a_id)
                )).scalars().all()
                remaining_imports = (await admin.execute(
                    select(ProductImport).where(ProductImport.shop_id == shop_a_id)
                )).scalars().all()
                remaining_jobs = (await admin.execute(
                    select(SyncJob).where(SyncJob.shop_id == shop_a_id)
                )).scalars().all()
                remaining_settings = (await admin.execute(
                    select(AppSetting).where(AppSetting.shop_id == shop_a_id)
                )).scalars().all()
                assert remaining_audits == []
                assert remaining_sessions == []
                assert remaining_imports == []
                assert remaining_jobs == []
                assert remaining_settings == []
                other_audits = (await admin.execute(
                    select(AuditLog).where(AuditLog.shop_id == shop_b_id)
                )).scalars().all()
                assert len(other_audits) == 1
                assert other_audits[0].action == "other_shop.customer"
        finally:
            await runtime_engine.dispose()
            await admin_engine.dispose()

    asyncio.run(scenario())
