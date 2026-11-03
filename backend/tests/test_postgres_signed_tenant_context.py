"""Prove caller-settable PostgreSQL GUCs cannot forge tenant or scheduler access."""
import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


def test_runtime_role_cannot_forge_tenant_or_scheduler_context():
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    runtime_url = os.getenv("MIA_RLS_TEST_RUNTIME_URL")
    if not admin_url or not runtime_url:
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL and MIA_RLS_TEST_RUNTIME_URL")
        pytest.skip("Dedicated PostgreSQL RLS URLs are not configured")

    from app.models.schemas import AuditLog, ScheduledReport, Shop
    from app.security.rls import set_internal_scheduler_context, set_shop_context

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
        shop_a_domain = f"signed-context-a-{suffix}.myshopify.com"
        shop_b_domain = f"signed-context-b-{suffix}.myshopify.com"
        try:
            async with admin_factory() as admin:
                shop_a = Shop(
                    shop_domain=shop_a_domain,
                    access_token_encrypted="signed-context-token-a",
                    is_active=True,
                )
                shop_b = Shop(
                    shop_domain=shop_b_domain,
                    access_token_encrypted="signed-context-token-b",
                    is_active=True,
                )
                admin.add_all([shop_a, shop_b])
                await admin.flush()
                shop_a_id, shop_b_id = shop_a.id, shop_b.id

                now = datetime.now(timezone.utc).replace(tzinfo=None)
                schedule_a = ScheduledReport(
                    shop_id=shop_a_id,
                    name="Shop A schedule",
                    report_type="sales_summary",
                    frequency="daily",
                    schedule_time="09:00",
                    timezone="UTC",
                    enabled=True,
                    next_run_at=now,
                )
                schedule_b = ScheduledReport(
                    shop_id=shop_b_id,
                    name="Shop B protected schedule",
                    report_type="sales_summary",
                    frequency="daily",
                    schedule_time="09:00",
                    timezone="UTC",
                    enabled=True,
                    next_run_at=now,
                )
                audit_b = AuditLog(
                    shop_id=shop_b_id,
                    action="signed_context.protected",
                    entity_type="customer",
                    entity_id=f"protected-{suffix}",
                    details_json='{"marker":"shop-b-must-survive"}',
                )
                admin.add_all([schedule_a, schedule_b, audit_b])
                await admin.commit()
                schedule_b_id = schedule_b.id
                audit_b_id = audit_b.id

            async with runtime_factory() as runtime:
                # A normal authenticated shop context sees A, not B.
                await set_shop_context(runtime, shop_a_domain, shop_a_id)
                own_shop = await runtime.scalar(
                    select(Shop).where(Shop.id == shop_a_id)
                )
                hidden_shop = await runtime.scalar(
                    select(Shop).where(Shop.id == shop_b_id)
                )
                own_schedule = await runtime.scalar(
                    select(ScheduledReport).where(ScheduledReport.shop_id == shop_a_id)
                )
                hidden_schedule = await runtime.scalar(
                    select(ScheduledReport).where(ScheduledReport.id == schedule_b_id)
                )
                assert own_shop is not None
                assert hidden_shop is None
                assert own_schedule is not None
                assert hidden_schedule is None

                # Simulate arbitrary SQL under the shared runtime role. The
                # current valid signature belongs to A; changing the tenant
                # GUCs and setting the old scheduler flag must not grant B.
                await runtime.execute(
                    text("""
                        SELECT
                            set_config('app.shop_domain', :domain, true),
                            set_config('app.shop_id', :shop_id, true),
                            set_config('app.internal_scheduler', 'true', true)
                    """),
                    {"domain": shop_b_domain, "shop_id": str(shop_b_id)},
                )
                forged_shop = await runtime.scalar(
                    select(Shop).where(Shop.id == shop_b_id)
                )
                forged_schedule = await runtime.scalar(
                    select(ScheduledReport).where(ScheduledReport.id == schedule_b_id)
                )
                assert forged_shop is None
                assert forged_schedule is None

                blocked_update = await runtime.execute(
                    update(ScheduledReport)
                    .where(ScheduledReport.id == schedule_b_id)
                    .values(name="tampered by forged context")
                )
                assert blocked_update.rowcount == 0

                # The GDPR SECURITY DEFINER function must reject the forged
                # tenant even though the caller supplies the other shop ID.
                with pytest.raises(DBAPIError):
                    await runtime.execute(
                        text(
                            "SELECT public.mia_purge_audit_logs_for_privacy("
                            ":shop_id, NULL, NULL, TRUE)"
                        ),
                        {"shop_id": shop_b_id},
                    )
                await runtime.rollback()

                # The shared runtime role cannot read the signing material.
                async with runtime.begin():
                    can_use_private_schema = await runtime.scalar(
                        text("SELECT has_schema_privilege(current_user, 'mia_private', 'USAGE')")
                    )
                    can_read_signing_key = await runtime.scalar(
                        text("SELECT has_table_privilege(current_user, 'mia_private.tenant_context_keys', 'SELECT')")
                    )
                    assert can_use_private_schema is False
                    assert can_read_signing_key is False

                    # A plain scheduler GUC is not enough, but the trusted
                    # application helper supplies the verifiable scheduler proof.
                    await set_internal_scheduler_context(runtime)
                    visible_shops = (await runtime.execute(select(Shop))).scalars().all()
                    visible_schedules = (await runtime.execute(select(ScheduledReport))).scalars().all()
                    assert {shop.id for shop in visible_shops} >= {shop_a_id, shop_b_id}
                    assert {item.id for item in visible_schedules} >= {schedule_b_id}

            async with admin_factory() as admin:
                protected_schedule = await admin.scalar(
                    select(ScheduledReport).where(ScheduledReport.id == schedule_b_id)
                )
                protected_audit = await admin.scalar(
                    select(AuditLog).where(AuditLog.id == audit_b_id)
                )
                assert protected_schedule.name == "Shop B protected schedule"
                assert protected_audit is not None
                assert protected_audit.details_json == '{"marker":"shop-b-must-survive"}'
        finally:
            await runtime_engine.dispose()
            await admin_engine.dispose()

    asyncio.run(scenario())
