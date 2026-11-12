"""Prevent the RLS signing secret from appearing in migration SQLAlchemy errors."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


def test_alembic_migration_engine_hides_bound_parameters_on_error():
    env_path = Path(__file__).resolve().parents[1] / "alembic" / "env.py"
    assert "hide_parameters=True" in env_path.read_text(encoding="utf-8")

    sentinel = "MIA_RLS_CONTEXT_SECRET_TEST_SENTINEL"
    engine = create_engine("sqlite://", hide_parameters=True)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE secret_probe (value TEXT CHECK (value = 'safe'))"
            ))

        with pytest.raises(IntegrityError) as error:
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO secret_probe (value) VALUES (:secret)"),
                    {"secret": sentinel},
                )

        assert sentinel not in str(error.value)
        assert "SQL parameters hidden" in str(error.value)
    finally:
        engine.dispose()

def test_migration_refuses_preexisting_verifier_relation_before_secret_insert():
    """A hostile pre-existing constraint must be rejected before the secret is sent to PostgreSQL."""
    import importlib.util
    import os

    from sqlalchemy import create_engine

    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    if not admin_url:
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL for the PostgreSQL secret-disclosure regression")
        pytest.skip("Dedicated PostgreSQL admin URL is not configured")

    sync_url = admin_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    engine = create_engine(sync_url, hide_parameters=True)
    migration_path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0010_signed_tenant_context.py"
    spec = importlib.util.spec_from_file_location("mia_signed_tenant_context_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    sentinel = "MIA_RLS_CONTEXT_SECRET_TEST_SENTINEL"
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text(
                    "ALTER TABLE mia_private.tenant_context_keys "
                    "RENAME TO tenant_context_keys_before_secret_test"
                ))
                connection.execute(text("""
                    CREATE TABLE mia_private.tenant_context_keys (
                        singleton boolean PRIMARY KEY DEFAULT TRUE CHECK (singleton IS TRUE),
                        secret text NOT NULL CHECK (secret = 'safe-test-value')
                    )
                """))

                runtime_role = connection.dialect.identifier_preparer.quote("mia_runtime_test")
                with pytest.raises(RuntimeError, match="refusing to reuse") as error:
                    migration._create_context_key_table(connection, runtime_role, sentinel)

                assert sentinel not in str(error.value)
                assert connection.scalar(text(
                    "SELECT count(*) FROM mia_private.tenant_context_keys"
                )) == 0
            finally:
                # Restore the CI migration's original verifier table and all dependent objects.
                transaction.rollback()
    finally:
        engine.dispose()

