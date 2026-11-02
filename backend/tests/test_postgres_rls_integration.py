"""PostgreSQL RLS integration tests using a non-owner runtime role.

CI must provide separate administrative and restricted-role URLs. The normal
API fixture uses SQLite, so these checks explicitly exercise PostgreSQL RLS.
"""
import asyncio
import json
import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


def test_scheduled_reports_and_memory_are_isolated_under_restricted_postgres_role():
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    runtime_url = os.getenv("MIA_RLS_TEST_RUNTIME_URL")
    if not admin_url or not runtime_url:
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL and MIA_RLS_TEST_RUNTIME_URL")
        pytest.skip("Dedicated PostgreSQL RLS URLs are not configured")

    from app.auth.dependencies import CurrentUser
    from app.models.schemas import MiaMemory, ScheduledReport, Shop
    from app.routers.reports import (
        ScheduledReportCreate,
        ScheduledReportUpdate,
        create_scheduled_report,
        update_scheduled_report,
    )
    from app.security.rls import set_shop_context

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
        shop_a_domain = f"rls-a-{suffix}.myshopify.com"
        shop_b_domain = f"rls-b-{suffix}.myshopify.com"
        try:
            async with admin_factory() as admin:
                shop_a = Shop(
                    shop_domain=shop_a_domain,
                    access_token_encrypted="ci-test-token-a",
                    is_active=True,
                )
                shop_b = Shop(
                    shop_domain=shop_b_domain,
                    access_token_encrypted="ci-test-token-b",
                    is_active=True,
                )
                admin.add_all([shop_a, shop_b])
                await admin.flush()

                other_schedule = ScheduledReport(
                    shop_id=shop_b.id,
                    name="Other shop report",
                    report_type="sales_summary",
                    frequency="daily",
                    schedule_time="09:00",
                    timezone="UTC",
                    enabled=True,
                    next_run_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    last_status="never",
                )
                memory_a = MiaMemory(
                    shop_id=shop_a.id,
                    conversation_json="[]",
                    knowledge_json='["shop A note"]',
                )
                memory_b = MiaMemory(
                    shop_id=shop_b.id,
                    conversation_json="[]",
                    knowledge_json='["shop B secret"]',
                )
                admin.add_all([other_schedule, memory_a, memory_b])
                await admin.commit()
                shop_a_id = shop_a.id
                shop_b_id = shop_b.id
                other_schedule_id = other_schedule.id

            async with runtime_factory() as runtime:
                created = await create_scheduled_report(
                    ScheduledReportCreate(
                        name="Shop A report",
                        frequency="daily",
                        schedule_time="09:00",
                        timezone="UTC",
                    ),
                    CurrentUser(shop_a_domain),
                    runtime,
                )
                assert created["name"] == "Shop A report"
                assert created["shop_id"] == shop_a_id
                schedule_id = created["id"]

                updated = await update_scheduled_report(
                    schedule_id,
                    ScheduledReportUpdate(name="Shop A updated report"),
                    CurrentUser(shop_a_domain),
                    runtime,
                )
                assert updated["name"] == "Shop A updated report"
                assert updated["shop_id"] == shop_a_id

                await set_shop_context(runtime, shop_a_domain, shop_a_id)
                own_schedule = await runtime.scalar(
                    select(ScheduledReport).where(ScheduledReport.id == schedule_id)
                )
                other_schedule = await runtime.scalar(
                    select(ScheduledReport).where(ScheduledReport.id == other_schedule_id)
                )
                assert own_schedule is not None
                assert other_schedule is None

                blocked_schedule_update = await runtime.execute(
                    update(ScheduledReport)
                    .where(ScheduledReport.id == other_schedule_id)
                    .values(name="tampered by shop A")
                )
                assert blocked_schedule_update.rowcount == 0

                own_memory = await runtime.scalar(
                    select(MiaMemory).where(MiaMemory.shop_id == shop_a_id)
                )
                other_memory = await runtime.scalar(
                    select(MiaMemory).where(MiaMemory.shop_id == shop_b_id)
                )
                assert own_memory is not None
                assert other_memory is None

                blocked_memory_update = await runtime.execute(
                    update(MiaMemory)
                    .where(MiaMemory.shop_id == shop_b_id)
                    .values(knowledge_json='["tampered by shop A"]')
                )
                assert blocked_memory_update.rowcount == 0
                await runtime.commit()

            async with admin_factory() as admin:
                unchanged_schedule = await admin.scalar(
                    select(ScheduledReport).where(ScheduledReport.id == other_schedule_id)
                )
                unchanged_memory = await admin.scalar(
                    select(MiaMemory).where(MiaMemory.shop_id == shop_b_id)
                )
                assert unchanged_schedule.name == "Other shop report"
                assert unchanged_memory.knowledge_json == '["shop B secret"]'
        finally:
            await runtime_engine.dispose()
            await admin_engine.dispose()

    asyncio.run(scenario())


def test_publishing_actions_are_isolated_under_restricted_postgres_role(monkeypatch):
    """Exercise publishing create/approve/execute with real PostgreSQL RLS."""
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    runtime_url = os.getenv("MIA_RLS_TEST_RUNTIME_URL")
    if not admin_url or not runtime_url:
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL and MIA_RLS_TEST_RUNTIME_URL")
        pytest.skip("Dedicated PostgreSQL RLS URLs are not configured")

    from app.auth.dependencies import CurrentUser
    from app.models.schemas import PublishingAction, Shop
    from app.routers import publishing as publishing_router
    from app.routers.publishing import (
        CreatePublishingRequest,
        approve_publishing_request,
        create_publishing_request,
        execute_publishing_request,
    )
    from app.security.rls import set_shop_context

    async def fake_perform(job, shop, current, db):
        return {"provider": "shopify", "publication_count": 1}

    monkeypatch.setattr(publishing_router, "_perform_action", fake_perform)

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
        shop_a_domain = f"publish-a-{suffix}.myshopify.com"
        shop_b_domain = f"publish-b-{suffix}.myshopify.com"
        try:
            async with admin_factory() as admin:
                shop_a = Shop(
                    shop_domain=shop_a_domain,
                    access_token_encrypted="ci-test-token-a",
                    is_active=True,
                )
                shop_b = Shop(
                    shop_domain=shop_b_domain,
                    access_token_encrypted="ci-test-token-b",
                    is_active=True,
                )
                admin.add_all([shop_a, shop_b])
                await admin.flush()
                other_action = PublishingAction(
                    shop_id=shop_b.id,
                    action_type="shopify_product_channels",
                    status="pending_approval",
                    idempotency_key=f"other-{suffix}",
                    request_hash="b" * 64,
                    payload_json=json.dumps({
                        "product_id": "gid://shopify/Product/999",
                        "publication_ids": ["gid://shopify/Publication/1"],
                        "publish": True,
                    }),
                    attempt_count=0,
                )
                admin.add(other_action)
                await admin.commit()
                shop_a_id = shop_a.id
                shop_b_id = shop_b.id
                other_action_id = other_action.id

            async with runtime_factory() as runtime:
                current = CurrentUser(shop_a_domain)
                request = CreatePublishingRequest(
                    action_type="shopify_product_channels",
                    idempotency_key=f"publishing-rls-{suffix}",
                    payload={
                        "product_id": "gid://shopify/Product/123",
                        "publication_ids": ["gid://shopify/Publication/1"],
                        "publish": True,
                    },
                )
                created = await create_publishing_request(request, current, runtime)
                assert created["action"]["status"] == "pending_approval"
                action_id = created["action"]["id"]

                approved = await approve_publishing_request(action_id, current, runtime)
                assert approved["action"]["status"] == "approved"

                executed = await execute_publishing_request(action_id, current, runtime)
                assert executed["action"]["status"] == "published"
                assert executed["action"]["result"]["provider"] == "shopify"

                await set_shop_context(runtime, shop_a_domain, shop_a_id)
                own_action = await runtime.scalar(
                    select(PublishingAction).where(PublishingAction.id == action_id)
                )
                other_action = await runtime.scalar(
                    select(PublishingAction).where(PublishingAction.id == other_action_id)
                )
                assert own_action is not None
                assert other_action is None

                blocked_update = await runtime.execute(
                    update(PublishingAction)
                    .where(PublishingAction.id == other_action_id)
                    .values(status="published")
                )
                assert blocked_update.rowcount == 0
                await runtime.commit()

            async with admin_factory() as admin:
                unchanged = await admin.scalar(
                    select(PublishingAction).where(PublishingAction.id == other_action_id)
                )
                assert unchanged.status == "pending_approval"
        finally:
            await runtime_engine.dispose()
            await admin_engine.dispose()

    asyncio.run(scenario())


def test_webhook_processing_and_event_deduplication_under_restricted_postgres_role(monkeypatch):
    """A signed webhook sees only its own shop and deduplicates events under RLS."""
    import base64
    import hashlib
    import hmac

    from starlette.requests import Request
    from starlette.responses import Response

    from app.models.schemas import Shop, WebhookEvent
    from app.routers.webhooks import receive_webhook
    from app.security.rls import set_shop_context
    from app.shopify.config import settings

    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    runtime_url = os.getenv("MIA_RLS_TEST_RUNTIME_URL")
    if not admin_url or not runtime_url:
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL and MIA_RLS_TEST_RUNTIME_URL")
        pytest.skip("Dedicated PostgreSQL RLS URLs are not configured")

    secret = "ci-webhook-rls-test-secret"
    monkeypatch.setattr(settings, "shopify_api_secret", secret)

    async def scenario():
        admin_engine = create_async_engine(admin_url, pool_pre_ping=True)
        runtime_engine = create_async_engine(runtime_url, pool_pre_ping=True)
        admin_factory = sessionmaker(bind=admin_engine, class_=AsyncSession, expire_on_commit=False)
        runtime_factory = sessionmaker(bind=runtime_engine, class_=AsyncSession, expire_on_commit=False)
        suffix = uuid.uuid4().hex[:12]
        shop_a_domain = f"webhook-a-{suffix}.myshopify.com"
        shop_b_domain = f"webhook-b-{suffix}.myshopify.com"
        event_a_id = f"webhook-a-event-{suffix}"
        event_b_id = f"webhook-b-event-{suffix}"
        body = json.dumps({"id": 123, "title": "RLS test product"}).encode()
        signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()

        def make_request():
            headers = [
                (b"x-shopify-topic", b"products/create"),
                (b"x-shopify-shop-domain", shop_a_domain.encode()),
                (b"x-shopify-event-id", event_a_id.encode()),
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

        try:
            async with admin_factory() as admin:
                shop_a = Shop(
                    shop_domain=shop_a_domain,
                    access_token_encrypted="ci-webhook-token-a",
                    is_active=True,
                )
                shop_b = Shop(
                    shop_domain=shop_b_domain,
                    access_token_encrypted="ci-webhook-token-b",
                    is_active=True,
                )
                admin.add_all([shop_a, shop_b])
                await admin.flush()
                shop_a_id = shop_a.id
                shop_b_id = shop_b.id
                foreign_event = WebhookEvent(
                    shop_id=shop_b_id,
                    topic="products/create",
                    event_id=event_b_id,
                    payload_json="{}",
                    processed=True,
                )
                admin.add(foreign_event)
                await admin.commit()
                foreign_event_id = foreign_event.id

            async with runtime_factory() as runtime:
                first = await receive_webhook(make_request(), Response(), runtime)
                assert first["processed"] is True

                duplicate = await receive_webhook(make_request(), Response(), runtime)
                assert duplicate["duplicate"] is True

                await set_shop_context(runtime, shop_a_domain, shop_a_id)
                own_event = await runtime.scalar(
                    select(WebhookEvent).where(WebhookEvent.event_id == event_a_id)
                )
                hidden_event = await runtime.scalar(
                    select(WebhookEvent).where(WebhookEvent.id == foreign_event_id)
                )
                assert own_event is not None
                assert hidden_event is None

                blocked_update = await runtime.execute(
                    update(WebhookEvent)
                    .where(WebhookEvent.id == foreign_event_id)
                    .values(processed=False)
                )
                assert blocked_update.rowcount == 0
                await runtime.commit()

            async with admin_factory() as admin:
                unchanged = await admin.scalar(
                    select(WebhookEvent).where(WebhookEvent.id == foreign_event_id)
                )
                assert unchanged.processed is True
        finally:
            await runtime_engine.dispose()
            await admin_engine.dispose()

    asyncio.run(scenario())
