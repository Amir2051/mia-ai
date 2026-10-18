"""Persist per-shop Mia conversations and merchant knowledge notes."""

from alembic import op
import sqlalchemy as sa

revision = "0006_mia_memory"
down_revision = "0005_social_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mia_memory",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("knowledge_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_mia_memory_shop_id", "mia_memory", ["shop_id"], unique=True)
    op.execute("ALTER TABLE mia_memory ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS mia_mia_memory_isolation ON mia_memory")
    op.execute("""
        CREATE POLICY mia_mia_memory_isolation ON mia_memory
        USING (shop_id = NULLIF(current_setting('app.shop_id', true), '')::bigint)
        WITH CHECK (shop_id = NULLIF(current_setting('app.shop_id', true), '')::bigint)
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS mia_mia_memory_isolation ON mia_memory")
    op.execute("ALTER TABLE mia_memory DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_mia_memory_shop_id", table_name="mia_memory")
    op.drop_table("mia_memory")
