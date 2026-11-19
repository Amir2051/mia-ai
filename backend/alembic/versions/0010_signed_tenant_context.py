"""Use HMAC-verified PostgreSQL context for tenant and scheduler RLS.

Revision ID: 0010_signed_tenant_context
Revises: 0009_privacy_audit_purge
"""
import os
import secrets
from contextlib import contextmanager

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

revision = "0010_signed_tenant_context"
down_revision = "0009_privacy_audit_purge"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "shop_sessions",
    "product_imports",
    "sync_jobs",
    "webhook_events",
    "app_settings",
    "audit_logs",
    "social_accounts",
    "mia_memory",
    "publishing_actions",
)


DEFINER_ROLE = "mia_rls_definer"


def _verify_same_database_cluster(bind, runtime_url: str) -> None:
    """Prove both URLs reach the same PostgreSQL database without relying on hostnames.

    A random session-level advisory lock is visible to a second connection only
    when it reaches the same PostgreSQL cluster and database. This works when
    one URL uses a pooler and the other uses a direct endpoint, and needs no
    elevated catalog privileges from the restricted runtime role.
    """
    runtime_parsed = make_url(runtime_url)
    if not runtime_parsed.drivername.startswith("postgresql"):
        raise RuntimeError("DATABASE_URL must use PostgreSQL for signed tenant-context migration")

    runtime_sync_url = runtime_parsed.set(drivername="postgresql+psycopg")
    lock_params = None
    for _ in range(5):
        candidate = {
            "key1": secrets.randbelow(2**31 - 1),
            "key2": secrets.randbelow(2**31 - 1),
        }
        if bind.execute(
            sa.text("SELECT pg_catalog.pg_try_advisory_lock(:key1, :key2)"),
            candidate,
        ).scalar_one():
            lock_params = candidate
            break
    if lock_params is None:
        raise RuntimeError("could not acquire a temporary PostgreSQL identity lock")

    runtime_engine = None
    try:
        runtime_engine = sa.create_engine(
            runtime_sync_url,
            poolclass=NullPool,
            hide_parameters=True,
        )
        with runtime_engine.connect() as runtime:
            actual_database = runtime.execute(
                sa.text("SELECT pg_catalog.current_database()")
            ).scalar_one()
            if actual_database != runtime_parsed.database:
                raise RuntimeError("DATABASE_URL connected to an unexpected PostgreSQL database")

            runtime_acquired = runtime.execute(
                sa.text("SELECT pg_catalog.pg_try_advisory_lock(:key1, :key2)"),
                lock_params,
            ).scalar_one()
            if runtime_acquired:
                runtime.execute(
                    sa.text("SELECT pg_catalog.pg_advisory_unlock(:key1, :key2)"),
                    lock_params,
                )
                raise RuntimeError(
                    "DATABASE_URL and MIGRATION_DATABASE_URL resolve to different PostgreSQL clusters/databases"
                )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            "could not verify that DATABASE_URL and MIGRATION_DATABASE_URL reach the same PostgreSQL cluster/database"
        ) from exc
    finally:
        if runtime_engine is not None:
            runtime_engine.dispose()
        bind.execute(
            sa.text("SELECT pg_catalog.pg_advisory_unlock(:key1, :key2)"),
            lock_params,
        )


def _verify_shop_domain_uniqueness(bind) -> None:
    """Require one row per normalized Shopify domain before enabling domain-based RLS."""
    duplicates = bind.execute(sa.text("""
        SELECT pg_catalog.lower(shop_domain) AS normalized_domain, count(*) AS row_count
          FROM public.shops
         GROUP BY pg_catalog.lower(shop_domain)
        HAVING count(*) > 1
         ORDER BY normalized_domain
         LIMIT 10
    """)).mappings().all()
    if duplicates:
        details = ", ".join(
            f"{row['normalized_domain']} ({row['row_count']} rows)"
            for row in duplicates
        )
        raise RuntimeError(
            "public.shops contains duplicate case-insensitive shop domains; "
            "resolve these rows before signed tenant RLS migration: " + details
        )


def _runtime_role(bind) -> str:
    runtime_url = os.environ.get("DATABASE_URL", "")
    migration_url = os.environ.get("MIGRATION_DATABASE_URL", "")
    if not runtime_url or not migration_url:
        raise RuntimeError("DATABASE_URL and MIGRATION_DATABASE_URL are required for signed RLS context")
    runtime_parsed = make_url(runtime_url)
    migration_parsed = make_url(migration_url)
    runtime_user = runtime_parsed.username
    migration_user = migration_parsed.username
    if not runtime_parsed.database or not migration_parsed.database:
        raise RuntimeError("Both database URLs must specify the target database")
    if runtime_parsed.database != migration_parsed.database:
        raise RuntimeError("DATABASE_URL and MIGRATION_DATABASE_URL must target the same database")
    if not runtime_user or not migration_user:
        raise RuntimeError("Both database URLs must include usernames")
    if runtime_user == migration_user:
        raise RuntimeError("DATABASE_URL must use a dedicated runtime role distinct from MIGRATION_DATABASE_URL")
    _verify_same_database_cluster(bind, runtime_url)
    return runtime_user


def _verify_runtime_role(bind, runtime_user: str) -> None:
    """Fail closed unless the app role cannot bypass RLS or reach verifier ownership."""
    role = bind.execute(
        sa.text("""
            SELECT oid, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                   rolinherit, rolreplication, rolbypassrls
              FROM pg_catalog.pg_roles
             WHERE rolname = :runtime_user
        """),
        {"runtime_user": runtime_user},
    ).mappings().one_or_none()
    if role is None:
        raise RuntimeError("DATABASE_URL runtime role does not exist in PostgreSQL")

    unsafe_attributes = [
        name for name, enabled in (
            ("LOGIN", role["rolcanlogin"]),
            ("SUPERUSER", role["rolsuper"]),
            ("CREATEDB", role["rolcreatedb"]),
            ("CREATEROLE", role["rolcreaterole"]),
            ("INHERIT", role["rolinherit"]),
            ("REPLICATION", role["rolreplication"]),
            ("BYPASSRLS", role["rolbypassrls"]),
        )
        if (name == "LOGIN" and not enabled) or (name != "LOGIN" and enabled)
    ]
    if unsafe_attributes:
        raise RuntimeError(
            "DATABASE_URL runtime role must be LOGIN, NOSUPERUSER, NOCREATEDB, "
            "NOCREATEROLE, NOINHERIT, NOREPLICATION, and NOBYPASSRLS; "
            f"unsafe attributes: {', '.join(unsafe_attributes)}"
        )

    # Even NOINHERIT members can SET ROLE on supported PostgreSQL versions.
    # Reject every direct or transitive membership rather than relying on
    # version-specific pg_auth_members.inherit_option/set_option semantics.
    membership_count = bind.execute(
        sa.text("""
            WITH RECURSIVE reachable_roles(role_oid) AS (
                SELECT m.roleid
                  FROM pg_catalog.pg_auth_members AS m
                 WHERE m.member = :runtime_oid
                UNION
                SELECT m.roleid
                  FROM pg_catalog.pg_auth_members AS m
                  JOIN reachable_roles AS r ON m.member = r.role_oid
            )
            SELECT count(*) FROM reachable_roles
        """),
        {"runtime_oid": role["oid"]},
    ).scalar_one()
    if membership_count:
        raise RuntimeError(
            "DATABASE_URL runtime role must have no direct or indirect role memberships; "
            "SET ROLE and inherited role privileges are not permitted"
        )

    protected_tables = sorted(set((*TENANT_TABLES, "shops", "scheduled_reports")))
    owned_tables = bind.execute(
        sa.text("""
            SELECT pg_catalog.format('%I.%I', n.nspname, c.relname)
              FROM pg_catalog.pg_class AS c
              JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public'
               AND c.relname IN :table_names
               AND c.relkind IN ('r', 'p')
               AND c.relowner = :runtime_oid
        """).bindparams(sa.bindparam("table_names", expanding=True)),
        {"table_names": protected_tables, "runtime_oid": role["oid"]},
    ).scalars().all()
    if owned_tables:
        raise RuntimeError(
            "DATABASE_URL runtime role must not own RLS-protected tables: "
            + ", ".join(owned_tables)
        )

    # RLS does not apply to TRUNCATE. REFERENCES and TRIGGER are also outside
    # the runtime DML contract, so reject those effective grants on every
    # public application table (including privileges inherited from PUBLIC).
    table_privilege_rows = bind.execute(
        sa.text("""
            SELECT pg_catalog.format('%I.%I', n.nspname, c.relname) AS table_name,
                   pg_catalog.has_table_privilege(CAST(:runtime_oid AS oid), c.oid, 'TRUNCATE') AS can_truncate,
                   pg_catalog.has_any_column_privilege(CAST(:runtime_oid AS oid), c.oid, 'REFERENCES') AS can_reference,
                   pg_catalog.has_table_privilege(CAST(:runtime_oid AS oid), c.oid, 'TRIGGER') AS can_trigger
              FROM pg_catalog.pg_class AS c
              JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public'
               AND c.relkind IN ('r', 'p')
             ORDER BY c.relname
        """),
        {"runtime_oid": role["oid"]},
    ).mappings().all()
    unsafe_table_grants = [
        f"{row['table_name']}:{privilege}"
        for row in table_privilege_rows
        for privilege, column in (
            ("TRUNCATE", "can_truncate"),
            ("REFERENCES", "can_reference"),
            ("TRIGGER", "can_trigger"),
        )
        if row[column]
    ]
    if unsafe_table_grants:
        raise RuntimeError(
            "DATABASE_URL runtime role must not have TRUNCATE, REFERENCES, or TRIGGER "
            "privileges on public tables: " + ", ".join(unsafe_table_grants)
        )

    owned_schemas = bind.execute(
        sa.text("""
            SELECT nspname
              FROM pg_catalog.pg_namespace
             WHERE nspname IN ('public', 'mia_private')
               AND nspowner = :runtime_oid
             ORDER BY nspname
        """),
        {"runtime_oid": role["oid"]},
    ).scalars().all()
    if owned_schemas:
        raise RuntimeError(
            "DATABASE_URL runtime role must not own protected schemas: "
            + ", ".join(owned_schemas)
        )

    owned_private_objects = bind.execute(
        sa.text("""
            SELECT pg_catalog.format('%I.%I', n.nspname, c.relname)
              FROM pg_catalog.pg_class AS c
              JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
             WHERE n.nspname = 'mia_private'
               AND c.relowner = :runtime_oid
            UNION ALL
            SELECT pg_catalog.format('%I.%I()', n.nspname, p.proname)
              FROM pg_catalog.pg_proc AS p
              JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
             WHERE n.nspname = 'mia_private'
               AND p.proowner = :runtime_oid
        """),
        {"runtime_oid": role["oid"]},
    ).scalars().all()
    if owned_private_objects:
        raise RuntimeError(
            "DATABASE_URL runtime role must not own private verifier objects: "
            + ", ".join(owned_private_objects)
        )

    owned_security_definers = bind.execute(
        sa.text("""
            SELECT p.proname
              FROM pg_catalog.pg_proc AS p
              JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
             WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
               AND n.nspname NOT LIKE 'pg_toast%'
               AND p.proowner = :runtime_oid
               AND p.prosecdef IS TRUE
        """),
        {"runtime_oid": role["oid"]},
    ).scalars().all()
    if owned_security_definers:
        raise RuntimeError(
            "DATABASE_URL runtime role must not own SECURITY DEFINER functions: "
            + ", ".join(owned_security_definers)
        )

    unexpected_create_schemas = bind.execute(
        sa.text("""
            SELECT n.nspname
              FROM pg_catalog.pg_namespace AS n
             WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
               AND n.nspname NOT LIKE 'pg_toast%'
               AND pg_catalog.has_schema_privilege(
                   CAST(:role_oid AS oid), n.oid, 'CREATE'
               )
             ORDER BY n.nspname
        """),
        {"role_oid": role["oid"]},
    ).scalars().all()
    if unexpected_create_schemas:
        raise RuntimeError(
            "DATABASE_URL runtime role must not have CREATE on non-system schemas: "
            + ", ".join(unexpected_create_schemas)
        )

    can_create_database_objects = bind.execute(
        sa.text("""
            SELECT pg_catalog.has_database_privilege(
                CAST(:role_oid AS oid),
                (SELECT oid FROM pg_catalog.pg_database WHERE datname = pg_catalog.current_database()),
                'CREATE'
            )
        """),
        {"role_oid": role["oid"]},
    ).scalar_one()
    if can_create_database_objects:
        raise RuntimeError("DATABASE_URL runtime role must not have CREATE privilege on the database")

    private_schema_oid = bind.execute(
        sa.text("SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'mia_private'")
    ).scalar_one_or_none()
    if private_schema_oid is not None:
        private_schema_access = bind.execute(
            sa.text("""
                SELECT pg_catalog.has_schema_privilege(CAST(:role_oid AS oid), CAST(:schema_oid AS oid), 'USAGE')
                    OR pg_catalog.has_schema_privilege(CAST(:role_oid AS oid), CAST(:schema_oid AS oid), 'CREATE')
            """),
            {"role_oid": role["oid"], "schema_oid": private_schema_oid},
        ).scalar_one()
        if private_schema_access:
            raise RuntimeError(
                "DATABASE_URL runtime role must not have USAGE or CREATE privilege on mia_private"
            )

        key_table_oid = bind.execute(
            sa.text("""
                SELECT c.oid
                  FROM pg_catalog.pg_class AS c
                  JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                 WHERE n.nspname = 'mia_private'
                   AND c.relname = 'tenant_context_keys'
                   AND c.relkind IN ('r', 'p')
            """)
        ).scalar_one_or_none()
        if key_table_oid is not None:
            key_table_access = bind.execute(
                sa.text("""
                    SELECT pg_catalog.has_table_privilege(CAST(:role_oid AS oid), CAST(:table_oid AS oid), 'SELECT')
                        OR pg_catalog.has_table_privilege(CAST(:role_oid AS oid), CAST(:table_oid AS oid), 'INSERT')
                        OR pg_catalog.has_table_privilege(CAST(:role_oid AS oid), CAST(:table_oid AS oid), 'UPDATE')
                        OR pg_catalog.has_table_privilege(CAST(:role_oid AS oid), CAST(:table_oid AS oid), 'DELETE')
                        OR pg_catalog.has_table_privilege(CAST(:role_oid AS oid), CAST(:table_oid AS oid), 'TRUNCATE')
                        OR pg_catalog.has_table_privilege(CAST(:role_oid AS oid), CAST(:table_oid AS oid), 'REFERENCES')
                        OR pg_catalog.has_table_privilege(CAST(:role_oid AS oid), CAST(:table_oid AS oid), 'TRIGGER')
                """),
                {"role_oid": role["oid"], "table_oid": key_table_oid},
            ).scalar_one()
            if key_table_access:
                raise RuntimeError(
                    "DATABASE_URL runtime role must not have privileges on mia_private.tenant_context_keys"
                )


def _verify_definer_role(
    bind,
    role_name: str,
    *,
    allow_private_schema_usage: bool = False,
) -> None:
    """Require a dedicated, non-login role that cannot bypass RLS or be assumed by the app.

    Before upgrade, the definer must have no access to mia_private. After the
    migration has granted the narrowly required USAGE privilege, downgrade
    may allow that USAGE while still rejecting CREATE; the exact verifier-table
    privileges are checked separately before rollback proceeds.
    """
    role = bind.execute(
        sa.text("""
            SELECT oid, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                   rolinherit, rolreplication, rolbypassrls
              FROM pg_catalog.pg_roles
             WHERE rolname = :role_name
        """),
        {"role_name": role_name},
    ).mappings().one_or_none()
    if role is None:
        raise RuntimeError(
            f"required SECURITY DEFINER owner role {role_name!r} is missing; "
            "provision it as NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
            "NOREPLICATION NOBYPASSRLS before migration"
        )

    unsafe_attributes = [
        name for name, enabled in (
            ("LOGIN", role["rolcanlogin"]),
            ("SUPERUSER", role["rolsuper"]),
            ("CREATEDB", role["rolcreatedb"]),
            ("CREATEROLE", role["rolcreaterole"]),
            ("INHERIT", role["rolinherit"]),
            ("REPLICATION", role["rolreplication"]),
            ("BYPASSRLS", role["rolbypassrls"]),
        )
        if enabled
    ]
    if unsafe_attributes:
        raise RuntimeError(
            f"SECURITY DEFINER owner role {role_name!r} must be NOLOGIN, NOSUPERUSER, "
            "NOCREATEDB, NOCREATEROLE, NOINHERIT, NOREPLICATION, and NOBYPASSRLS; "
            f"unsafe attributes: {', '.join(unsafe_attributes)}"
        )

    migration_role = bind.execute(sa.text("""
        SELECT oid, rolsuper
          FROM pg_catalog.pg_roles
         WHERE rolname = current_user
    """)).mappings().one()
    server_version_num = int(bind.execute(sa.text("SHOW server_version_num")).scalar_one())
    membership_privilege = "SET" if server_version_num >= 160000 else "MEMBER"
    can_transfer_ownership = bool(migration_role["rolsuper"]) or bind.execute(
        sa.text("""
            SELECT pg_catalog.pg_has_role(
                CAST(:migration_oid AS oid),
                CAST(:definer_oid AS oid),
                :membership_privilege
            )
        """),
        {
            "migration_oid": migration_role["oid"],
            "definer_oid": role["oid"],
            "membership_privilege": membership_privilege,
        },
    ).scalar_one()
    if not can_transfer_ownership:
        raise RuntimeError(
            f"migration role must be a superuser or have {membership_privilege} permission "
            f"to assume {role_name!r} and manage SECURITY DEFINER functions"
        )

    can_grant_public_create = bind.execute(sa.text("""
        SELECT pg_catalog.has_schema_privilege(
            current_user, 'public', 'CREATE WITH GRANT OPTION'
        )
    """)).scalar_one()
    if not can_grant_public_create:
        raise RuntimeError(
            "migration role must own public or have CREATE WITH GRANT OPTION on the public schema "
            "to transfer SECURITY DEFINER function ownership safely"
        )

    parent_memberships = bind.execute(
        sa.text("""
            WITH RECURSIVE parent_roles(role_oid) AS (
                SELECT m.roleid
                  FROM pg_catalog.pg_auth_members AS m
                 WHERE m.member = :role_oid
                UNION
                SELECT m.roleid
                  FROM pg_catalog.pg_auth_members AS m
                  JOIN parent_roles AS p ON m.member = p.role_oid
            )
            SELECT count(*) FROM parent_roles
        """),
        {"role_oid": role["oid"]},
    ).scalar_one()
    if parent_memberships:
        raise RuntimeError(
            f"SECURITY DEFINER owner role {role_name!r} must not be a member of any other role"
        )

    unexpected_members = bind.execute(
        sa.text("""
            SELECT r.rolname
              FROM pg_catalog.pg_roles AS r
             WHERE r.oid NOT IN (:definer_oid, :migration_oid)
               AND r.rolsuper IS FALSE
               AND pg_catalog.pg_has_role(r.oid, CAST(:definer_oid AS oid), 'MEMBER')
             ORDER BY r.rolname
        """),
        {"definer_oid": role["oid"], "migration_oid": migration_role["oid"]},
    ).scalars().all()
    if unexpected_members:
        raise RuntimeError(
            f"non-superuser roles must not be able to SET ROLE to {role_name!r}: "
            + ", ".join(unexpected_members)
        )

    owned_objects = bind.execute(
        sa.text("""
            SELECT pg_catalog.format('%I.%I', n.nspname, c.relname) AS object_name
              FROM pg_catalog.pg_class AS c
              JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
             WHERE c.relowner = :role_oid
               AND n.nspname NOT IN ('pg_catalog', 'information_schema')
               AND n.nspname NOT LIKE 'pg_toast%'
            UNION ALL
            SELECT n.nspname
              FROM pg_catalog.pg_namespace AS n
             WHERE n.nspowner = :role_oid
               AND n.nspname NOT IN ('pg_catalog', 'information_schema')
               AND n.nspname NOT LIKE 'pg_toast%'
        """),
        {"role_oid": role["oid"]},
    ).scalars().all()
    if owned_objects:
        raise RuntimeError(
            f"SECURITY DEFINER owner role {role_name!r} must not own tables, sequences, "
            "or schemas: " + ", ".join(owned_objects)
        )

    # A pre-existing function owned by this NOLOGIN role could expose its
    # elevated table grants through an unexpected SECURITY DEFINER entry point.
    # Permit only the three functions this migration deliberately assigns.
    owned_functions = bind.execute(sa.text("""
        SELECT pg_catalog.format(
                   '%I.%I(%s)',
                   n.nspname,
                   p.proname,
                   pg_catalog.pg_get_function_identity_arguments(p.oid)
               )
          FROM pg_catalog.pg_proc AS p
          JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
         WHERE p.proowner = :role_oid
           AND n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND n.nspname NOT LIKE 'pg_toast%'
           AND p.oid NOT IN (
               SELECT fn::oid
                 FROM unnest(ARRAY[
                     pg_catalog.to_regprocedure('public.mia_current_shop_domain()'),
                     pg_catalog.to_regprocedure('public.mia_is_internal_scheduler()'),
                     pg_catalog.to_regprocedure(
                         'public.mia_purge_audit_logs_for_privacy(bigint,text,text,boolean)'
                     )
                 ]) AS fn
                WHERE fn IS NOT NULL
           )
         ORDER BY n.nspname, p.proname
    """), {"role_oid": role["oid"]}).scalars().all()
    if owned_functions:
        raise RuntimeError(
            f"SECURITY DEFINER owner role {role_name!r} must not own unexpected functions: "
            + ", ".join(owned_functions)
        )

    can_create_database_objects = bind.execute(
        sa.text("""
            SELECT pg_catalog.has_database_privilege(
                CAST(:role_oid AS oid), pg_catalog.current_database(), 'CREATE'
            )
        """),
        {"role_oid": role["oid"]},
    ).scalar_one()
    if can_create_database_objects:
        raise RuntimeError(
            f"SECURITY DEFINER owner role {role_name!r} must not have CREATE on the database"
        )

    # The definer must not be able to create objects in any application or
    # extension schema. Checking only public would miss a pre-existing CREATE
    # grant on another non-system schema.
    unexpected_create_schemas = bind.execute(
        sa.text("""
            SELECT n.nspname
              FROM pg_catalog.pg_namespace AS n
             WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
               AND n.nspname NOT LIKE 'pg_toast%'
               AND pg_catalog.has_schema_privilege(
                   CAST(:role_oid AS oid), n.oid, 'CREATE'
               )
             ORDER BY n.nspname
        """),
        {"role_oid": role["oid"]},
    ).scalars().all()
    if unexpected_create_schemas:
        raise RuntimeError(
            f"SECURITY DEFINER owner role {role_name!r} must not have CREATE on schemas: "
            + ", ".join(unexpected_create_schemas)
        )

    private_schema_oid = bind.execute(
        sa.text("SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'mia_private'")
    ).scalar_one_or_none()
    if private_schema_oid is not None:
        private_schema_privileges = bind.execute(
            sa.text("""
                SELECT
                    pg_catalog.has_schema_privilege(
                        CAST(:role_oid AS oid), CAST(:schema_oid AS oid), 'USAGE'
                    ) AS can_use,
                    pg_catalog.has_schema_privilege(
                        CAST(:role_oid AS oid), CAST(:schema_oid AS oid), 'CREATE'
                    ) AS can_create
            """),
            {"role_oid": role["oid"], "schema_oid": private_schema_oid},
        ).mappings().one()
        if private_schema_privileges["can_create"] or (
            private_schema_privileges["can_use"] and not allow_private_schema_usage
        ):
            raise RuntimeError(
                f"SECURITY DEFINER owner role {role_name!r} must not have pre-existing "
                "access to mia_private"
            )


def _assign_security_definer_owner(
    bind,
    role_name: str,
    function_signature: str,
    *,
    keep_create: bool = False,
) -> None:
    """Transfer function ownership and temporarily grant schema CREATE when replacement is required."""
    role_identifier = bind.dialect.identifier_preparer.quote(role_name)
    owner_name = bind.execute(
        sa.text("""
            SELECT owner.rolname
              FROM pg_catalog.pg_proc AS function
              JOIN pg_catalog.pg_roles AS owner ON owner.oid = function.proowner
             WHERE function.oid = pg_catalog.to_regprocedure(:signature)::oid
        """),
        {"signature": f"public.{function_signature}"},
    ).scalar_one_or_none()
    if owner_name is None:
        raise RuntimeError(f"function public.{function_signature} does not exist for ownership transfer")

    if owner_name == role_name:
        if keep_create:
            bind.execute(sa.text(f"GRANT CREATE ON SCHEMA public TO {role_identifier}"))
        return

    bind.execute(sa.text(f"GRANT CREATE ON SCHEMA public TO {role_identifier}"))
    bind.execute(sa.text(
        f"ALTER FUNCTION public.{function_signature} OWNER TO {role_identifier}"
    ))
    if not keep_create:
        bind.execute(sa.text(f"REVOKE CREATE ON SCHEMA public FROM {role_identifier}"))
        can_create = bind.execute(
            sa.text("SELECT pg_catalog.has_schema_privilege(:role_name, 'public', 'CREATE')"),
            {"role_name": role_name},
        ).scalar_one()
        if can_create:
            raise RuntimeError(
                f"SECURITY DEFINER owner role {role_name!r} retained CREATE on public after ownership transfer"
            )


@contextmanager
def _as_role(bind, role_name: str):
    """Temporarily SET LOCAL ROLE, restoring the original current_user on success."""
    original_role = bind.execute(sa.text("SELECT current_user")).scalar_one()
    if original_role == role_name:
        yield
        return

    preparer = bind.dialect.identifier_preparer
    bind.execute(sa.text(f"SET LOCAL ROLE {preparer.quote(role_name)}"))
    try:
        yield
    except BaseException:
        # The surrounding migration transaction will roll back the role change.
        raise
    else:
        bind.execute(sa.text(f"SET LOCAL ROLE {preparer.quote(original_role)}"))


def _reconcile_function_execute_acl(
    bind,
    function_signature: str,
    allowed_roles: list[str],
    *,
    owner_role: str | None = None,
) -> None:
    """Remove every non-owner EXECUTE grant, then grant only the explicit callers."""
    def reconcile() -> None:
        function_oid = bind.execute(
            sa.text("SELECT pg_catalog.to_regprocedure(:signature)::oid"),
            {"signature": f"public.{function_signature}"},
        ).scalar_one_or_none()
        if function_oid is None:
            raise RuntimeError(f"function public.{function_signature} does not exist for ACL reconciliation")

        grantees = bind.execute(
            sa.text("""
                SELECT DISTINCT grantee_role.rolname
                  FROM pg_catalog.pg_proc AS function
                  CROSS JOIN LATERAL pg_catalog.aclexplode(
                      COALESCE(
                          function.proacl,
                          pg_catalog.acldefault('f', function.proowner)
                      )
                  ) AS acl
                  JOIN pg_catalog.pg_roles AS grantee_role
                    ON grantee_role.oid = acl.grantee
                 WHERE function.oid = :function_oid
                   AND acl.grantee <> function.proowner
                 ORDER BY grantee_role.rolname
            """),
            {"function_oid": function_oid},
        ).scalars().all()

        preparer = bind.dialect.identifier_preparer
        for grantee in grantees:
            bind.execute(sa.text(
                f"REVOKE ALL PRIVILEGES ON FUNCTION public.{function_signature} "
                f"FROM {preparer.quote(grantee)}"
            ))
        bind.execute(sa.text(
            f"REVOKE ALL PRIVILEGES ON FUNCTION public.{function_signature} FROM PUBLIC"
        ))
        for allowed_role in allowed_roles:
            bind.execute(sa.text(
                f"GRANT EXECUTE ON FUNCTION public.{function_signature} "
                f"TO {preparer.quote(allowed_role)}"
            ))

    if owner_role is None:
        reconcile()
    else:
        with _as_role(bind, owner_role):
            reconcile()


def _verify_definer_table_privileges(bind, role_name: str) -> None:
    """Require only the exact table privileges used by the signed-context functions."""
    role = bind.execute(
        sa.text("SELECT oid FROM pg_catalog.pg_roles WHERE rolname = :role_name"),
        {"role_name": role_name},
    ).mappings().one()
    rows = bind.execute(
        sa.text("""
            SELECT n.nspname, c.relname,
                   pg_catalog.has_any_column_privilege(CAST(:role_oid AS oid), c.oid, 'SELECT') AS can_select,
                   pg_catalog.has_any_column_privilege(CAST(:role_oid AS oid), c.oid, 'INSERT') AS can_insert,
                   pg_catalog.has_any_column_privilege(CAST(:role_oid AS oid), c.oid, 'UPDATE') AS can_update,
                   pg_catalog.has_table_privilege(CAST(:role_oid AS oid), c.oid, 'DELETE') AS can_delete,
                   pg_catalog.has_table_privilege(CAST(:role_oid AS oid), c.oid, 'TRUNCATE') AS can_truncate,
                   pg_catalog.has_any_column_privilege(CAST(:role_oid AS oid), c.oid, 'REFERENCES') AS can_reference,
                   pg_catalog.has_table_privilege(CAST(:role_oid AS oid), c.oid, 'TRIGGER') AS can_trigger
              FROM pg_catalog.pg_class AS c
              JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
             WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
               AND n.nspname NOT IN ('pg_catalog', 'information_schema')
               AND n.nspname NOT LIKE 'pg_toast%'
             ORDER BY n.nspname, c.relname
        """),
        {"role_oid": role["oid"]},
    ).mappings().all()

    expected = {
        ("public", "shops"): {"SELECT"},
        ("public", "audit_logs"): {"SELECT", "DELETE"},
        ("mia_private", "tenant_context_keys"): {"SELECT"},
    }
    columns = {
        "SELECT": "can_select",
        "INSERT": "can_insert",
        "UPDATE": "can_update",
        "DELETE": "can_delete",
        "TRUNCATE": "can_truncate",
        "REFERENCES": "can_reference",
        "TRIGGER": "can_trigger",
    }
    unexpected = []
    for row in rows:
        key = (row["nspname"], row["relname"])
        actual = {privilege for privilege, column in columns.items() if row[column]}
        allowed = expected.get(key, set())
        if actual != allowed:
            unexpected.append(
                f"{row['nspname']}.{row['relname']}="
                f"{','.join(sorted(actual)) or 'none'} (expected {','.join(sorted(allowed)) or 'none'})"
            )
    if unexpected:
        raise RuntimeError(
            f"SECURITY DEFINER owner role {role_name!r} has unexpected effective table/column privileges: "
            + "; ".join(unexpected)
        )


def _revoke_non_owner_table_privileges(bind) -> None:
    """Remove default-ACL grants and fail closed on effective non-owner access.

    A pre-existing schema can have named USAGE grants, and the migration role
    can have default table privileges that grant access to other roles. Revoking
    only PUBLIC and the runtime role is insufficient in that environment.
    """
    table = bind.execute(sa.text("""
        SELECT c.oid, c.relowner
          FROM pg_catalog.pg_class AS c
          JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname = 'mia_private'
           AND c.relname = 'tenant_context_keys'
           AND c.relkind IN ('r', 'p')
    """)).mappings().one_or_none()
    if table is None:
        raise RuntimeError("mia_private.tenant_context_keys was not created")

    grantees = bind.execute(
        sa.text("""
            SELECT DISTINCT acl.grantee AS grantee_oid, roles.rolname AS role_name
              FROM pg_catalog.pg_class AS c
              CROSS JOIN LATERAL pg_catalog.aclexplode(
                  COALESCE(c.relacl, pg_catalog.acldefault('r', c.relowner))
              ) AS acl
              LEFT JOIN pg_catalog.pg_roles AS roles ON roles.oid = acl.grantee
             WHERE c.oid = :table_oid
               AND acl.grantee <> c.relowner
        """),
        {"table_oid": table["oid"]},
    ).mappings().all()

    preparer = bind.dialect.identifier_preparer
    for grantee in grantees:
        if grantee["grantee_oid"] == 0:
            bind.execute(sa.text(
                "REVOKE ALL PRIVILEGES ON TABLE mia_private.tenant_context_keys FROM PUBLIC CASCADE"
            ))
            continue
        role_name = grantee["role_name"]
        if not role_name:
            raise RuntimeError(
                "unable to identify a non-owner grantee on mia_private.tenant_context_keys"
            )
        role_identifier = preparer.quote(role_name)
        bind.execute(sa.text(
            "REVOKE ALL PRIVILEGES ON TABLE mia_private.tenant_context_keys "
            f"FROM {role_identifier} CASCADE"
        ))

    unexpected_roles = bind.execute(
        sa.text("""
            SELECT r.rolname
              FROM pg_catalog.pg_roles AS r
             WHERE r.rolcanlogin IS TRUE
               AND r.rolsuper IS FALSE
               AND r.oid <> :owner_oid
               AND (
                   pg_catalog.has_table_privilege(r.oid, :table_oid, 'SELECT')
                   OR pg_catalog.has_table_privilege(r.oid, :table_oid, 'INSERT')
                   OR pg_catalog.has_table_privilege(r.oid, :table_oid, 'UPDATE')
                   OR pg_catalog.has_table_privilege(r.oid, :table_oid, 'DELETE')
                   OR pg_catalog.has_table_privilege(r.oid, :table_oid, 'TRUNCATE')
                   OR pg_catalog.has_table_privilege(r.oid, :table_oid, 'REFERENCES')
                   OR pg_catalog.has_table_privilege(r.oid, :table_oid, 'TRIGGER')
                   OR pg_catalog.pg_has_role(r.oid, :owner_oid, 'MEMBER')
                   OR EXISTS (
                       SELECT 1
                         FROM pg_catalog.pg_roles AS capability_role
                        WHERE capability_role.rolname IN ('pg_read_all_data', 'pg_write_all_data')
                          AND pg_catalog.pg_has_role(r.oid, capability_role.oid, 'MEMBER')
                   )
               )
             ORDER BY r.rolname
        """),
        {"owner_oid": table["relowner"], "table_oid": table["oid"]},
    ).scalars().all()
    if unexpected_roles:
        raise RuntimeError(
            "non-owner roles retain effective privileges or membership on "
            "mia_private.tenant_context_keys: " + ", ".join(unexpected_roles)
        )


def _create_context_key_table(bind, runtime_role: str, secret: str) -> None:
    """Create a fresh, owner-only verifier table before binding the secret.

    Reusing a pre-existing relation could activate unknown CHECK constraints or
    triggers. Default ACLs on a pre-existing schema could also grant the new
    table to named roles, so all non-owner table privileges are removed and
    effective access is checked before the secret is inserted.
    """
    existing_relation = bind.execute(
        sa.text("SELECT pg_catalog.to_regclass('mia_private.tenant_context_keys')")
    ).scalar_one()
    if existing_relation is not None:
        raise RuntimeError(
            "mia_private.tenant_context_keys already exists; refusing to reuse an existing relation"
        )

    bind.execute(sa.text("""
        CREATE TABLE mia_private.tenant_context_keys (
            singleton boolean PRIMARY KEY DEFAULT TRUE CHECK (singleton IS TRUE),
            secret text NOT NULL
        )
    """))
    bind.execute(sa.text(f"REVOKE ALL ON SCHEMA mia_private FROM {runtime_role}"))
    _revoke_non_owner_table_privileges(bind)
    bind.execute(
        sa.text("""
            INSERT INTO mia_private.tenant_context_keys (singleton, secret)
            VALUES (TRUE, :secret)
        """),
        {"secret": secret},
    )


def upgrade() -> None:
    secret = os.environ.get("RLS_CONTEXT_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("RLS_CONTEXT_SECRET must contain at least 32 bytes before applying signed tenant-context migration")

    bind = op.get_bind()
    runtime_role_name = _runtime_role(bind)
    _verify_runtime_role(bind, runtime_role_name)
    _verify_shop_domain_uniqueness(bind)
    op.execute(
        "CREATE UNIQUE INDEX uq_shops_shop_domain_lower "
        "ON public.shops (pg_catalog.lower(shop_domain))"
    )
    runtime_role = bind.dialect.identifier_preparer.quote(runtime_role_name)
    definer_role = bind.dialect.identifier_preparer.quote(DEFINER_ROLE)

    # pgcrypto may already live in Supabase's extensions schema. Resolve its
    # actual schema rather than assuming public or extensions.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    extension_schema = bind.execute(sa.text("""
        SELECT n.nspname
          FROM pg_catalog.pg_extension AS e
          JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace
         WHERE e.extname = 'pgcrypto'
    """)).scalar_one_or_none()
    if not extension_schema:
        raise RuntimeError("pgcrypto extension is required for HMAC-verified tenant context")
    hmac_schema = bind.dialect.identifier_preparer.quote(extension_schema)

    op.execute("CREATE SCHEMA IF NOT EXISTS mia_private AUTHORIZATION CURRENT_USER")
    op.execute("REVOKE ALL ON SCHEMA mia_private FROM PUBLIC")
    _verify_definer_role(bind, DEFINER_ROLE)
    # A pre-existing verifier relation is rejected before the secret is bound to SQL.
    _create_context_key_table(bind, runtime_role, secret)
    op.execute(f"GRANT USAGE ON SCHEMA mia_private TO {definer_role}")
    op.execute(f"GRANT SELECT ON TABLE mia_private.tenant_context_keys TO {definer_role}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {definer_role}")
    op.execute(f"GRANT SELECT ON TABLE public.shops TO {definer_role}")
    op.execute(f"GRANT SELECT, DELETE ON TABLE public.audit_logs TO {definer_role}")
    op.execute(f"GRANT USAGE ON SCHEMA {hmac_schema} TO {definer_role}")
    op.execute(f"GRANT EXECUTE ON FUNCTION {hmac_schema}.hmac(bytea, bytea, text) TO {definer_role}")
    _verify_definer_table_privileges(bind, DEFINER_ROLE)

    op.execute(f"""
        CREATE OR REPLACE FUNCTION public.mia_current_shop_domain()
        RETURNS text
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_domain text;
            v_signature text;
            v_secret text;
            v_expected text;
        BEGIN
            v_domain := pg_catalog.lower(NULLIF(pg_catalog.current_setting('app.shop_domain', true), ''));
            v_signature := pg_catalog.lower(NULLIF(pg_catalog.current_setting('app.shop_context_signature', true), ''));
            IF v_domain IS NULL OR v_signature IS NULL THEN
                RETURN NULL;
            END IF;

            SELECT k.secret
              INTO v_secret
              FROM mia_private.tenant_context_keys AS k
             WHERE k.singleton IS TRUE;
            IF v_secret IS NULL THEN
                RETURN NULL;
            END IF;

            v_expected := pg_catalog.encode(
                {hmac_schema}.hmac(
                    pg_catalog.convert_to(
                        'mia:shop:v1:' || pg_catalog.txid_current()::text || ':' || v_domain,
                        'UTF8'
                    ),
                    pg_catalog.convert_to(v_secret, 'UTF8'),
                    'sha256'
                ),
                'hex'
            );
            IF v_signature IS DISTINCT FROM v_expected THEN
                RETURN NULL;
            END IF;
            RETURN v_domain;
        END;
        $function$;
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION public.mia_current_shop_id()
        RETURNS bigint
        LANGUAGE plpgsql
        STABLE
        SECURITY INVOKER
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_domain text;
            v_shop_id bigint;
        BEGIN
            v_domain := public.mia_current_shop_domain();
            IF v_domain IS NULL THEN
                RETURN NULL;
            END IF;

            -- Run as the caller so the shops RLS policy remains active. That
            -- policy verifies the signed domain without calling this function.
            SELECT s.id
              INTO v_shop_id
              FROM public.shops AS s
             WHERE pg_catalog.lower(s.shop_domain) = v_domain
             LIMIT 1;
            RETURN v_shop_id;
        END;
        $function$;
    """)

    op.execute(f"""
        CREATE OR REPLACE FUNCTION public.mia_is_internal_scheduler()
        RETURNS boolean
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $function$
        DECLARE
            v_flag text;
            v_signature text;
            v_secret text;
            v_expected text;
        BEGIN
            v_flag := pg_catalog.lower(NULLIF(pg_catalog.current_setting('app.internal_scheduler', true), ''));
            v_signature := pg_catalog.lower(NULLIF(pg_catalog.current_setting('app.internal_scheduler_signature', true), ''));
            IF v_flag IS DISTINCT FROM 'true' OR v_signature IS NULL THEN
                RETURN FALSE;
            END IF;

            SELECT k.secret
              INTO v_secret
              FROM mia_private.tenant_context_keys AS k
             WHERE k.singleton IS TRUE;
            IF v_secret IS NULL THEN
                RETURN FALSE;
            END IF;

            v_expected := pg_catalog.encode(
                {hmac_schema}.hmac(
                    pg_catalog.convert_to(
                    'mia:internal-scheduler:v1:' || pg_catalog.txid_current()::text,
                    'UTF8'
                ),
                    pg_catalog.convert_to(v_secret, 'UTF8'),
                    'sha256'
                ),
                'hex'
            );
            RETURN v_signature = v_expected;
        END;
        $function$;
    """)

    _assign_security_definer_owner(bind, DEFINER_ROLE, "mia_current_shop_domain()")
    _assign_security_definer_owner(bind, DEFINER_ROLE, "mia_is_internal_scheduler()")

    _reconcile_function_execute_acl(
        bind,
        "mia_current_shop_domain()",
        [runtime_role_name],
        owner_role=DEFINER_ROLE,
    )
    _reconcile_function_execute_acl(
        bind,
        "mia_current_shop_id()",
        [runtime_role_name, DEFINER_ROLE],
    )
    _reconcile_function_execute_acl(
        bind,
        "mia_is_internal_scheduler()",
        [runtime_role_name],
        owner_role=DEFINER_ROLE,
    )

    # Replace caller-settable GUC policies with signed-context predicates.
    op.execute("ALTER TABLE public.shops ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS mia_shop_isolation ON public.shops")
    op.execute("""
        CREATE POLICY mia_shop_isolation ON public.shops
        USING (pg_catalog.lower(shop_domain) = (SELECT public.mia_current_shop_domain()))
        WITH CHECK (pg_catalog.lower(shop_domain) = (SELECT public.mia_current_shop_domain()))
    """)
    op.execute("DROP POLICY IF EXISTS mia_shops_scheduler_read ON public.shops")
    op.execute("""
        CREATE POLICY mia_shops_scheduler_read ON public.shops
        FOR SELECT
        USING ((SELECT public.mia_is_internal_scheduler()))
    """)

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS mia_{table}_isolation ON public.{table}")
        op.execute(f"""
            CREATE POLICY mia_{table}_isolation ON public.{table}
            USING (shop_id = (SELECT public.mia_current_shop_id()))
            WITH CHECK (shop_id = (SELECT public.mia_current_shop_id()))
        """)

    op.execute("ALTER TABLE public.scheduled_reports ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS mia_scheduled_reports_isolation ON public.scheduled_reports")
    op.execute("DROP POLICY IF EXISTS mia_scheduled_reports_scheduler ON public.scheduled_reports")
    op.execute("DROP POLICY IF EXISTS mia_scheduled_reports_isolation ON public.scheduled_reports")
    op.execute("DROP POLICY IF EXISTS mia_scheduled_reports_scheduler_read ON public.scheduled_reports")
    op.execute("DROP POLICY IF EXISTS mia_scheduled_reports_scheduler_update ON public.scheduled_reports")
    op.execute("""
        CREATE POLICY mia_scheduled_reports_isolation ON public.scheduled_reports
        USING (shop_id = (SELECT public.mia_current_shop_id()))
        WITH CHECK (shop_id = (SELECT public.mia_current_shop_id()))
    """)
    op.execute("""
        CREATE POLICY mia_scheduled_reports_scheduler_read ON public.scheduled_reports
        FOR SELECT
        USING ((SELECT public.mia_is_internal_scheduler()))
    """)
    op.execute("""
        CREATE POLICY mia_scheduled_reports_scheduler_update ON public.scheduled_reports
        FOR UPDATE
        USING ((SELECT public.mia_is_internal_scheduler()))
        WITH CHECK ((SELECT public.mia_is_internal_scheduler()))
    """)

    # RLS policies authorize rows, not individual columns. Keep the trusted
    # scheduler's cross-shop UPDATE capability limited to claim/status fields.
    op.execute("""
        CREATE OR REPLACE FUNCTION public.mia_guard_scheduled_report_scheduler_update()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            -- Use a positive allowlist so newly added report columns are protected by default.
            -- The scheduler may only change the fields needed to claim and report execution.
            IF public.mia_is_internal_scheduler() AND (
                (pg_catalog.to_jsonb(NEW) - ARRAY[
                    'next_run_at', 'last_run_at', 'last_status', 'last_error', 'updated_at'
                ]) IS DISTINCT FROM
                (pg_catalog.to_jsonb(OLD) - ARRAY[
                    'next_run_at', 'last_run_at', 'last_status', 'last_error', 'updated_at'
                ])
            ) THEN
                RAISE EXCEPTION 'internal scheduler may only update report claim/status fields'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END;
        $function$;
    """)
    op.execute("REVOKE ALL ON FUNCTION public.mia_guard_scheduled_report_scheduler_update() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.mia_guard_scheduled_report_scheduler_update() TO {runtime_role}")
    op.execute("""
        DROP TRIGGER IF EXISTS mia_guard_scheduler_report_update ON public.scheduled_reports
    """)
    op.execute("""
        CREATE TRIGGER mia_guard_scheduler_report_update
        BEFORE UPDATE ON public.scheduled_reports
        FOR EACH ROW
        EXECUTE FUNCTION public.mia_guard_scheduled_report_scheduler_update()
    """)

    # Re-bind the existing 0009 GDPR function before replacing it. The
    # restricted owner receives CREATE only for this transaction's replacement.
    purge_signature = "mia_purge_audit_logs_for_privacy(bigint, text, text, boolean)"
    _assign_security_definer_owner(
        bind,
        DEFINER_ROLE,
        purge_signature,
        keep_create=True,
    )
    with _as_role(bind, DEFINER_ROLE):
        bind.execute(sa.text("""
            CREATE OR REPLACE FUNCTION public.mia_purge_audit_logs_for_privacy(
                p_shop_id bigint,
                p_customer_id text,
                p_customer_email text,
                p_all_shop boolean
            )
            RETURNS integer
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog
            AS $function$
            DECLARE
                v_shop_domain text;
                v_deleted integer;
            BEGIN
                IF p_shop_id IS NULL
                   OR public.mia_current_shop_id() IS DISTINCT FROM p_shop_id THEN
                    RAISE EXCEPTION 'privacy audit purge requires verified shop context'
                        USING ERRCODE = '42501';
                END IF;

                SELECT pg_catalog.lower(s.shop_domain)
                  INTO v_shop_domain
                  FROM public.shops AS s
                 WHERE s.id = p_shop_id;

                IF v_shop_domain IS NULL
                   OR public.mia_current_shop_domain() IS DISTINCT FROM v_shop_domain THEN
                    RAISE EXCEPTION 'privacy audit purge requires matching signed shop domain'
                        USING ERRCODE = '42501';
                END IF;

                IF p_all_shop THEN
                    DELETE FROM public.audit_logs WHERE shop_id = p_shop_id;
                ELSE
                    DELETE FROM public.audit_logs AS a
                     WHERE a.shop_id = p_shop_id
                       AND (
                           (NULLIF(p_customer_id, '') IS NOT NULL
                            AND (pg_catalog.strpos(pg_catalog.lower(COALESCE(a.details_json, '')), pg_catalog.lower(p_customer_id)) > 0
                                 OR pg_catalog.lower(a.entity_id) = pg_catalog.lower(p_customer_id)))
                           OR
                           (NULLIF(p_customer_email, '') IS NOT NULL
                            AND (pg_catalog.strpos(pg_catalog.lower(COALESCE(a.details_json, '')), pg_catalog.lower(p_customer_email)) > 0
                                 OR pg_catalog.lower(a.entity_id) = pg_catalog.lower(p_customer_email)))
                       );
                END IF;

                GET DIAGNOSTICS v_deleted = ROW_COUNT;
                RETURN v_deleted;
            END;
            $function$;
        """))

    bind.execute(sa.text(f"REVOKE CREATE ON SCHEMA public FROM {definer_role}"))
    if bind.execute(
        sa.text("SELECT pg_catalog.has_schema_privilege(:role_name, 'public', 'CREATE')"),
        {"role_name": DEFINER_ROLE},
    ).scalar_one():
        raise RuntimeError("SECURITY DEFINER owner role retained CREATE on public after GDPR function replacement")

    _reconcile_function_execute_acl(
        bind,
        purge_signature,
        [runtime_role_name],
        owner_role=DEFINER_ROLE,
    )


def downgrade() -> None:
    # Explicit downgrade restores the previous GUC-only policies. Use only as
    # part of a reviewed rollback because it reintroduces the documented gap.
    bind = op.get_bind()
    runtime_role_name = _runtime_role(bind)
    definer_role = bind.dialect.identifier_preparer.quote(DEFINER_ROLE)
    # The installed function owner intentionally has USAGE on mia_private and
    # SELECT on its verifier table. Permit that existing runtime grant during
    # rollback, but still require the exact table/column privilege allowlist.
    _verify_definer_role(
        bind,
        DEFINER_ROLE,
        allow_private_schema_usage=True,
    )
    _verify_definer_table_privileges(bind, DEFINER_ROLE)
    op.execute("DROP TRIGGER IF EXISTS mia_guard_scheduler_report_update ON public.scheduled_reports")
    op.execute("DROP FUNCTION IF EXISTS public.mia_guard_scheduled_report_scheduler_update()")
    op.execute("DROP POLICY IF EXISTS mia_shop_isolation ON public.shops")
    op.execute("""
        CREATE POLICY mia_shop_isolation ON public.shops
        USING (shop_domain = current_setting('app.shop_domain', true))
        WITH CHECK (shop_domain = current_setting('app.shop_domain', true))
    """)
    op.execute("DROP POLICY IF EXISTS mia_shops_scheduler_read ON public.shops")
    op.execute("""
        CREATE POLICY mia_shops_scheduler_read ON public.shops
        FOR SELECT USING (current_setting('app.internal_scheduler', true) = 'true')
    """)

    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS mia_{table}_isolation ON public.{table}")
        op.execute(f"""
            CREATE POLICY mia_{table}_isolation ON public.{table}
            USING (shop_id = NULLIF(current_setting('app.shop_id', true), '')::bigint)
            WITH CHECK (shop_id = NULLIF(current_setting('app.shop_id', true), '')::bigint)
        """)

    op.execute("DROP POLICY IF EXISTS mia_scheduled_reports_scheduler_read ON public.scheduled_reports")
    op.execute("DROP POLICY IF EXISTS mia_scheduled_reports_scheduler_update ON public.scheduled_reports")
    op.execute("DROP POLICY IF EXISTS mia_scheduled_reports_isolation ON public.scheduled_reports")
    op.execute("""
        CREATE POLICY mia_scheduled_reports_isolation ON public.scheduled_reports
        USING (shop_id = NULLIF(current_setting('app.shop_id', true), '')::bigint)
        WITH CHECK (shop_id = NULLIF(current_setting('app.shop_id', true), '')::bigint)
    """)
    op.execute("""
        CREATE POLICY mia_scheduled_reports_scheduler ON public.scheduled_reports
        USING (current_setting('app.internal_scheduler', true) = 'true')
        WITH CHECK (current_setting('app.internal_scheduler', true) = 'true')
    """)

    bind.execute(sa.text(f"GRANT CREATE ON SCHEMA public TO {definer_role}"))
    with _as_role(bind, DEFINER_ROLE):
        bind.execute(sa.text("""
            CREATE OR REPLACE FUNCTION public.mia_purge_audit_logs_for_privacy(
                p_shop_id bigint,
                p_customer_id text,
                p_customer_email text,
                p_all_shop boolean
            )
            RETURNS integer
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $function$
            DECLARE
                v_shop_domain text;
                v_deleted integer;
            BEGIN
                IF p_shop_id IS NULL
                   OR current_setting('app.shop_id', true) IS DISTINCT FROM p_shop_id::text THEN
                    RAISE EXCEPTION 'privacy audit purge requires matching shop context'
                        USING ERRCODE = '42501';
                END IF;
                SELECT lower(s.shop_domain) INTO v_shop_domain
                  FROM public.shops AS s WHERE s.id = p_shop_id;
                IF v_shop_domain IS NULL
                   OR lower(current_setting('app.shop_domain', true)) IS DISTINCT FROM v_shop_domain THEN
                    RAISE EXCEPTION 'privacy audit purge requires matching shop domain'
                        USING ERRCODE = '42501';
                END IF;
                IF p_all_shop THEN
                    DELETE FROM public.audit_logs WHERE shop_id = p_shop_id;
                ELSE
                    DELETE FROM public.audit_logs AS a
                     WHERE a.shop_id = p_shop_id
                       AND (
                           (NULLIF(p_customer_id, '') IS NOT NULL
                            AND (strpos(lower(coalesce(a.details_json, '')), lower(p_customer_id)) > 0
                                 OR lower(a.entity_id) = lower(p_customer_id)))
                           OR
                           (NULLIF(p_customer_email, '') IS NOT NULL
                            AND (strpos(lower(coalesce(a.details_json, '')), lower(p_customer_email)) > 0
                                 OR lower(a.entity_id) = lower(p_customer_email)))
                       );
                END IF;
                GET DIAGNOSTICS v_deleted = ROW_COUNT;
                RETURN v_deleted;
            END;
            $function$;
        """))

    bind.execute(sa.text(f"REVOKE CREATE ON SCHEMA public FROM {definer_role}"))
    _reconcile_function_execute_acl(
        bind,
        "mia_purge_audit_logs_for_privacy(bigint, text, text, boolean)",
        [runtime_role_name],
        owner_role=DEFINER_ROLE,
    )
    op.execute("DROP FUNCTION IF EXISTS public.mia_current_shop_id()")
    with _as_role(bind, DEFINER_ROLE):
        bind.execute(sa.text("DROP FUNCTION IF EXISTS public.mia_current_shop_domain()"))
        bind.execute(sa.text("DROP FUNCTION IF EXISTS public.mia_is_internal_scheduler()"))
    # Remove only the key table created by this migration. mia_private may have
    # existed before this revision or contain unrelated objects; never CASCADE
    # drop the entire schema during rollback.
    op.execute("DROP TABLE IF EXISTS mia_private.tenant_context_keys")
    op.execute("REVOKE ALL ON SCHEMA mia_private FROM mia_rls_definer")
    op.execute("DROP INDEX IF EXISTS public.uq_shops_shop_domain_lower")
