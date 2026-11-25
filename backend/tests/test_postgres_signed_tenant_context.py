"""Prove caller-settable PostgreSQL GUCs cannot forge tenant or scheduler access."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


def _configured_database_role(env_name: str) -> str:
    url = os.getenv(env_name, "")
    if not url:
        pytest.fail(f"{env_name} must be configured for PostgreSQL role assertions")
    role_name = make_url(url).username
    if not role_name:
        pytest.fail(f"{env_name} must include a PostgreSQL username")
    return role_name


def _quote_role_identifier(role_name: str) -> str:
    return '"' + role_name.replace('"', '""') + '"'


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
        shop_a_id = shop_b_id = None
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
                tenant_signature = await runtime.scalar(
                    text("SELECT current_setting('app.shop_context_signature', true)")
                )
                tenant_txid = await runtime.scalar(
                    text("SELECT pg_catalog.txid_current()")
                )
                assert tenant_signature
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

                # Each legacy caller-settable GUC must be ineffective on its own.
                # First forge only shop_id while the valid signed domain remains A.
                await runtime.execute(
                    text("SELECT set_config('app.shop_id', :shop_id, true)"),
                    {"shop_id": str(shop_b_id)},
                )
                assert await runtime.scalar(select(Shop).where(Shop.id == shop_b_id)) is None
                assert await runtime.scalar(select(Shop).where(Shop.id == shop_a_id)) is not None

                # Next forge shop_domain without changing A's valid signature.
                await runtime.execute(
                    text("SELECT set_config('app.shop_domain', :domain, true)"),
                    {"domain": shop_b_domain},
                )
                assert await runtime.scalar(select(Shop).where(Shop.id == shop_b_id)) is None
                assert await runtime.scalar(select(ScheduledReport).where(ScheduledReport.id == schedule_b_id)) is None

                # A plain scheduler flag, without its HMAC proof, must not grant cross-shop reads.
                await runtime.execute(
                    text("SELECT set_config('app.internal_scheduler', 'true', true)")
                )
                assert await runtime.scalar(select(Shop).where(Shop.id == shop_b_id)) is None
                assert await runtime.scalar(select(ScheduledReport).where(ScheduledReport.id == schedule_b_id)) is None

                # Finally combine all three forged GUCs; the valid signature still belongs to A.
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
                        text("""
                            SELECT pg_catalog.has_table_privilege(
                                current_user,
                                (
                                    SELECT c.oid
                                      FROM pg_catalog.pg_class AS c
                                      JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                                     WHERE n.nspname = 'mia_private'
                                       AND c.relname = 'tenant_context_keys'
                                ),
                                'SELECT'
                            )
                        """)
                    )
                    assert can_use_private_schema is False
                    assert can_read_signing_key is False

                    # A plain scheduler GUC is not enough, but the trusted
                    # application helper supplies the verifiable scheduler proof.
                    await set_internal_scheduler_context(runtime)
                    scheduler_signature = await runtime.scalar(
                        text("SELECT current_setting('app.internal_scheduler_signature', true)")
                    )
                    scheduler_txid = await runtime.scalar(
                        text("SELECT pg_catalog.txid_current()")
                    )
                    assert scheduler_signature
                    visible_shops = (await runtime.execute(select(Shop))).scalars().all()
                    visible_schedules = (await runtime.execute(select(ScheduledReport))).scalars().all()
                    assert {shop.id for shop in visible_shops} >= {shop_a_id, shop_b_id}
                    assert {item.id for item in visible_schedules} >= {schedule_b_id}

                    # The scheduler needs to claim/update due schedules, but
                    # its cross-shop update privilege must not alter merchant-owned
                    # schedule configuration or reassign a report to another shop.
                    with pytest.raises(DBAPIError):
                        async with runtime.begin_nested():
                            await runtime.execute(
                                update(ScheduledReport)
                                .where(ScheduledReport.id == schedule_b_id)
                                .values(name="tampered by scheduler")
                            )
                    with pytest.raises(DBAPIError):
                        async with runtime.begin_nested():
                            await runtime.execute(
                                update(ScheduledReport)
                                .where(ScheduledReport.id == schedule_b_id)
                                .values(shop_id=shop_a_id)
                            )
                    with pytest.raises(DBAPIError):
                        async with runtime.begin_nested():
                            await runtime.execute(
                                update(ScheduledReport)
                                .where(ScheduledReport.id == schedule_b_id)
                                .values(enabled=False)
                            )
                    # The row-level scheduler policy is paired with a BEFORE UPDATE
                    # trigger so schedule-definition fields remain merchant-owned.
                    for protected_values in (
                        {"report_type": "inventory_summary"},
                        {"frequency": "weekly"},
                        {"timezone": "America/Los_Angeles"},
                        {"schedule_time": "10:30"},
                        {"last_result_json": '{"tampered":true}'},
                    ):
                        with pytest.raises(DBAPIError):
                            async with runtime.begin_nested():
                                await runtime.execute(
                                    update(ScheduledReport)
                                    .where(ScheduledReport.id == schedule_b_id)
                                    .values(**protected_values)
                                )

                    claim_time = datetime.now(timezone.utc).replace(tzinfo=None)
                    next_run = claim_time + timedelta(days=1)
                    scheduler_claim = await runtime.execute(
                        update(ScheduledReport)
                        .where(ScheduledReport.id == schedule_b_id)
                        .values(
                            next_run_at=next_run,
                            last_status="running",
                            last_run_at=claim_time,
                            last_error="claim test",
                        )
                    )
                    assert scheduler_claim.rowcount == 1
                    blocked_shop_update = await runtime.execute(
                        update(Shop)
                        .where(Shop.id == shop_b_id)
                        .values(shop_name="tampered by scheduler")
                    )
                    assert blocked_shop_update.rowcount == 0

                # A captured scheduler proof must not grant cross-shop access
                # when replayed in a different PostgreSQL transaction.
                async with runtime.begin():
                    replay_txid = await runtime.scalar(
                        text("SELECT pg_catalog.txid_current()")
                    )
                    assert replay_txid != scheduler_txid
                    await runtime.execute(
                        text("SELECT set_config('app.internal_scheduler', 'true', true)")
                    )
                    await runtime.execute(
                        text("SELECT set_config('app.internal_scheduler_signature', :signature, true)"),
                        {"signature": scheduler_signature},
                    )
                    assert await runtime.scalar(
                        select(Shop).where(Shop.id == shop_b_id)
                    ) is None
                    assert await runtime.scalar(
                        select(ScheduledReport).where(ScheduledReport.id == schedule_b_id)
                    ) is None

                    # A tenant proof captured in an earlier transaction must
                    # not grant access when replayed with the same shop domain.
                    tenant_replay_txid = await runtime.scalar(
                        text("SELECT pg_catalog.txid_current()")
                    )
                    assert tenant_replay_txid != tenant_txid
                    await runtime.execute(
                        text("""
                            SELECT
                                set_config('app.shop_domain', :domain, true),
                                set_config('app.shop_context_signature', :signature, true),
                                set_config('app.shop_id', :shop_id, true)
                        """),
                        {
                            "domain": shop_a_domain,
                            "signature": tenant_signature,
                            "shop_id": str(shop_a_id),
                        },
                    )
                    assert await runtime.scalar(
                        select(Shop).where(Shop.id == shop_a_id)
                    ) is None

            async with admin_factory() as admin:
                protected_schedule = await admin.scalar(
                    select(ScheduledReport).where(ScheduledReport.id == schedule_b_id)
                )
                protected_audit = await admin.scalar(
                    select(AuditLog).where(AuditLog.id == audit_b_id)
                )
                protected_shop = await admin.scalar(
                    select(Shop).where(Shop.id == shop_b_id)
                )
                assert protected_schedule.name == "Shop B protected schedule"
                assert protected_schedule.shop_id == shop_b_id
                assert protected_schedule.enabled is True
                assert protected_schedule.last_result_json is None
                assert protected_schedule.next_run_at == next_run
                assert protected_schedule.last_status == "running"
                assert protected_schedule.last_run_at == claim_time
                assert protected_schedule.last_error == "claim test"
                assert protected_shop.shop_name is None
                assert protected_audit is not None
                assert protected_audit.details_json == '{"marker":"shop-b-must-survive"}'
        finally:
            try:
                if shop_a_id is not None and shop_b_id is not None:
                    async with admin_factory() as cleanup:
                        await cleanup.execute(
                            text("DELETE FROM public.audit_logs WHERE shop_id IN (:shop_a_id, :shop_b_id)"),
                            {"shop_a_id": shop_a_id, "shop_b_id": shop_b_id},
                        )
                        await cleanup.execute(
                            text("DELETE FROM public.shops WHERE id IN (:shop_a_id, :shop_b_id)"),
                            {"shop_a_id": shop_a_id, "shop_b_id": shop_b_id},
                        )
                        await cleanup.commit()
            finally:
                await runtime_engine.dispose()
                await admin_engine.dispose()

    asyncio.run(scenario())



def test_signed_rls_runtime_role_preflight_rejects_unsafe_roles():
    """Exercise the migration's fail-closed role/ownership/privilege checks."""
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    if not admin_url:
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL")
        pytest.skip("Dedicated PostgreSQL admin URL is not configured")

    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0010_signed_tenant_context.py"
    )
    spec = spec_from_file_location("mia_signed_tenant_context_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    async def scenario():
        engine = create_async_engine(admin_url, pool_pre_ping=True)
        suffix = uuid.uuid4().hex[:10]
        safe_role = f"mia_preflight_safe_{suffix}"
        bypass_role = f"mia_preflight_bypass_{suffix}"
        inherit_role = f"mia_preflight_inherit_{suffix}"
        member_role = f"mia_preflight_member_{suffix}"
        intermediate_role = f"mia_preflight_intermediate_{suffix}"
        elevated_role = f"mia_preflight_elevated_{suffix}"
        try:
            async with engine.begin() as admin:
                database_name = await admin.scalar(text("SELECT pg_catalog.current_database()"))
                database_identifier = admin.dialect.identifier_preparer.quote(database_name)
                await admin.execute(text(
                    f'CREATE ROLE "{safe_role}" LOGIN PASSWORD \'ci-only\' '
                    'NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS'
                ))
                await admin.run_sync(
                    lambda connection: migration._verify_runtime_role(connection, safe_role)
                )

                # RLS does not protect TRUNCATE; REFERENCES and TRIGGER are
                # likewise outside the application's restricted DML contract.
                for privilege in ("TRUNCATE", "REFERENCES", "TRIGGER"):
                    savepoint = await admin.begin_nested()
                    await admin.execute(text(
                        f'GRANT {privilege} ON TABLE public.shops TO "{safe_role}"'
                    ))
                    with pytest.raises(RuntimeError, match=privilege):
                        await admin.run_sync(
                            lambda connection: migration._verify_runtime_role(connection, safe_role)
                        )
                    await savepoint.rollback()

                # A column-only REFERENCES grant is also outside the runtime contract.
                column_reference_savepoint = await admin.begin_nested()
                await admin.execute(text(
                    f'GRANT REFERENCES (shop_domain) ON TABLE public.shops TO "{safe_role}"'
                ))
                with pytest.raises(RuntimeError, match="REFERENCES"):
                    await admin.run_sync(
                        lambda connection: migration._verify_runtime_role(connection, safe_role)
                    )
                await column_reference_savepoint.rollback()

                # Runtime must not be able to create objects in any non-system schema.
                schema_savepoint = await admin.begin_nested()
                schema_name = f"pgxtoast_runtime_create_{uuid.uuid4().hex[:10]}"
                await admin.execute(text(f'CREATE SCHEMA "{schema_name}"'))
                await admin.execute(text(
                    f'GRANT CREATE ON SCHEMA "{schema_name}" TO "{safe_role}"'
                ))
                with pytest.raises(RuntimeError, match="CREATE on non-system schemas"):
                    await admin.run_sync(
                        lambda connection: migration._verify_runtime_role(connection, safe_role)
                    )
                await schema_savepoint.rollback()

                # Effective privilege checks must also see grants made to PUBLIC.
                savepoint = await admin.begin_nested()
                await admin.execute(text("GRANT TRUNCATE ON TABLE public.shops TO PUBLIC"))
                with pytest.raises(RuntimeError, match="TRUNCATE"):
                    await admin.run_sync(
                        lambda connection: migration._verify_runtime_role(connection, safe_role)
                    )
                await savepoint.rollback()

                # Exercise every role attribute that the migration requires to be disabled.
                unsafe_role_specs = (
                    ("no_login", "NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS", "LOGIN"),
                    ("superuser", "LOGIN SUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS", "SUPERUSER"),
                    ("createdb", "LOGIN NOSUPERUSER CREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS", "CREATEDB"),
                    ("createrole", "LOGIN NOSUPERUSER NOCREATEDB CREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS", "CREATEROLE"),
                    ("replication", "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT REPLICATION NOBYPASSRLS", "REPLICATION"),
                )
                for label, attributes, expected in unsafe_role_specs:
                    unsafe_role = f"mia_preflight_{label}_{suffix}"
                    await admin.execute(text(
                        f'CREATE ROLE "{unsafe_role}" PASSWORD \'ci-only\' {attributes}'
                    ))
                    with pytest.raises(RuntimeError, match=expected):
                        await admin.run_sync(
                            lambda connection, role_name=unsafe_role: migration._verify_runtime_role(
                                connection, role_name
                            )
                        )
                    await admin.execute(text(f'DROP ROLE "{unsafe_role}"'))

                await admin.execute(text(
                    f'CREATE ROLE "{bypass_role}" LOGIN PASSWORD \'ci-only\' '
                    'NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION BYPASSRLS'
                ))
                with pytest.raises(RuntimeError, match="BYPASSRLS"):
                    await admin.run_sync(
                        lambda connection: migration._verify_runtime_role(connection, bypass_role)
                    )

                await admin.execute(text(
                    f'CREATE ROLE "{inherit_role}" LOGIN PASSWORD \'ci-only\' '
                    'NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT NOREPLICATION NOBYPASSRLS'
                ))
                with pytest.raises(RuntimeError, match="INHERIT"):
                    await admin.run_sync(
                        lambda connection: migration._verify_runtime_role(connection, inherit_role)
                    )

                await admin.execute(text(
                    f'CREATE ROLE "{member_role}" LOGIN PASSWORD \'ci-only\' '
                    'NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS'
                ))
                await admin.execute(text(
                    f'CREATE ROLE "{elevated_role}" NOLOGIN NOSUPERUSER NOCREATEDB '
                    'NOCREATEROLE NOINHERIT NOREPLICATION BYPASSRLS'
                ))
                await admin.execute(text(
                    f'CREATE ROLE "{intermediate_role}" NOLOGIN NOSUPERUSER NOCREATEDB '
                    'NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS'
                ))
                # The elevated role is granted transitively, not directly.
                await admin.execute(text(f'GRANT "{elevated_role}" TO "{intermediate_role}"'))
                await admin.execute(text(f'GRANT "{intermediate_role}" TO "{member_role}"'))
                with pytest.raises(RuntimeError, match="role memberships"):
                    await admin.run_sync(
                        lambda connection: migration._verify_runtime_role(connection, member_role)
                    )
                await admin.execute(text(f'REVOKE "{intermediate_role}" FROM "{member_role}"'))
                await admin.execute(text(f'REVOKE "{elevated_role}" FROM "{intermediate_role}"'))

                # Ownership of an RLS-protected table must fail the preflight.
                savepoint = await admin.begin_nested()
                await admin.execute(text(f'GRANT CREATE ON SCHEMA public TO "{safe_role}"'))
                await admin.execute(text(f'ALTER TABLE public.shops OWNER TO "{safe_role}"'))
                with pytest.raises(RuntimeError, match="RLS-protected tables"):
                    await admin.run_sync(
                        lambda connection: migration._verify_runtime_role(connection, safe_role)
                    )
                await savepoint.rollback()

                # Ownership of the protected private schema must also fail.
                savepoint = await admin.begin_nested()
                await admin.execute(text(f'GRANT CREATE ON DATABASE {database_identifier} TO "{safe_role}"'))
                await admin.execute(text(f'ALTER SCHEMA mia_private OWNER TO "{safe_role}"'))
                with pytest.raises(RuntimeError, match="protected schemas"):
                    await admin.run_sync(
                        lambda connection: migration._verify_runtime_role(connection, safe_role)
                    )
                await savepoint.rollback()

                # Ownership of the verifier table is independently prohibited.
                savepoint = await admin.begin_nested()
                await admin.execute(text(f'GRANT CREATE ON SCHEMA mia_private TO "{safe_role}"'))
                await admin.execute(text(
                    f'ALTER TABLE mia_private.tenant_context_keys OWNER TO "{safe_role}"'
                ))
                with pytest.raises(RuntimeError, match="private verifier objects"):
                    await admin.run_sync(
                        lambda connection: migration._verify_runtime_role(connection, safe_role)
                    )
                await savepoint.rollback()

                # Direct CREATE grants on public/database are not part of the runtime contract.
                savepoint = await admin.begin_nested()
                await admin.execute(text(f'GRANT CREATE ON SCHEMA public TO "{safe_role}"'))
                with pytest.raises(RuntimeError, match="CREATE on non-system schemas"):
                    await admin.run_sync(
                        lambda connection: migration._verify_runtime_role(connection, safe_role)
                    )
                await savepoint.rollback()

                savepoint = await admin.begin_nested()
                await admin.execute(text(f'GRANT CREATE ON DATABASE {database_identifier} TO "{safe_role}"'))
                with pytest.raises(RuntimeError, match="CREATE privilege on the database"):
                    await admin.run_sync(
                        lambda connection: migration._verify_runtime_role(connection, safe_role)
                    )
                await savepoint.rollback()

                # A SECURITY DEFINER function owned by the runtime role is also prohibited.
                savepoint = await admin.begin_nested()
                await admin.execute(text(f'GRANT CREATE ON SCHEMA public TO "{safe_role}"'))
                await admin.execute(text("""
                    CREATE FUNCTION public.mia_preflight_owned_definer()
                    RETURNS integer
                    LANGUAGE sql
                    SECURITY DEFINER
                    AS 'SELECT 1'
                """))
                await admin.execute(text(
                    f'ALTER FUNCTION public.mia_preflight_owned_definer() OWNER TO "{safe_role}"'
                ))
                with pytest.raises(RuntimeError, match="SECURITY DEFINER functions"):
                    await admin.run_sync(
                        lambda connection: migration._verify_runtime_role(connection, safe_role)
                    )
                await savepoint.rollback()

                # Direct schema/table grants must be rejected even without role membership.
                savepoint = await admin.begin_nested()
                await admin.execute(text(f'GRANT USAGE ON SCHEMA mia_private TO "{safe_role}"'))
                with pytest.raises(RuntimeError, match="USAGE or CREATE privilege on mia_private"):
                    await admin.run_sync(
                        lambda connection: migration._verify_runtime_role(connection, safe_role)
                    )
                await savepoint.rollback()

                savepoint = await admin.begin_nested()
                await admin.execute(text(
                    f'GRANT SELECT ON TABLE mia_private.tenant_context_keys TO "{safe_role}"'
                ))
                with pytest.raises(RuntimeError, match="privileges on mia_private.tenant_context_keys"):
                    await admin.run_sync(
                        lambda connection: migration._verify_runtime_role(connection, safe_role)
                    )
                await savepoint.rollback()

                await admin.execute(text(f'DROP ROLE "{safe_role}"'))
                await admin.execute(text(f'DROP ROLE "{bypass_role}"'))
                await admin.execute(text(f'DROP ROLE "{inherit_role}"'))
                await admin.execute(text(f'DROP ROLE "{member_role}"'))
                await admin.execute(text(f'DROP ROLE "{intermediate_role}"'))
                await admin.execute(text(f'DROP ROLE "{elevated_role}"'))
        finally:
            await engine.dispose()

    asyncio.run(scenario())



def test_security_definer_owner_is_least_privilege_and_rls_scoped():
    """Prove SECURITY DEFINER functions run as a restricted role subject to tenant RLS."""
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    runtime_url = os.getenv("MIA_RLS_TEST_RUNTIME_URL")
    if not admin_url or not runtime_url or not os.getenv("DATABASE_URL") or not os.getenv("MIGRATION_DATABASE_URL"):
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL, MIA_RLS_TEST_RUNTIME_URL, DATABASE_URL, and MIGRATION_DATABASE_URL")
        pytest.skip("Dedicated PostgreSQL RLS and migration URLs are not configured")
    runtime_role_name = _configured_database_role("DATABASE_URL")
    migration_role_name = _configured_database_role("MIGRATION_DATABASE_URL")
    if make_url(runtime_url).username != runtime_role_name:
        pytest.fail("MIA_RLS_TEST_RUNTIME_URL must use the DATABASE_URL runtime role")

    from app.models.schemas import AuditLog, Shop
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
        shop_a_domain = f"definer-a-{suffix}.myshopify.com"
        shop_b_domain = f"definer-b-{suffix}.myshopify.com"
        shop_a_id = shop_b_id = audit_a_id = audit_b_id = None
        try:
            async with admin_factory() as admin:
                async with admin.begin():
                    role = (await admin.execute(text("""
                        SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                               rolinherit, rolreplication, rolbypassrls
                          FROM pg_catalog.pg_roles
                         WHERE rolname = 'mia_rls_definer'
                    """))).mappings().one()
                    assert role["rolcanlogin"] is False
                    assert role["rolsuper"] is False
                    assert role["rolcreatedb"] is False
                    assert role["rolcreaterole"] is False
                    assert role["rolinherit"] is False
                    assert role["rolreplication"] is False
                    assert role["rolbypassrls"] is False

                    functions = (await admin.execute(text("""
                        SELECT p.proname, owner.rolname AS owner_name, p.prosecdef
                          FROM pg_catalog.pg_proc AS p
                          JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
                          JOIN pg_catalog.pg_roles AS owner ON owner.oid = p.proowner
                         WHERE n.nspname = 'public'
                           AND p.proname IN (
                               'mia_current_shop_domain',
                               'mia_current_shop_id',
                               'mia_is_internal_scheduler',
                               'mia_purge_audit_logs_for_privacy'
                           )
                    """))).mappings().all()
                    by_name = {row["proname"]: row for row in functions}
                    assert set(by_name) == {
                        "mia_current_shop_domain",
                        "mia_current_shop_id",
                        "mia_is_internal_scheduler",
                        "mia_purge_audit_logs_for_privacy",
                    }
                    for function_name in (
                        "mia_current_shop_domain",
                        "mia_is_internal_scheduler",
                        "mia_purge_audit_logs_for_privacy",
                    ):
                        assert by_name[function_name]["owner_name"] == "mia_rls_definer"
                        assert by_name[function_name]["prosecdef"] is True
                    assert by_name["mia_current_shop_id"]["prosecdef"] is False

                    server_version_num = int(await admin.scalar(text("SHOW server_version_num")))
                    membership_privilege = "SET" if server_version_num >= 160000 else "MEMBER"
                    privileges = (await admin.execute(
                        text("""
                            SELECT
                                pg_catalog.has_table_privilege('mia_rls_definer', 'public.shops', 'SELECT') AS shops_select,
                                pg_catalog.has_table_privilege('mia_rls_definer', 'public.shops', 'UPDATE') AS shops_update,
                                pg_catalog.has_table_privilege('mia_rls_definer', 'public.audit_logs', 'SELECT') AS audit_select,
                                pg_catalog.has_table_privilege('mia_rls_definer', 'public.audit_logs', 'DELETE') AS audit_delete,
                                pg_catalog.has_table_privilege('mia_rls_definer', 'public.audit_logs', 'INSERT') AS audit_insert,
                                pg_catalog.has_table_privilege('mia_rls_definer', 'public.audit_logs', 'UPDATE') AS audit_update,
                                pg_catalog.has_table_privilege('mia_rls_definer', 'mia_private.tenant_context_keys', 'SELECT') AS key_select,
                                pg_catalog.has_table_privilege('mia_rls_definer', 'mia_private.tenant_context_keys', 'INSERT') AS key_insert,
                                pg_catalog.has_table_privilege('mia_rls_definer', 'mia_private.tenant_context_keys', 'UPDATE') AS key_update,
                                pg_catalog.has_table_privilege('mia_rls_definer', 'mia_private.tenant_context_keys', 'DELETE') AS key_delete,
                                pg_catalog.has_schema_privilege('mia_rls_definer', 'public', 'CREATE') AS public_create,
                                pg_catalog.has_database_privilege('mia_rls_definer', pg_catalog.current_database(), 'CREATE') AS database_create,
                                pg_catalog.pg_has_role(:runtime_role, 'mia_rls_definer', 'MEMBER') AS runtime_can_assume_definer,
                                pg_catalog.pg_has_role(:migration_role, 'mia_rls_definer', :membership_privilege) AS migration_can_assume_definer
                        """),
                        {
                            "runtime_role": runtime_role_name,
                            "migration_role": migration_role_name,
                            "membership_privilege": membership_privilege,
                        },
                    )).mappings().one()
                    assert privileges["shops_select"] is True
                    assert privileges["shops_update"] is False
                    assert privileges["audit_select"] is True
                    assert privileges["audit_delete"] is True
                    assert privileges["audit_insert"] is False
                    assert privileges["audit_update"] is False
                    assert privileges["key_select"] is True
                    assert privileges["key_insert"] is False
                    assert privileges["key_update"] is False
                    assert privileges["key_delete"] is False
                    assert privileges["public_create"] is False
                    assert privileges["database_create"] is False
                    assert privileges["runtime_can_assume_definer"] is False
                    assert privileges["migration_can_assume_definer"] is True

                    members = (await admin.execute(text("""
                        SELECT r.rolname
                          FROM pg_catalog.pg_roles AS r
                         WHERE r.oid <> (
                             SELECT oid FROM pg_catalog.pg_roles WHERE rolname = 'mia_rls_definer'
                         )
                           AND r.rolsuper IS FALSE
                           AND pg_catalog.pg_has_role(r.oid, 'mia_rls_definer', 'MEMBER')
                    """))).scalars().all()
                    assert set(members) == {migration_role_name}

                    owned_relations = await admin.scalar(text("""
                        SELECT count(*)
                          FROM pg_catalog.pg_class AS c
                         WHERE c.relowner = (
                             SELECT oid FROM pg_catalog.pg_roles WHERE rolname = 'mia_rls_definer'
                         )
                           AND c.relkind IN ('r', 'p', 'S')
                    """))
                    owned_schemas = await admin.scalar(text("""
                        SELECT count(*)
                          FROM pg_catalog.pg_namespace AS n
                         WHERE n.nspowner = (
                             SELECT oid FROM pg_catalog.pg_roles WHERE rolname = 'mia_rls_definer'
                         )
                    """))
                    assert owned_relations == 0
                    assert owned_schemas == 0

                    shop_a = Shop(
                        shop_domain=shop_a_domain,
                        access_token_encrypted="definer-token-a",
                        is_active=True,
                    )
                    shop_b = Shop(
                        shop_domain=shop_b_domain,
                        access_token_encrypted="definer-token-b",
                        is_active=True,
                    )
                    admin.add_all([shop_a, shop_b])
                    await admin.flush()
                    shop_a_id, shop_b_id = shop_a.id, shop_b.id
                    audit_a = AuditLog(
                        shop_id=shop_a_id,
                        action="definer.test.a",
                        entity_type="customer",
                        entity_id=f"a-{suffix}",
                        details_json='{"marker":"shop-a"}',
                    )
                    audit_b = AuditLog(
                        shop_id=shop_b_id,
                        action="definer.test.b",
                        entity_type="customer",
                        entity_id=f"b-{suffix}",
                        details_json='{"marker":"shop-b"}',
                    )
                    admin.add_all([audit_a, audit_b])
                    await admin.flush()
                    audit_a_id, audit_b_id = audit_a.id, audit_b.id

            # Simulate the actual function owner, not the superuser migration role.
            async with admin_factory() as definer_session:
                async with definer_session.begin():
                    await definer_session.execute(text("SET LOCAL ROLE mia_rls_definer"))
                    assert await definer_session.scalar(text("SELECT current_user")) == "mia_rls_definer"
                    await set_shop_context(definer_session, shop_a_domain, shop_a_id)
                    visible_shops = (await definer_session.execute(select(Shop))).scalars().all()
                    visible_audits = (await definer_session.execute(select(AuditLog))).scalars().all()
                    assert {item.id for item in visible_shops} == {shop_a_id}
                    assert {item.id for item in visible_audits} == {audit_a_id}

                    # The definer role has DELETE, but RLS must still hide Shop B's row.
                    blocked_delete = await definer_session.execute(
                        text("DELETE FROM public.audit_logs WHERE id = :audit_id"),
                        {"audit_id": audit_b_id},
                    )
                    assert blocked_delete.rowcount == 0

            # The runtime role can invoke the GDPR function, but its restricted
            # function owner may delete only the signed tenant's audit records.
            async with runtime_factory() as runtime:
                async with runtime.begin():
                    await set_shop_context(runtime, shop_a_domain, shop_a_id)
                    deleted = await runtime.scalar(
                        text("""
                            SELECT public.mia_purge_audit_logs_for_privacy(
                                :shop_id, NULL, NULL, TRUE
                            )
                        """),
                        {"shop_id": shop_a_id},
                    )
                    assert deleted == 1

            async with admin_factory() as admin:
                async with admin.begin():
                    remaining_a = await admin.scalar(
                        select(AuditLog.id).where(AuditLog.id == audit_a_id)
                    )
                    remaining_b = await admin.scalar(
                        select(AuditLog.id).where(AuditLog.id == audit_b_id)
                    )
                    assert remaining_a is None
                    assert remaining_b == audit_b_id
        finally:
            try:
                if shop_a_id is not None and shop_b_id is not None:
                    async with admin_factory() as cleanup:
                        await cleanup.execute(
                            text("DELETE FROM public.audit_logs WHERE id IN (:audit_a_id, :audit_b_id)"),
                            {"audit_a_id": audit_a_id, "audit_b_id": audit_b_id},
                        )
                        await cleanup.execute(
                            text("DELETE FROM public.shops WHERE id IN (:shop_a_id, :shop_b_id)"),
                            {"shop_a_id": shop_a_id, "shop_b_id": shop_b_id},
                        )
                        await cleanup.commit()
            finally:
                await runtime_engine.dispose()
                await admin_engine.dispose()

    asyncio.run(scenario())


def test_signed_definer_role_preflight_rejects_login_bypass_role():
    """Fail closed if the provisioned SECURITY DEFINER owner can log in or bypass RLS."""
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    if not admin_url:
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL")
        pytest.skip("Dedicated PostgreSQL admin URL is not configured")

    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0010_signed_tenant_context.py"
    )
    spec = spec_from_file_location("mia_signed_tenant_context_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    async def scenario():
        engine = create_async_engine(admin_url, pool_pre_ping=True)
        bad_role = f"mia_bad_definer_{uuid.uuid4().hex[:10]}"
        try:
            async with engine.begin() as admin:
                await admin.execute(text(
                    f'CREATE ROLE "{bad_role}" LOGIN PASSWORD \'ci-only\' '
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION BYPASSRLS"
                ))
                with pytest.raises(RuntimeError, match="LOGIN|BYPASSRLS"):
                    await admin.run_sync(
                        lambda connection: migration._verify_definer_role(connection, bad_role)
                    )
                await admin.execute(text(f'DROP ROLE "{bad_role}"'))
        finally:
            await engine.dispose()

    asyncio.run(scenario())

def test_definer_role_preflight_rejects_unexpected_schema_and_column_grants():
    """Reject non-system schema CREATE and column-only grants outside the allowlist."""
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    if not admin_url or not os.getenv("MIGRATION_DATABASE_URL"):
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL and MIGRATION_DATABASE_URL")
        pytest.skip("Dedicated PostgreSQL admin and migration URLs are not configured")
    migration_role_identifier = _quote_role_identifier(
        _configured_database_role("MIGRATION_DATABASE_URL")
    )

    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0010_signed_tenant_context.py"
    )
    spec = spec_from_file_location("mia_signed_tenant_context_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    async def scenario():
        engine = create_async_engine(admin_url, pool_pre_ping=True)
        suffix = uuid.uuid4().hex[:10]
        schema_name = f"pgxtoast_definer_schema_probe_{suffix}"
        table_name = f"mia_definer_column_probe_{suffix}"
        try:
            async with engine.begin() as admin:
                # A CREATE grant outside public must fail the same preflight.
                schema_savepoint = await admin.begin_nested()
                await admin.execute(text(f'CREATE SCHEMA "{schema_name}"'))
                await admin.execute(text(
                    f'GRANT CREATE ON SCHEMA "{schema_name}" TO mia_rls_definer'
                ))
                await admin.execute(text(f"SET LOCAL ROLE {migration_role_identifier}"))
                with pytest.raises(RuntimeError, match="CREATE on schemas"):
                    await admin.run_sync(
                        lambda connection: migration._verify_definer_role(
                            connection, "mia_rls_definer"
                        )
                    )
                await schema_savepoint.rollback()

                # The owner must not own any non-system schema, even one whose
                # name resembles the PostgreSQL toast prefix.
                owned_schema_savepoint = await admin.begin_nested()
                owned_schema_name = f"pgxtoast_definer_owned_schema_{suffix}"
                await admin.execute(text(
                    f'CREATE SCHEMA "{owned_schema_name}" AUTHORIZATION mia_rls_definer'
                ))
                await admin.execute(text(f"SET LOCAL ROLE {migration_role_identifier}"))
                with pytest.raises(RuntimeError, match="must not own tables, sequences, or schemas"):
                    await admin.run_sync(
                        lambda connection: migration._verify_definer_role(
                            connection, "mia_rls_definer"
                        )
                    )
                await owned_schema_savepoint.rollback()

                # The owner must not own tables in non-system schemas either.
                owned_table_savepoint = await admin.begin_nested()
                owned_table_schema = f"pgxtoast_definer_owned_table_{suffix}"
                owned_table_name = f"owned_{suffix}"
                await admin.execute(text(f'CREATE SCHEMA "{owned_table_schema}"'))
                await admin.execute(text(
                    f'GRANT CREATE ON SCHEMA "{owned_table_schema}" TO mia_rls_definer'
                ))
                await admin.execute(text(
                    f'CREATE TABLE "{owned_table_schema}"."{owned_table_name}" (id bigint)'
                ))
                await admin.execute(text(
                    f'ALTER TABLE "{owned_table_schema}"."{owned_table_name}" OWNER TO mia_rls_definer'
                ))
                await admin.execute(text(
                    f'REVOKE CREATE ON SCHEMA "{owned_table_schema}" FROM mia_rls_definer'
                ))
                await admin.execute(text(f"SET LOCAL ROLE {migration_role_identifier}"))
                with pytest.raises(RuntimeError, match="must not own tables, sequences, or schemas"):
                    await admin.run_sync(
                        lambda connection: migration._verify_definer_role(
                            connection, "mia_rls_definer"
                        )
                    )
                await owned_table_savepoint.rollback()

                # Table-level privilege checks alone miss column-only grants.
                # The owner may read only the explicitly allowlisted tables.
                column_savepoint = await admin.begin_nested()
                column_schema = f"pgxtoast_definer_column_probe_{suffix}"
                await admin.execute(text(f'CREATE SCHEMA "{column_schema}"'))
                await admin.execute(text(
                    f'GRANT USAGE ON SCHEMA "{column_schema}" TO mia_rls_definer'
                ))
                await admin.execute(text(
                    f'CREATE TABLE "{column_schema}"."{table_name}" (id bigint, sensitive_value text)'
                ))
                await admin.execute(text(
                    f'GRANT SELECT (sensitive_value) ON TABLE "{column_schema}"."{table_name}" '
                    'TO mia_rls_definer'
                ))
                with pytest.raises(RuntimeError, match="table/column privileges"):
                    await admin.run_sync(
                        lambda connection: migration._verify_definer_table_privileges(
                            connection, "mia_rls_definer"
                        )
                    )
                await column_savepoint.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())



def _load_signed_context_migration_for_preflight_tests():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0010_signed_tenant_context.py"
    )
    spec = spec_from_file_location("mia_signed_tenant_context_preflight_test", migration_path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_runtime_role_requires_matching_database_targets(monkeypatch):
    migration = _load_signed_context_migration_for_preflight_tests()
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://runtime:runtime-password@pooler.example:6543/mia_app",
    )
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL",
        "postgresql+asyncpg://migrator:migration-password@database.example:5432/mia_other",
    )

    with pytest.raises(RuntimeError, match="must target the same database"):
        migration._runtime_role(None)

    # A pooler hostname and a direct migration hostname may differ while both
    # URLs still target the same PostgreSQL database. The connection-level
    # advisory-lock probe is exercised separately below.
    monkeypatch.setattr(migration, "_verify_same_database_cluster", lambda *_args: None)
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL",
        "postgresql+asyncpg://migrator:migration-password@database.example:5432/mia_app",
    )
    assert migration._runtime_role(None) == "runtime"


def test_downgrade_role_resolution_uses_migration_connection_without_runtime_login(monkeypatch):
    """Rollback can resolve the configured app role without opening DATABASE_URL."""
    migration = _load_signed_context_migration_for_preflight_tests()
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://runtime:runtime-password@disabled.example:6543/mia_app",
    )
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL",
        "postgresql+asyncpg://migrator:migration-password@database.example:5432/mia_app",
    )

    class Result:
        def scalar_one(self):
            return "mia_app"

    class FakeBind:
        def execute(self, statement):
            assert "current_database" in str(statement).lower()
            return Result()

    monkeypatch.setattr(
        migration,
        "_verify_same_database_cluster",
        lambda *_args: pytest.fail("downgrade role resolution must not open DATABASE_URL"),
    )
    assert migration._runtime_role(
        FakeBind(), verify_runtime_endpoint=False
    ) == "runtime"


def test_function_execute_acl_reconciliation_cascades_delegated_grants():
    """Revoking a named grantor also removes EXECUTE grants it delegated."""
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    if not admin_url or not os.getenv("DATABASE_URL") or not os.getenv("MIGRATION_DATABASE_URL"):
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure PostgreSQL admin, runtime, and migration URLs")
        pytest.skip("Dedicated PostgreSQL role URLs are not configured")

    migration = _load_signed_context_migration_for_preflight_tests()
    runtime_role = _configured_database_role("DATABASE_URL")
    suffix = uuid.uuid4().hex[:10]
    grantor = f"mia_acl_a_grantor_{suffix}"
    delegate = f"mia_acl_z_delegate_{suffix}"
    grantor_sql = _quote_role_identifier(grantor)
    delegate_sql = _quote_role_identifier(delegate)
    signature = "mia_purge_audit_logs_for_privacy(bigint, text, text, boolean)"

    async def scenario():
        engine = create_async_engine(admin_url, pool_pre_ping=True)
        try:
            async with engine.begin() as admin:
                await admin.execute(text(f"CREATE ROLE {grantor_sql} NOLOGIN"))
                await admin.execute(text(f"CREATE ROLE {delegate_sql} NOLOGIN"))
                savepoint = await admin.begin_nested()
                try:
                    await admin.execute(text("SET LOCAL ROLE mia_rls_definer"))
                    await admin.execute(text(
                        f"GRANT EXECUTE ON FUNCTION public.{signature} TO {grantor_sql} WITH GRANT OPTION"
                    ))
                    await admin.execute(text("RESET ROLE"))
                    await admin.execute(text(f"SET LOCAL ROLE {grantor_sql}"))
                    await admin.execute(text(
                        f"GRANT EXECUTE ON FUNCTION public.{signature} TO {delegate_sql}"
                    ))
                    await admin.execute(text("RESET ROLE"))

                    await admin.run_sync(
                        lambda connection: migration._reconcile_function_execute_acl(
                            connection,
                            signature,
                            [runtime_role],
                            owner_role="mia_rls_definer",
                        )
                    )
                    grants = (await admin.execute(
                        text("""
                            SELECT
                                pg_catalog.has_function_privilege(:grantor, :signature, 'EXECUTE') AS grantor_can_execute,
                                pg_catalog.has_function_privilege(:delegate, :signature, 'EXECUTE') AS delegate_can_execute,
                                pg_catalog.has_function_privilege(:runtime, :signature, 'EXECUTE') AS runtime_can_execute
                        """),
                        {
                            "grantor": grantor,
                            "delegate": delegate,
                            "runtime": runtime_role,
                            "signature": f"public.{signature}",
                        },
                    )).mappings().one()
                    assert grants["grantor_can_execute"] is False
                    assert grants["delegate_can_execute"] is False
                    assert grants["runtime_can_execute"] is True
                finally:
                    if savepoint.is_active:
                        await savepoint.rollback()
                    await admin.execute(text(
                        f"REVOKE ALL PRIVILEGES ON FUNCTION public.{signature} FROM {grantor_sql} CASCADE"
                    ))
                    await admin.execute(text(f"DROP ROLE IF EXISTS {delegate_sql}"))
                    await admin.execute(text(f"DROP ROLE IF EXISTS {grantor_sql}"))
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_runtime_role_preflight_rejects_non_public_security_definer_ownership():
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    if not admin_url or not os.getenv("DATABASE_URL"):
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL and DATABASE_URL")
        pytest.skip("Dedicated PostgreSQL admin and runtime URLs are not configured")
    runtime_role_name = _configured_database_role("DATABASE_URL")
    runtime_role_identifier = _quote_role_identifier(runtime_role_name)

    migration = _load_signed_context_migration_for_preflight_tests()

    async def scenario():
        engine = create_async_engine(admin_url, pool_pre_ping=True)
        suffix = uuid.uuid4().hex[:10]
        schema_name = f"pgxtoast_runtime_function_probe_{suffix}"
        function_name = f"runtime_owned_definer_{suffix}"
        try:
            async with engine.begin() as admin:
                savepoint = await admin.begin_nested()
                try:
                    await admin.execute(text(f'CREATE SCHEMA "{schema_name}"'))
                    await admin.execute(text(
                        f'GRANT USAGE, CREATE ON SCHEMA "{schema_name}" TO {runtime_role_identifier}'
                    ))
                    await admin.execute(text(
                        f'CREATE FUNCTION "{schema_name}"."{function_name}"() '
                        "RETURNS integer LANGUAGE sql SECURITY DEFINER AS $$ SELECT 1 $$"
                    ))
                    await admin.execute(text(
                        f'ALTER FUNCTION "{schema_name}"."{function_name}"() OWNER TO {runtime_role_identifier}'
                    ))
                    await admin.execute(text(
                        f'REVOKE CREATE ON SCHEMA "{schema_name}" FROM {runtime_role_identifier}'
                    ))
                    with pytest.raises(RuntimeError, match="must not own SECURITY DEFINER functions"):
                        await admin.run_sync(
                            lambda connection: migration._verify_runtime_role(
                                connection, runtime_role_name
                            )
                        )
                finally:
                    if savepoint.is_active:
                        await savepoint.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_runtime_role_preflight_rejects_executable_security_definer_functions():
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    if not admin_url or not os.getenv("DATABASE_URL"):
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL and DATABASE_URL")
        pytest.skip("Dedicated PostgreSQL admin and runtime URLs are not configured")
    runtime_role_name = _configured_database_role("DATABASE_URL")
    runtime_role_identifier = _quote_role_identifier(runtime_role_name)

    migration = _load_signed_context_migration_for_preflight_tests()

    async def scenario():
        engine = create_async_engine(admin_url, pool_pre_ping=True)
        suffix = uuid.uuid4().hex[:10]
        schema_name = f"pgxtoast_executable_definer_{suffix}"
        named_function = f"mia_runtime_named_definer_{suffix}"
        public_function = f"mia_runtime_public_definer_{suffix}"
        try:
            async with engine.begin() as admin:
                savepoint = await admin.begin_nested()
                try:
                    await admin.execute(text(f'CREATE SCHEMA "{schema_name}"'))
                    await admin.execute(text(
                        f'GRANT USAGE ON SCHEMA "{schema_name}" TO {runtime_role_identifier}'
                    ))
                    await admin.execute(text(
                        f'CREATE FUNCTION "{schema_name}"."{named_function}"() '
                        "RETURNS integer LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'"
                    ))
                    await admin.execute(text(
                        f'REVOKE ALL ON FUNCTION "{schema_name}"."{named_function}"() FROM PUBLIC'
                    ))
                    await admin.execute(text(
                        f'GRANT EXECUTE ON FUNCTION "{schema_name}"."{named_function}"() TO {runtime_role_identifier}'
                    ))
                    await admin.execute(text(
                        f'CREATE FUNCTION "{schema_name}"."{public_function}"() '
                        "RETURNS integer LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'"
                    ))
                    await admin.execute(text(
                        f'GRANT EXECUTE ON FUNCTION "{schema_name}"."{public_function}"() TO PUBLIC'
                    ))

                    with pytest.raises(
                        RuntimeError,
                        match="must not execute unreviewed SECURITY DEFINER functions",
                    ) as error:
                        await admin.run_sync(
                            lambda connection: migration._verify_runtime_role(
                                connection, runtime_role_name
                            )
                        )
                    assert named_function in str(error.value)
                    assert public_function in str(error.value)
                finally:
                    if savepoint.is_active:
                        await savepoint.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_definer_role_preflight_rejects_unexpected_function_ownership():
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    if not admin_url or not os.getenv("MIGRATION_DATABASE_URL"):
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL and MIGRATION_DATABASE_URL")
        pytest.skip("Dedicated PostgreSQL admin and migration URLs are not configured")
    migration_role_identifier = _quote_role_identifier(
        _configured_database_role("MIGRATION_DATABASE_URL")
    )

    migration = _load_signed_context_migration_for_preflight_tests()

    async def scenario():
        engine = create_async_engine(admin_url, pool_pre_ping=True)
        schema_name = f"pgxtoast_definer_function_probe_{uuid.uuid4().hex[:10]}"
        function_name = f"mia_unexpected_definer_{uuid.uuid4().hex[:10]}"
        try:
            async with engine.begin() as admin:
                savepoint = await admin.begin_nested()
                try:
                    await admin.execute(text(f'CREATE SCHEMA "{schema_name}"'))
                    await admin.execute(text(
                        f'GRANT CREATE ON SCHEMA "{schema_name}" TO mia_rls_definer'
                    ))
                    await admin.execute(text(
                        f'CREATE FUNCTION "{schema_name}"."{function_name}"() '
                        "RETURNS integer LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'"
                    ))
                    await admin.execute(text(
                        f'ALTER FUNCTION "{schema_name}"."{function_name}"() OWNER TO mia_rls_definer'
                    ))
                    await admin.execute(text(
                        f'REVOKE CREATE ON SCHEMA "{schema_name}" FROM mia_rls_definer'
                    ))
                    await admin.execute(text(f"SET LOCAL ROLE {migration_role_identifier}"))
                    with pytest.raises(RuntimeError, match="must not own unexpected functions"):
                        await admin.run_sync(
                            lambda connection: migration._verify_definer_role(
                                connection, "mia_rls_definer"
                            )
                        )
                finally:
                    if savepoint.is_active:
                        await savepoint.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())

@pytest.mark.parametrize(
    ("runtime_lock_acquired", "runtime_current_user", "should_reject"),
    [
        (False, "runtime", False),
        (True, "runtime", True),
        (False, "pooler_backend", True),
    ],
)
def test_database_cluster_probe_accepts_pooler_direct_pair_and_rejects_other_cluster(
    monkeypatch,
    runtime_lock_acquired,
    runtime_current_user,
    should_reject,
):
    """A same-named database on another cluster must not pass the advisory-lock identity check."""
    from contextlib import nullcontext
    from types import SimpleNamespace

    migration = _load_signed_context_migration_for_preflight_tests()

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

    class FakeBind:
        def __init__(self):
            self.lock_key = None
            self.unlock_key = None

        def execute(self, statement, _params=None):
            sql = str(statement).lower()
            key = (_params["key1"], _params["key2"]) if _params else None
            if "pg_try_advisory_lock" in sql:
                self.lock_key = key
                return Result(True)
            if "pg_advisory_unlock" in sql:
                self.unlock_key = key
                return Result(True)
            return Result(True)

    class FakeRuntimeConnection:
        def __init__(self):
            self.probe_key = None
            self.unlock_key = None

        def execute(self, statement, _params=None):
            sql = str(statement).lower()
            key = (_params["key1"], _params["key2"]) if _params else None
            if "current_database" in sql:
                return Result("mia_app")
            if "current_user" in sql:
                return Result(runtime_current_user)
            if "pg_try_advisory_lock" in sql:
                self.probe_key = key
                return Result(runtime_lock_acquired)
            if "pg_advisory_unlock" in sql:
                self.unlock_key = key
                return Result(True)
            return Result(True)

    fake_runtime = FakeRuntimeConnection()
    fake_engine = SimpleNamespace(
        connect=lambda: nullcontext(fake_runtime),
        dispose=lambda: None,
    )
    monkeypatch.setattr(migration.sa, "create_engine", lambda *_args, **_kwargs: fake_engine)

    runtime_url = "postgresql+asyncpg://runtime:runtime-password@pooler.example:6543/mia_app"
    fake_bind = FakeBind()
    if should_reject:
        expected_message = (
            "unexpected PostgreSQL role"
            if runtime_current_user != "runtime"
            else "different PostgreSQL clusters"
        )
        with pytest.raises(RuntimeError, match=expected_message):
            migration._verify_same_database_cluster(fake_bind, runtime_url)
    else:
        migration._verify_same_database_cluster(fake_bind, runtime_url)

    assert fake_bind.lock_key is not None
    assert fake_bind.unlock_key == fake_bind.lock_key
    if runtime_current_user == "runtime":
        assert fake_runtime.probe_key == fake_bind.lock_key
        if runtime_lock_acquired:
            assert fake_runtime.unlock_key == fake_runtime.probe_key


def test_migration_rejects_case_insensitive_shop_domain_duplicates():
    """Signed domain RLS requires a database-enforced unique normalized shop domain."""
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    if not admin_url:
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL")
        pytest.skip("Dedicated PostgreSQL admin URL is not configured")

    migration = _load_signed_context_migration_for_preflight_tests()

    async def scenario():
        engine = create_async_engine(admin_url, pool_pre_ping=True)
        suffix = uuid.uuid4().hex[:10]
        domain = f"normalized-domain-{suffix}.myshopify.com"
        try:
            async with engine.begin() as admin:
                index_definition = await admin.scalar(text("""
                    SELECT indexdef
                      FROM pg_catalog.pg_indexes
                     WHERE schemaname = 'public'
                       AND indexname = 'uq_shops_shop_domain_lower'
                """))
                assert index_definition is not None
                assert "lower(" in index_definition.lower() and "shop_domain" in index_definition.lower()

                unique_savepoint = await admin.begin_nested()
                try:
                    await admin.execute(text("""
                        INSERT INTO public.shops (shop_domain, installed_at, is_active)
                        VALUES (:domain, CURRENT_TIMESTAMP, TRUE)
                    """), {"domain": domain})
                    with pytest.raises(IntegrityError):
                        await admin.execute(text("""
                            INSERT INTO public.shops (shop_domain, installed_at, is_active)
                            VALUES (:mixed_domain, CURRENT_TIMESTAMP, TRUE)
                        """), {"mixed_domain": domain.upper()})
                finally:
                    await unique_savepoint.rollback()

                duplicate_savepoint = await admin.begin_nested()
                try:
                    await admin.execute(text(
                        "DROP INDEX public.uq_shops_shop_domain_lower"
                    ))
                    await admin.execute(text("""
                        INSERT INTO public.shops (shop_domain, installed_at, is_active)
                        VALUES
                            (:domain, CURRENT_TIMESTAMP, TRUE),
                            (:mixed_domain, CURRENT_TIMESTAMP, TRUE)
                    """), {
                        "domain": domain,
                        "mixed_domain": domain.upper(),
                    })
                    with pytest.raises(RuntimeError, match="duplicate case-insensitive shop domains"):
                        await admin.run_sync(migration._verify_shop_domain_uniqueness)
                finally:
                    await duplicate_savepoint.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_definer_owner_requires_set_role_membership_for_non_superuser_migration():
    """PostgreSQL 16+ migration logins need SET, not merely MEMBER, to manage the owner role."""
    admin_url = os.getenv("MIA_RLS_TEST_ADMIN_URL")
    if not admin_url:
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must configure MIA_RLS_TEST_ADMIN_URL")
        pytest.skip("Dedicated PostgreSQL admin URL is not configured")

    migration = _load_signed_context_migration_for_preflight_tests()

    async def scenario():
        engine = create_async_engine(admin_url, pool_pre_ping=True)
        migration_role = f"mia_migration_no_set_{uuid.uuid4().hex[:10]}"
        try:
            async with engine.begin() as admin:
                version = int(await admin.scalar(text("SHOW server_version_num")))
                if version < 160000:
                    pytest.skip("PostgreSQL SET membership options require PostgreSQL 16 or newer")

                await admin.execute(text(
                    f'CREATE ROLE "{migration_role}" LOGIN PASSWORD \'ci-only\' '
                    'NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS'
                ))
                await admin.execute(text(
                    f'GRANT mia_rls_definer TO "{migration_role}" WITH SET FALSE, INHERIT FALSE'
                ))
                savepoint = await admin.begin_nested()
                await admin.execute(text(f'SET LOCAL ROLE "{migration_role}"'))
                with pytest.raises(RuntimeError, match="SET permission"):
                    await admin.run_sync(
                        lambda connection: migration._verify_definer_role(
                            connection, "mia_rls_definer"
                        )
                    )
                await savepoint.rollback()
                await admin.execute(text(f'DROP ROLE "{migration_role}"'))
        finally:
            await engine.dispose()

    asyncio.run(scenario())

