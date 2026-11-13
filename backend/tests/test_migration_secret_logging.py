"""Prevent the RLS signing secret from appearing in migration SQLAlchemy errors."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError


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


def test_migration_revokes_named_default_acl_grants_before_secret_insert():
    """A named default-ACL grantee with schema USAGE must not read the verifier secret."""
    import importlib.util
    import os

    from sqlalchemy import create_engine

    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    if not admin_url:
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL for the default-ACL security regression")
        pytest.skip("Dedicated PostgreSQL admin URL is not configured")

    sync_url = admin_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    engine = create_engine(sync_url, hide_parameters=True)
    migration_path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0010_signed_tenant_context.py"
    spec = importlib.util.spec_from_file_location("mia_signed_tenant_context_acl_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    sentinel = "MIA_RLS_CONTEXT_SECRET_ACL_TEST_SENTINEL"
    reader_role_name = f"mia_secret_reader_{uuid.uuid4().hex[:12]}"
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                owner_name = connection.scalar(text("SELECT current_user"))
                preparer = connection.dialect.identifier_preparer
                owner_identifier = preparer.quote(owner_name)
                reader_identifier = preparer.quote(reader_role_name)
                runtime_role = preparer.quote("mia_runtime_test")

                connection.execute(text(f"CREATE ROLE {reader_identifier} NOLOGIN"))
                connection.execute(text(f"GRANT USAGE ON SCHEMA mia_private TO {reader_identifier}"))
                connection.execute(text(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE "
                    f"{owner_identifier} IN SCHEMA mia_private "
                    f"GRANT SELECT ON TABLES TO {reader_identifier}"
                ))

                role_oid = connection.scalar(
                    text("SELECT oid FROM pg_catalog.pg_roles WHERE rolname = :role_name"),
                    {"role_name": reader_role_name},
                )
                assert role_oid is not None
                connection.execute(text(
                    "CREATE TABLE mia_private.default_acl_probe (id integer)"
                ))
                probe_oid = connection.scalar(
                    text("SELECT pg_catalog.to_regclass('mia_private.default_acl_probe')::oid")
                )
                assert probe_oid is not None
                assert connection.scalar(
                    text("SELECT pg_catalog.has_table_privilege(:role_oid, :table_oid, 'SELECT')"),
                    {"role_oid": role_oid, "table_oid": probe_oid},
                ) is True
                connection.execute(text("DROP TABLE mia_private.default_acl_probe"))

                connection.execute(text(
                    "ALTER TABLE mia_private.tenant_context_keys "
                    "RENAME TO tenant_context_keys_before_acl_test"
                ))
                migration._create_context_key_table(connection, runtime_role, sentinel)

                table_oid = connection.scalar(
                    text("SELECT pg_catalog.to_regclass('mia_private.tenant_context_keys')::oid")
                )
                assert role_oid is not None
                assert table_oid is not None
                assert connection.scalar(
                    text("SELECT pg_catalog.has_schema_privilege(:role_oid, 'mia_private', 'USAGE')"),
                    {"role_oid": role_oid},
                ) is True
                assert connection.scalar(
                    text("SELECT pg_catalog.has_table_privilege(:role_oid, :table_oid, 'SELECT')"),
                    {"role_oid": role_oid, "table_oid": table_oid},
                ) is False
                assert connection.scalar(
                    text("SELECT secret FROM mia_private.tenant_context_keys")
                ) == sentinel

                savepoint = connection.begin_nested()
                try:
                    connection.execute(text(f"SET LOCAL ROLE {reader_identifier}"))
                    with pytest.raises(DBAPIError, match="permission denied"):
                        connection.execute(
                            text("SELECT secret FROM mia_private.tenant_context_keys")
                        ).all()
                finally:
                    savepoint.rollback()
            finally:
                # Restore the original verifier table, ACLs, role, and defaults.
                transaction.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize("capability_role", ["pg_read_all_data", "pg_write_all_data"])
def test_migration_rejects_login_membership_in_postgres_data_roles(capability_role):
    """A NOINHERIT login must not retain SET ROLE access to PostgreSQL data-wide roles."""
    import importlib.util
    import os

    from sqlalchemy import create_engine

    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    if not admin_url:
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL for the PostgreSQL capability-role regression")
        pytest.skip("Dedicated PostgreSQL admin URL is not configured")

    sync_url = admin_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    engine = create_engine(sync_url, hide_parameters=True)
    migration_path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0010_signed_tenant_context.py"
    spec = importlib.util.spec_from_file_location("mia_signed_tenant_context_capability_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    sentinel = "MIA_RLS_CONTEXT_SECRET_CAPABILITY_TEST_SENTINEL"
    reader_role_name = f"mia_capability_reader_{uuid.uuid4().hex[:12]}"
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                preparer = connection.dialect.identifier_preparer
                reader_identifier = preparer.quote(reader_role_name)
                runtime_role = preparer.quote("mia_runtime_test")
                capability_identifier = preparer.quote(capability_role)

                connection.execute(text(f"CREATE ROLE {reader_identifier} LOGIN NOINHERIT"))
                connection.execute(text(
                    f"GRANT {capability_identifier} TO {reader_identifier}"
                ))
                assert connection.scalar(
                    text("SELECT pg_catalog.pg_has_role(:reader, :capability, 'MEMBER')"),
                    {"reader": reader_role_name, "capability": capability_role},
                ) is True

                connection.execute(text(
                    "ALTER TABLE mia_private.tenant_context_keys "
                    "RENAME TO tenant_context_keys_before_capability_test"
                ))
                with pytest.raises(
                    RuntimeError,
                    match="non-owner roles retain effective privileges or membership",
                ) as error:
                    migration._create_context_key_table(connection, runtime_role, sentinel)

                assert sentinel not in str(error.value)
                assert connection.scalar(text(
                    "SELECT count(*) FROM mia_private.tenant_context_keys"
                )) == 0
            finally:
                # Restore the original table and remove the temporary role grant.
                transaction.rollback()
    finally:
        engine.dispose()

