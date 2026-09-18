"""Add PostgreSQL row-level isolation for merchant-owned data."""

from alembic import op


revision = "0004_postgres_rls"
down_revision = "0003_import_product_id_text"
branch_labels = None
depends_on = None

SHOP_TABLES = (
    "shop_sessions",
    "product_imports",
    "sync_jobs",
    "webhook_events",
    "app_settings",
    "audit_logs",
)


def upgrade() -> None:
    # The runtime role is intentionally not the table owner. RLS therefore
    # applies without FORCE ROW LEVEL SECURITY, while migrations keep ownership.
    op.execute("ALTER TABLE shops ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS mia_shop_isolation ON shops")
    op.execute(
        """
        CREATE POLICY mia_shop_isolation ON shops
        USING (shop_domain = current_setting('app.shop_domain', true))
        WITH CHECK (shop_domain = current_setting('app.shop_domain', true))
        """
    )

    for table in SHOP_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS mia_{table}_isolation ON {table}")
        op.execute(
            f"""
            CREATE POLICY mia_{table}_isolation ON {table}
            USING (shop_id = NULLIF(current_setting('app.shop_id', true), '')::bigint)
            WITH CHECK (shop_id = NULLIF(current_setting('app.shop_id', true), '')::bigint)
            """
        )

    # Suppliers are global application reference data, not merchant-owned records.
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute("REVOKE ALL ON TABLE alembic_version FROM PUBLIC")


def downgrade() -> None:
    for table in reversed(SHOP_TABLES):
        op.execute(f"DROP POLICY IF EXISTS mia_{table}_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS mia_shop_isolation ON shops")
    op.execute("ALTER TABLE shops DISABLE ROW LEVEL SECURITY")
    op.execute("GRANT CREATE ON SCHEMA public TO PUBLIC")
