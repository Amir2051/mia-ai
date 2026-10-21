"""Add tenant-scoped scheduled in-app reports."""

from alembic import op
import sqlalchemy as sa

revision = "0007_scheduled_reports"
down_revision = "0006_mia_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("report_type", sa.String(50), nullable=False, server_default="sales_summary"),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("schedule_time", sa.String(5), nullable=False, server_default="09:00"),
        sa.Column("timezone", sa.String(100), nullable=False, server_default="UTC"),
        sa.Column("day_of_week", sa.Integer(), nullable=True),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("next_run_at", sa.DateTime(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(30), nullable=False, server_default="never"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_result_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_scheduled_reports_shop_id", "scheduled_reports", ["shop_id"])
    op.create_index("ix_scheduled_reports_next_run_at", "scheduled_reports", ["next_run_at"])
    op.create_index("ix_scheduled_reports_due", "scheduled_reports", ["enabled", "next_run_at"])
    op.execute("ALTER TABLE scheduled_reports ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS mia_scheduled_reports_isolation ON scheduled_reports")
    op.execute("""
        CREATE POLICY mia_scheduled_reports_isolation ON scheduled_reports
        USING (shop_id = NULLIF(current_setting('app.shop_id', true), '')::bigint)
        WITH CHECK (shop_id = NULLIF(current_setting('app.shop_id', true), '')::bigint)
    """)
    # The internal scheduler is the only code path that sets this transaction-local
    # flag. It is needed to claim due rows across shops without weakening normal
    # per-shop API access.
    op.execute("DROP POLICY IF EXISTS mia_scheduled_reports_scheduler ON scheduled_reports")
    op.execute("""
        CREATE POLICY mia_scheduled_reports_scheduler ON scheduled_reports
        USING (current_setting('app.internal_scheduler', true) = 'true')
        WITH CHECK (current_setting('app.internal_scheduler', true) = 'true')
    """)
    op.execute("DROP POLICY IF EXISTS mia_shops_scheduler_read ON shops")
    op.execute("""
        CREATE POLICY mia_shops_scheduler_read ON shops
        FOR SELECT
        USING (current_setting('app.internal_scheduler', true) = 'true')
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS mia_shops_scheduler_read ON shops")
    op.execute("DROP POLICY IF EXISTS mia_scheduled_reports_scheduler ON scheduled_reports")
    op.execute("DROP POLICY IF EXISTS mia_scheduled_reports_isolation ON scheduled_reports")
    op.execute("ALTER TABLE scheduled_reports DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_scheduled_reports_due", table_name="scheduled_reports")
    op.drop_index("ix_scheduled_reports_next_run_at", table_name="scheduled_reports")
    op.drop_index("ix_scheduled_reports_shop_id", table_name="scheduled_reports")
    op.drop_table("scheduled_reports")
