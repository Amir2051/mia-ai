"""Use HMAC-verified PostgreSQL context for tenant and scheduler RLS.

Revision ID: 0010_signed_tenant_context
Revises: 0009_privacy_audit_purge
"""
import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

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


def _runtime_role(bind) -> str:
    runtime_url = os.environ.get("DATABASE_URL", "")
    migration_url = os.environ.get("MIGRATION_DATABASE_URL", "")
    if not runtime_url or not migration_url:
        raise RuntimeError("DATABASE_URL and MIGRATION_DATABASE_URL are required for signed RLS context")
    runtime_user = make_url(runtime_url).username
    migration_user = make_url(migration_url).username
    if not runtime_user or not migration_user:
        raise RuntimeError("Both database URLs must include usernames")
    if runtime_user == migration_user:
        raise RuntimeError("DATABASE_URL must use a dedicated runtime role distinct from MIGRATION_DATABASE_URL")
    return bind.dialect.identifier_preparer.quote(runtime_user)


def upgrade() -> None:
    secret = os.environ.get("RLS_CONTEXT_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("RLS_CONTEXT_SECRET must contain at least 32 bytes before applying signed tenant-context migration")

    bind = op.get_bind()
    runtime_role = _runtime_role(bind)

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
    op.execute("""
        CREATE TABLE IF NOT EXISTS mia_private.tenant_context_keys (
            singleton boolean PRIMARY KEY DEFAULT TRUE CHECK (singleton IS TRUE),
            secret text NOT NULL
        )
    """)
    bind.execute(
        sa.text("""
            INSERT INTO mia_private.tenant_context_keys (singleton, secret)
            VALUES (TRUE, :secret)
            ON CONFLICT (singleton) DO UPDATE SET secret = EXCLUDED.secret
        """),
        {"secret": secret},
    )
    op.execute("REVOKE ALL ON TABLE mia_private.tenant_context_keys FROM PUBLIC")
    op.execute(f"REVOKE ALL ON SCHEMA mia_private FROM {runtime_role}")
    op.execute(f"REVOKE ALL ON TABLE mia_private.tenant_context_keys FROM {runtime_role}")

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
                    pg_catalog.convert_to(v_domain, 'UTF8'),
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
        SECURITY DEFINER
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

            -- The function owner is the shops table owner and intentionally
            -- bypasses its non-FORCE RLS policy for this indexed lookup.
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
                    pg_catalog.convert_to('mia:internal-scheduler:v1', 'UTF8'),
                    pg_catalog.convert_to(v_secret, 'UTF8'),
                    'sha256'
                ),
                'hex'
            );
            RETURN v_signature = v_expected;
        END;
        $function$;
    """)

    for function_name in (
        "mia_current_shop_domain()",
        "mia_current_shop_id()",
        "mia_is_internal_scheduler()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{function_name} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{function_name} TO {runtime_role}")

    # Replace caller-settable GUC policies with signed-context predicates.
    op.execute("ALTER TABLE public.shops ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS mia_shop_isolation ON public.shops")
    op.execute("""
        CREATE POLICY mia_shop_isolation ON public.shops
        USING (pg_catalog.lower(shop_domain) = public.mia_current_shop_domain())
        WITH CHECK (pg_catalog.lower(shop_domain) = public.mia_current_shop_domain())
    """)
    op.execute("DROP POLICY IF EXISTS mia_shops_scheduler_read ON public.shops")
    op.execute("""
        CREATE POLICY mia_shops_scheduler_read ON public.shops
        FOR SELECT
        USING (public.mia_is_internal_scheduler())
    """)

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS mia_{table}_isolation ON public.{table}")
        op.execute(f"""
            CREATE POLICY mia_{table}_isolation ON public.{table}
            USING (shop_id = public.mia_current_shop_id())
            WITH CHECK (shop_id = public.mia_current_shop_id())
        """)

    op.execute("ALTER TABLE public.scheduled_reports ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS mia_scheduled_reports_isolation ON public.scheduled_reports")
    op.execute("DROP POLICY IF EXISTS mia_scheduled_reports_scheduler ON public.scheduled_reports")
    op.execute("DROP POLICY IF EXISTS mia_scheduled_reports_isolation ON public.scheduled_reports")
    op.execute("DROP POLICY IF EXISTS mia_scheduled_reports_scheduler_read ON public.scheduled_reports")
    op.execute("DROP POLICY IF EXISTS mia_scheduled_reports_scheduler_update ON public.scheduled_reports")
    op.execute("""
        CREATE POLICY mia_scheduled_reports_isolation ON public.scheduled_reports
        USING (shop_id = public.mia_current_shop_id())
        WITH CHECK (shop_id = public.mia_current_shop_id())
    """)
    op.execute("""
        CREATE POLICY mia_scheduled_reports_scheduler_read ON public.scheduled_reports
        FOR SELECT
        USING (public.mia_is_internal_scheduler())
    """)
    op.execute("""
        CREATE POLICY mia_scheduled_reports_scheduler_update ON public.scheduled_reports
        FOR UPDATE
        USING (public.mia_is_internal_scheduler())
        WITH CHECK (public.mia_is_internal_scheduler())
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
            IF public.mia_is_internal_scheduler() AND (
                NEW.id IS DISTINCT FROM OLD.id
                OR NEW.shop_id IS DISTINCT FROM OLD.shop_id
                OR NEW.name IS DISTINCT FROM OLD.name
                OR NEW.report_type IS DISTINCT FROM OLD.report_type
                OR NEW.frequency IS DISTINCT FROM OLD.frequency
                OR NEW.schedule_time IS DISTINCT FROM OLD.schedule_time
                OR NEW.timezone IS DISTINCT FROM OLD.timezone
                OR NEW.day_of_week IS DISTINCT FROM OLD.day_of_week
                OR NEW.day_of_month IS DISTINCT FROM OLD.day_of_month
                OR NEW.enabled IS DISTINCT FROM OLD.enabled
                OR NEW.last_result_json IS DISTINCT FROM OLD.last_result_json
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
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

    # Re-bind the GDPR SECURITY DEFINER function to the verified tenant
    # context. It must not trust app.shop_id or app.shop_domain on their own.
    op.execute("""
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
    """)
    op.execute("REVOKE ALL ON FUNCTION public.mia_purge_audit_logs_for_privacy(bigint, text, text, boolean) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.mia_purge_audit_logs_for_privacy(bigint, text, text, boolean) TO {runtime_role}")


def downgrade() -> None:
    # Explicit downgrade restores the previous GUC-only policies. Use only as
    # part of a reviewed rollback because it reintroduces the documented gap.
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

    op.execute("""
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
    """)
    op.execute("DROP FUNCTION IF EXISTS public.mia_current_shop_id()")
    op.execute("DROP FUNCTION IF EXISTS public.mia_current_shop_domain()")
    op.execute("DROP FUNCTION IF EXISTS public.mia_is_internal_scheduler()")
    # Remove only the key table created by this migration. mia_private may have
    # existed before this revision or contain unrelated objects; never CASCADE
    # drop the entire schema during rollback.
    op.execute("DROP TABLE IF EXISTS mia_private.tenant_context_keys")
