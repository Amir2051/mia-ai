"""RLS isolation checks for the remaining merchant-owned tables."""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


def test_remaining_merchant_tables_are_isolated_under_restricted_postgres_role():
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    runtime_url = os.getenv("MIA_RLS_TEST_RUNTIME_URL")
    if not admin_url or not runtime_url:
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL and MIA_RLS_TEST_RUNTIME_URL")
        pytest.skip("Dedicated PostgreSQL RLS URLs are not configured")

    from app.models.schemas import AppSetting, AuditLog, Shop, ShopSession, SyncJob
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
        shop_a_domain = f"remaining-a-{suffix}.myshopify.com"
        shop_b_domain = f"remaining-b-{suffix}.myshopify.com"
        try:
            async with admin_factory() as admin:
                shop_a = Shop(shop_domain=shop_a_domain, access_token_encrypted="ci-test-token-a", is_active=True)
                shop_b = Shop(shop_domain=shop_b_domain, access_token_encrypted="ci-test-token-b", is_active=True)
                admin.add_all([shop_a, shop_b])
                await admin.flush()

                session_a = ShopSession(shop_id=shop_a.id, session_token=f"session-a-{suffix}", state=f"state-a-{suffix}", is_valid=True)
                session_b = ShopSession(shop_id=shop_b.id, session_token=f"session-b-{suffix}", state=f"state-b-{suffix}", is_valid=True)
                sync_a = SyncJob(shop_id=shop_a.id, entity="products", status="queued", details_json="{}")
                sync_b = SyncJob(shop_id=shop_b.id, entity="products", status="queued", details_json="{}")
                setting_a = AppSetting(shop_id=shop_a.id, key=f"setting-a-{suffix}", value_json='{"owner":"shop-a"}')
                setting_b = AppSetting(shop_id=shop_b.id, key=f"setting-b-{suffix}", value_json='{"owner":"shop-b"}')
                audit_a = AuditLog(shop_id=shop_a.id, action="rls.integration.shop_a", entity_type="test", entity_id=suffix, details_json='{"owner":"shop-a"}')
                audit_b = AuditLog(shop_id=shop_b.id, action="rls.integration.shop_b", entity_type="test", entity_id=suffix, details_json='{"owner":"shop-b"}')
                admin.add_all([session_a, session_b, sync_a, sync_b, setting_a, setting_b, audit_a, audit_b])
                await admin.commit()

                shop_a_id, shop_b_id = shop_a.id, shop_b.id
                session_a_id, session_b_id = session_a.id, session_b.id
                sync_a_id, sync_b_id = sync_a.id, sync_b.id
                setting_a_id, setting_b_id = setting_a.id, setting_b.id
                audit_a_id, audit_b_id = audit_a.id, audit_b.id

            async with runtime_factory() as runtime:
                await set_shop_context(runtime, shop_a_domain, shop_a_id)

                own_shop = await runtime.scalar(select(Shop).where(Shop.id == shop_a_id))
                hidden_shop = await runtime.scalar(select(Shop).where(Shop.id == shop_b_id))
                assert own_shop is not None
                assert hidden_shop is None

                own_session = await runtime.scalar(select(ShopSession).where(ShopSession.id == session_a_id))
                hidden_session = await runtime.scalar(select(ShopSession).where(ShopSession.id == session_b_id))
                assert own_session is not None
                assert hidden_session is None
                blocked_session_update = await runtime.execute(
                    update(ShopSession).where(ShopSession.id == session_b_id).values(is_valid=False)
                )
                assert blocked_session_update.rowcount == 0

                own_sync = await runtime.scalar(select(SyncJob).where(SyncJob.id == sync_a_id))
                hidden_sync = await runtime.scalar(select(SyncJob).where(SyncJob.id == sync_b_id))
                assert own_sync is not None
                assert hidden_sync is None
                blocked_sync_update = await runtime.execute(
                    update(SyncJob).where(SyncJob.id == sync_b_id).values(status="failed")
                )
                assert blocked_sync_update.rowcount == 0

                own_setting = await runtime.scalar(select(AppSetting).where(AppSetting.id == setting_a_id))
                hidden_setting = await runtime.scalar(select(AppSetting).where(AppSetting.id == setting_b_id))
                assert own_setting is not None
                assert hidden_setting is None
                blocked_setting_update = await runtime.execute(
                    update(AppSetting).where(AppSetting.id == setting_b_id).values(value_json='{"tampered":true}')
                )
                assert blocked_setting_update.rowcount == 0

                own_audit = await runtime.scalar(select(AuditLog).where(AuditLog.id == audit_a_id))
                hidden_audit = await runtime.scalar(select(AuditLog).where(AuditLog.id == audit_b_id))
                assert own_audit is not None
                assert hidden_audit is None
                # Audit logs are append-only for the runtime role; it intentionally
                # has no UPDATE privilege, so only read isolation is tested here.
                await runtime.commit()

            async with admin_factory() as admin:
                foreign_session = await admin.scalar(select(ShopSession).where(ShopSession.id == session_b_id))
                foreign_sync = await admin.scalar(select(SyncJob).where(SyncJob.id == sync_b_id))
                foreign_setting = await admin.scalar(select(AppSetting).where(AppSetting.id == setting_b_id))
                foreign_audit = await admin.scalar(select(AuditLog).where(AuditLog.id == audit_b_id))
                assert foreign_session.is_valid is True
                assert foreign_sync.status == "queued"
                assert foreign_setting.value_json == '{"owner":"shop-b"}'
                assert foreign_audit.details_json == '{"owner":"shop-b"}'
                assert foreign_session.shop_id == shop_b_id
                assert foreign_sync.shop_id == shop_b_id
                assert foreign_setting.shop_id == shop_b_id
                assert foreign_audit.shop_id == shop_b_id
        finally:
            await runtime_engine.dispose()
            await admin_engine.dispose()

    asyncio.run(scenario())
