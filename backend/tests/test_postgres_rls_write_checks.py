"""Verify RLS WITH CHECK and runtime insert permissions on tenant-owned data."""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


def test_runtime_role_can_create_own_memory_but_cannot_insert_for_another_shop():
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    runtime_url = os.getenv("MIA_RLS_TEST_RUNTIME_URL")
    if not admin_url or not runtime_url:
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL and MIA_RLS_TEST_RUNTIME_URL")
        pytest.skip("Dedicated PostgreSQL RLS URLs are not configured")

    from app.models.schemas import MiaMemory, Shop
    from app.routers.mia import _memory_row

    from app.security.rls import set_shop_context

    async def scenario():
        admin_engine = create_async_engine(admin_url, pool_pre_ping=True)
        runtime_engine = create_async_engine(runtime_url, pool_pre_ping=True)
        admin_factory = sessionmaker(bind=admin_engine, class_=AsyncSession, expire_on_commit=False)
        runtime_factory = sessionmaker(bind=runtime_engine, class_=AsyncSession, expire_on_commit=False)
        suffix = uuid.uuid4().hex[:12]
        shop_a_domain = f"memory-write-a-{suffix}.myshopify.com"
        shop_b_domain = f"memory-write-b-{suffix}.myshopify.com"
        try:
            async with admin_factory() as admin:
                shop_a = Shop(shop_domain=shop_a_domain, access_token_encrypted="ci-token-a", is_active=True)
                shop_b = Shop(shop_domain=shop_b_domain, access_token_encrypted="ci-token-b", is_active=True)
                admin.add_all([shop_a, shop_b])
                await admin.commit()
                shop_a_id, shop_b_id = shop_a.id, shop_b.id

            async with runtime_factory() as runtime:
                await set_shop_context(runtime, shop_a_domain, shop_a_id)
                own_shop = await runtime.scalar(select(Shop).where(Shop.id == shop_a_id))
                assert own_shop is not None

                # The first conversation request for a shop must be able to create
                # its single MiaMemory row under the restricted runtime role.
                memory = await _memory_row(runtime, own_shop)
                assert memory.shop_id == shop_a_id
                await runtime.commit()

                # A valid shop-A context must not be able to insert a row owned by B.
                await set_shop_context(runtime, shop_a_domain, shop_a_id)
                runtime.add(MiaMemory(
                    shop_id=shop_b_id,
                    conversation_json="[]",
                    knowledge_json='["must not be inserted by shop A"]',
                ))
                with pytest.raises(DBAPIError):
                    await runtime.flush()
                await runtime.rollback()

            async with admin_factory() as admin:
                memory_a = await admin.scalar(select(MiaMemory).where(MiaMemory.shop_id == shop_a_id))
                memory_b = await admin.scalar(select(MiaMemory).where(MiaMemory.shop_id == shop_b_id))
                assert memory_a is not None
                assert memory_a.knowledge_json == "[]"
                assert memory_b is None
        finally:
            await runtime_engine.dispose()
            await admin_engine.dispose()

    asyncio.run(scenario())


def test_shop_rls_allows_install_for_matching_domain_and_rejects_other_domain():
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    runtime_url = os.getenv("MIA_RLS_TEST_RUNTIME_URL")
    if not admin_url or not runtime_url:
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL and MIA_RLS_TEST_RUNTIME_URL")
        pytest.skip("Dedicated PostgreSQL RLS URLs are not configured")

    from app.models.schemas import Shop
    from app.security.rls import set_shop_context

    async def scenario():
        admin_engine = create_async_engine(admin_url, pool_pre_ping=True)
        runtime_engine = create_async_engine(runtime_url, pool_pre_ping=True)
        admin_factory = sessionmaker(bind=admin_engine, class_=AsyncSession, expire_on_commit=False)
        runtime_factory = sessionmaker(bind=runtime_engine, class_=AsyncSession, expire_on_commit=False)
        suffix = uuid.uuid4().hex[:12]
        new_shop_domain = f"install-allowed-{suffix}.myshopify.com"
        foreign_shop_domain = f"install-blocked-{suffix}.myshopify.com"
        owner_domain = f"install-owner-{suffix}.myshopify.com"
        try:
            async with admin_factory() as admin:
                owner = Shop(shop_domain=owner_domain, access_token_encrypted="ci-owner-token", is_active=True)
                admin.add(owner)
                await admin.commit()
                owner_id = owner.id

            async with runtime_factory() as runtime:
                # During first installation there is no shop_id yet; the domain
                # context is the RLS boundary for creating the shops row.
                await set_shop_context(runtime, new_shop_domain)
                new_shop = Shop(
                    shop_domain=new_shop_domain,
                    access_token_encrypted="ci-install-token",
                    is_active=True,
                )
                runtime.add(new_shop)
                await runtime.commit()
                new_shop_id = new_shop.id
                assert new_shop_id is not None

                # The same runtime role cannot create a different domain while
                # operating under the authenticated owner's domain context.
                await set_shop_context(runtime, owner_domain, owner_id)
                runtime.add(Shop(
                    shop_domain=foreign_shop_domain,
                    access_token_encrypted="must-not-be-inserted",
                    is_active=True,
                ))
                with pytest.raises(DBAPIError):
                    await runtime.flush()
                await runtime.rollback()

            async with admin_factory() as admin:
                allowed = await admin.scalar(select(Shop).where(Shop.shop_domain == new_shop_domain))
                blocked = await admin.scalar(select(Shop).where(Shop.shop_domain == foreign_shop_domain))
                assert allowed is not None
                assert blocked is None
        finally:
            await runtime_engine.dispose()
            await admin_engine.dispose()

    asyncio.run(scenario())
