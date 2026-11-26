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



def _patch_postgres_verifier_probe(monkeypatch, *, verifier_result):
    from types import SimpleNamespace

    import app.models.database as db_mod
    import app.security.rls as rls_mod

    class FakeTransaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class FakeSession:
        bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        def begin(self):
            return FakeTransaction()

        async def scalar(self, statement):
            assert "mia_is_internal_scheduler" in str(statement)
            return verifier_result

    monkeypatch.setattr(db_mod, "AsyncSessionLocal", lambda: FakeSession())

    async def fake_set_internal_scheduler_context(_db):
        return None

    monkeypatch.setattr(rls_mod, "set_internal_scheduler_context", fake_set_internal_scheduler_context)


def test_application_lifespan_accepts_matching_postgres_signing_secret(monkeypatch):
    import main as app_main

    settings = Settings(
        environment="development",
        database_url="postgresql+asyncpg://mia:example@localhost/mia",
        rls_context_secret="x" * 32,
    )
    monkeypatch.setattr(app_main, "settings", settings)
    _patch_postgres_verifier_probe(monkeypatch, verifier_result=True)

    async def scenario():
        async with app_main.lifespan(None):
            pass

    asyncio.run(scenario())


def test_application_lifespan_rejects_postgres_verifier_key_mismatch(monkeypatch):
    import main as app_main

    settings = Settings(
        environment="development",
        database_url="postgresql+asyncpg://mia:example@localhost/mia",
        rls_context_secret="x" * 32,
    )
    monkeypatch.setattr(app_main, "settings", settings)
    _patch_postgres_verifier_probe(monkeypatch, verifier_result=False)

    async def scenario():
        with pytest.raises(
            RuntimeError,
            match="PostgreSQL signed-context verification failed",
        ):
            async with app_main.lifespan(None):
                pass

    asyncio.run(scenario())
