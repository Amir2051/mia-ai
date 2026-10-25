"""Add approval-gated merchant publishing actions."""
from alembic import op
import sqlalchemy as sa

revision = "0008_publishing_actions"
down_revision = "0007_scheduled_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "publishing_actions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_type", sa.String(60), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending_approval"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("shop_id", "idempotency_key", name="uq_publishing_action_shop_idempotency"),
    )
    op.create_index("ix_publishing_actions_shop_status", "publishing_actions", ["shop_id", "status"])
    op.execute("ALTER TABLE publishing_actions ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY mia_publishing_actions_isolation ON publishing_actions
        USING (shop_id = NULLIF(current_setting('app.shop_id', true), '')::bigint)
        WITH CHECK (shop_id = NULLIF(current_setting('app.shop_id', true), '')::bigint)
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS mia_publishing_actions_isolation ON publishing_actions")
    op.execute("ALTER TABLE publishing_actions DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_publishing_actions_shop_status", table_name="publishing_actions")
    op.drop_table("publishing_actions")
