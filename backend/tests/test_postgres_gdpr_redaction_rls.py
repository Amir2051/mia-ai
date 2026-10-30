"""GDPR redaction must work under RLS without general audit-log DELETE rights."""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
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
        legacy_customer_payload = json.dumps({
            "customer": {"id": int(customer_id), "email": customer_email.upper()}
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
                    WebhookEvent(shop_id=shop_a_id, topic="orders/create", event_id=f"legacy-pii-{suffix}", payload_json=legacy_customer_payload, processed=True),
                    AuditLog(shop_id=shop_a_id, action="legacy.customer", entity_type="customer", entity_id=customer_id, details_json=legacy_customer_payload),
                    AuditLog(shop_id=shop_a_id, action="legacy.email_entity", entity_type="customer", entity_id=customer_email.upper(), details_json="{}"),
                    AuditLog(shop_id=shop_a_id, action="legacy.non_pii", entity_type="product", entity_id="1", details_json="{}"),
                    SocialAccount(
                        shop_id=shop_a_id, provider="meta", account_type="page",
                        external_account_id=f"page-{suffix}", account_name="GDPR test page",
                        access_token_encrypted="ci-encrypted-token",
                    ),
                    MiaMemory(shop_id=shop_a_id, conversation_json="[]", knowledge_json="[]"),
                    ScheduledReport(
                        shop_id=shop_a_id, name="GDPR test report", report_type="sales_summary",
                        frequency="daily", schedule_time="09:00", timezone="UTC",
                        next_run_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    ),
                    PublishingAction(
                        shop_id=shop_a_id, action_type="product.publish",
                        status="pending_approval", idempotency_key=f"gdpr-{suffix}",
                        request_hash="a" * 64, payload_json="{}",
                    ),
                    AuditLog(shop_id=shop_b_id, action="other_shop.customer", entity_type="customer", entity_id=customer_id, details_json=customer_payload),
                ])
                await admin.commit()

            async with runtime_factory() as runtime:
                can_delete_audit = await runtime.scalar(
                    text("SELECT has_table_privilege(current_user, 'public.audit_logs', 'DELETE')")
                )
                assert can_delete_audit is False

                # A caller with Shop A context must not use the definer function
                # to erase Shop B's audit trail.
                from app.security.rls import set_shop_context
                await set_shop_context(runtime, shop_a_domain, shop_a_id)
                with pytest.raises(DBAPIError):
                    await runtime.execute(
                        text(
                            "SELECT public.mia_purge_audit_logs_for_privacy("
                            ":shop_id, :customer_id, :customer_email, :all_shop)"
                        ),
                        {
                            "shop_id": shop_b_id,
                            "customer_id": None,
                            "customer_email": None,
                            "all_shop": True,
                        },
                    )
                await runtime.rollback()

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
                assert not any(
                    customer_email.lower() in (row.details_json or "").lower()
                    or (row.entity_id or "").lower() == customer_email.lower()
                    for row in shop_a_audits
                )
                assert not any(
                    customer_id in (row.details_json or "") or row.entity_id == customer_id
                    for row in shop_a_audits
                )
                legacy_customer_events = (await admin.execute(
                    select(WebhookEvent).where(
                        WebhookEvent.shop_id == shop_a_id,
                        func.lower(WebhookEvent.payload_json).contains(customer_email.lower()),
                    )
                )).scalars().all()
                assert legacy_customer_events == []
                assert any(row.action == "legacy.non_pii" for row in shop_a_audits)
                assert any(row.action == "other_shop.customer" for row in shop_b_audits)
                assert any(
                    row.action == "privacy.customer_redact" and row.entity_id is None
                    for row in shop_a_audits
                )

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
                remaining_social_accounts = (await admin.execute(
                    select(SocialAccount).where(SocialAccount.shop_id == shop_a_id)
                )).scalars().all()
                remaining_memory = (await admin.execute(
                    select(MiaMemory).where(MiaMemory.shop_id == shop_a_id)
                )).scalars().all()
                remaining_reports = (await admin.execute(
                    select(ScheduledReport).where(ScheduledReport.shop_id == shop_a_id)
                )).scalars().all()
                remaining_publishing_actions = (await admin.execute(
                    select(PublishingAction).where(PublishingAction.shop_id == shop_a_id)
                )).scalars().all()
                assert remaining_audits == []
                assert remaining_sessions == []
                assert remaining_imports == []
                assert remaining_jobs == []
                assert remaining_settings == []
                assert remaining_social_accounts == []
                assert remaining_memory == []
                assert remaining_reports == []
                assert remaining_publishing_actions == []
                other_audits = (await admin.execute(
                    select(AuditLog).where(AuditLog.shop_id == shop_b_id)
                )).scalars().all()
                assert len(other_audits) == 1
                assert other_audits[0].action == "other_shop.customer"
        finally:
            await runtime_engine.dispose()
            await admin_engine.dispose()

    asyncio.run(scenario())
