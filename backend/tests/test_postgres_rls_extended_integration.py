"""Additional RLS isolation tests for product imports and social accounts."""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


def test_product_imports_and_social_accounts_are_isolated_under_restricted_postgres_role():
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    runtime_url = os.getenv("MIA_RLS_TEST_RUNTIME_URL")
    if not admin_url or not runtime_url:
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL and MIA_RLS_TEST_RUNTIME_URL")
        pytest.skip("Dedicated PostgreSQL RLS URLs are not configured")

    from app.models.schemas import ProductImport, Shop, SocialAccount
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
        shop_a_domain = f"imports-a-{suffix}.myshopify.com"
        shop_b_domain = f"imports-b-{suffix}.myshopify.com"
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

                import_a = ProductImport(
                    shop_id=shop_a.id,
                    source="rls-integration-test",
                    supplier_sku=f"sku-a-{suffix}",
                    title="Shop A product",
                )
                import_b = ProductImport(
                    shop_id=shop_b.id,
                    source="rls-integration-test",
                    supplier_sku=f"sku-b-{suffix}",
                    title="Shop B product",
                )
                social_a = SocialAccount(
                    shop_id=shop_a.id,
                    provider="meta",
                    account_type="facebook_page",
                    external_account_id=f"page-a-{suffix}",
                    account_name="Shop A Page",
                    access_token_encrypted="ci-test-token-a",
                    metadata_json="{}",
                    is_active=True,
                )
                social_b = SocialAccount(
                    shop_id=shop_b.id,
                    provider="meta",
                    account_type="facebook_page",
                    external_account_id=f"page-b-{suffix}",
                    account_name="Shop B Page",
                    access_token_encrypted="ci-test-token-b",
                    metadata_json="{}",
                    is_active=True,
                )
                admin.add_all([import_a, import_b, social_a, social_b])
                await admin.commit()
                shop_a_id = shop_a.id
                shop_b_id = shop_b.id
                import_a_id = import_a.id
                import_b_id = import_b.id
                social_a_id = social_a.id
                social_b_id = social_b.id

            async with runtime_factory() as runtime:
                await set_shop_context(runtime, shop_a_domain, shop_a_id)

                own_import = await runtime.scalar(
                    select(ProductImport).where(ProductImport.id == import_a_id)
                )
                hidden_import = await runtime.scalar(
                    select(ProductImport).where(ProductImport.id == import_b_id)
                )
                assert own_import is not None
                assert hidden_import is None

                blocked_import_update = await runtime.execute(
                    update(ProductImport)
                    .where(ProductImport.id == import_b_id)
                    .values(title="tampered by shop A")
                )
                assert blocked_import_update.rowcount == 0

                own_social = await runtime.scalar(
                    select(SocialAccount).where(SocialAccount.id == social_a_id)
                )
                hidden_social = await runtime.scalar(
                    select(SocialAccount).where(SocialAccount.id == social_b_id)
                )
                assert own_social is not None
                assert hidden_social is None

                blocked_social_update = await runtime.execute(
                    update(SocialAccount)
                    .where(SocialAccount.id == social_b_id)
                    .values(account_name="tampered by shop A")
                )
                assert blocked_social_update.rowcount == 0
                await runtime.commit()

            async with admin_factory() as admin:
                unchanged_import = await admin.scalar(
                    select(ProductImport).where(ProductImport.id == import_b_id)
                )
                unchanged_social = await admin.scalar(
                    select(SocialAccount).where(SocialAccount.id == social_b_id)
                )
                assert unchanged_import.title == "Shop B product"
                assert unchanged_social.account_name == "Shop B Page"
                assert unchanged_import.shop_id == shop_b_id
                assert unchanged_social.shop_id == shop_b_id
        finally:
            await runtime_engine.dispose()
            await admin_engine.dispose()

    asyncio.run(scenario())
