import asyncio

import pytest

from app.shopify.config import Settings


def test_postgresql_requires_rls_context_secret_outside_production():
    settings = Settings(
        environment="development",
        database_url="postgresql+asyncpg://mia:example@localhost/mia",
        rls_context_secret="",
    )

    assert settings.validate_runtime_security() == [
        "RLS_CONTEXT_SECRET must contain at least 32 bytes for PostgreSQL"
    ]


def test_sqlite_development_does_not_require_rls_context_secret():
    settings = Settings(
        environment="development",
        database_url="sqlite+aiosqlite:///./test.db",
        rls_context_secret="",
    )

    assert settings.validate_runtime_security() == []


def test_production_validation_uses_normalized_environment_value():
    settings = Settings(
        environment=" Production ",
        database_url="postgresql+asyncpg://mia:example@localhost/mia",
        rls_context_secret="",
    )

    assert settings.is_production is True
    assert "RLS_CONTEXT_SECRET must contain at least 32 bytes" in settings.validate_production()



def test_application_lifespan_rejects_missing_postgres_signing_secret(monkeypatch):
    import main as app_main

    settings = Settings(
        environment="development",
        database_url="postgresql+asyncpg://mia:example@localhost/mia",
        rls_context_secret="",
    )
    monkeypatch.setattr(app_main, "settings", settings)

    async def scenario():
        with pytest.raises(RuntimeError, match="Database security configuration missing"):
            async with app_main.lifespan(None):
                pass

    asyncio.run(scenario())
