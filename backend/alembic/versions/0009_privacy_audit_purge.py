"""Provide scoped GDPR audit-log erasure without general runtime DELETE.

Revision ID: 0009_privacy_audit_purge
Revises: 0008_publishing_actions
"""
import os

from alembic import op
from sqlalchemy.engine import make_url

revision = "0009_privacy_audit_purge"
down_revision = "0008_publishing_actions"
branch_labels = None
depends_on = None

FUNCTION_SIGNATURE = "public.mia_purge_audit_logs_for_privacy(bigint, text, text, boolean)"


def upgrade() -> None:
    op.execute(
        """
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

            SELECT lower(s.shop_domain)
              INTO v_shop_domain
              FROM public.shops AS s
             WHERE s.id = p_shop_id;

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
                        AND strpos(coalesce(a.details_json, ''), p_customer_id) > 0)
                       OR
                       (NULLIF(p_customer_email, '') IS NOT NULL
                        AND strpos(coalesce(a.details_json, ''), p_customer_email) > 0)
                   );
            END IF;

            GET DIAGNOSTICS v_deleted = ROW_COUNT;
            RETURN v_deleted;
        END;
        $function$;
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION_SIGNATURE} FROM PUBLIC")

    # The app uses DATABASE_URL at runtime and a separate migration URL for DDL.
    # Grant the narrowly scoped function only to the configured runtime DB role.
    runtime_url = os.environ.get("DATABASE_URL")
    if not runtime_url:
        raise RuntimeError("DATABASE_URL is required to grant the GDPR purge function to the runtime role")
    runtime_username = make_url(runtime_url).username
    if not runtime_username:
        raise RuntimeError("DATABASE_URL must include the runtime database username")
    quoted_role = op.get_bind().dialect.identifier_preparer.quote(runtime_username)
    op.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION_SIGNATURE} TO {quoted_role}")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION_SIGNATURE}")
