import asyncio
import base64
import hashlib
import hmac
import json
import os
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.models.database import get_db
from app.models.schemas import Shop
from app.shopify.config import settings
POSTGRES_ADMIN_URL = os.getenv("POSTGRES_ADMIN_URL")
POSTGRES_RLS_TEST_URL = os.getenv("POSTGRES_RLS_TEST_URL")


pytestmark = pytest.mark.skipif(
    not POSTGRES_ADMIN_URL or not POSTGRES_RLS_TEST_URL,
    reason="PostgreSQL RLS integration environment is not configured",
)


def _signed_headers(topic, shop, event_id, body, secret="test-secret"):
    signature = base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()
    return {
        "X-Shopify-Topic": topic,
        "X-Shopify-Shop-Domain": shop,
        "X-Shopify-Event-Id": event_id,
        "X-Shopify-Hmac-SHA256": signature,
        "Content-Type": "application/json",
    }


async def _session_factory(url):
    engine = create_async_engine(url, poolclass=NullPool, pool_pre_ping=True)
    factory = sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    return engine, factory


@pytest.fixture()
def postgres_rls_client():
    from fastapi.testclient import TestClient
    import main as main_mod

    async def setup():
        admin_engine, admin_factory = await _session_factory(POSTGRES_ADMIN_URL)
        async with admin_factory() as db:
            await db.execute(text("DELETE FROM webhook_events WHERE event_id LIKE 'rls-test-%'"))
            await db.execute(text("DELETE FROM shops WHERE shop_domain = 'rls-test.myshopify.com'"))
            db.add(Shop(shop_domain="rls-test.myshopify.com", is_active=True))
            await db.commit()
        await admin_engine.dispose()

        runtime_engine, runtime_factory = await _session_factory(POSTGRES_RLS_TEST_URL)
        async with runtime_factory() as db:
            await db.execute(text("SELECT set_config('app.shop_domain', 'rls-test.myshopify.com', true)"))
            visible = await db.execute(
                text("SELECT shop_domain FROM shops WHERE shop_domain = 'rls-test.myshopify.com'")
            )
            assert visible.scalar_one() == "rls-test.myshopify.com"
        return runtime_engine, runtime_factory

    engine, factory = asyncio.run(setup())
    original_secret = settings.shopify_api_secret
    settings.shopify_api_secret = "test-secret"

    async def override_get_db():
        async with factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    application = main_mod.create_app()
    application.dependency_overrides[get_db] = override_get_db
    with TestClient(application) as client:
        yield client

    application.dependency_overrides.clear()
    settings.shopify_api_secret = original_secret

    async def cleanup():
        admin_engine, admin_factory = await _session_factory(POSTGRES_ADMIN_URL)
        async with admin_engine.begin() as conn:
            await conn.execute(text("DELETE FROM webhook_events WHERE event_id LIKE 'rls-test-%'"))
            await conn.execute(text("DELETE FROM audit_logs WHERE action = 'rls-test'"))
            await conn.execute(text("DELETE FROM shops WHERE shop_domain = 'rls-test.myshopify.com'"))
        await admin_engine.dispose()
        await engine.dispose()

    asyncio.run(cleanup())


def test_webhook_processes_existing_shop_under_non_owner_rls_role(postgres_rls_client):
    body = json.dumps({"id": 987654321, "title": "RLS Test Product"}).encode()

    response = postgres_rls_client.post(
        "/api/webhooks",
        headers=_signed_headers(
            "products/create",
            "rls-test.myshopify.com",
            "rls-test-event-1",
            body,
        ),
        content=body,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "received", "processed": True}
