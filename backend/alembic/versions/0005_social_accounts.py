"""add social accounts

Revision ID: 0005_social_accounts
Revises: 0004_postgres_rls
"""
from alembic import op
import sqlalchemy as sa


revision = "0005_social_accounts"
down_revision = "0004_postgres_rls"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "social_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("account_type", sa.String(100), nullable=False),
        sa.Column("external_account_id", sa.String(255), nullable=False),
        sa.Column("account_name", sa.String(255), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_social_accounts_shop_id", "social_accounts", ["shop_id"])
    op.create_index("ix_social_accounts_provider_external", "social_accounts", ["provider", "external_account_id"], unique=True)

    op.execute("ALTER TABLE social_accounts ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS mia_social_accounts_isolation ON social_accounts")
    op.execute("""
        CREATE POLICY mia_social_accounts_isolation ON social_accounts
        USING (shop_id = NULLIF(current_setting('app.shop_id', true), '')::bigint)
        WITH CHECK (shop_id = NULLIF(current_setting('app.shop_id', true), '')::bigint)
    """)


def downgrade():
    op.execute("DROP POLICY IF EXISTS mia_social_accounts_isolation ON social_accounts")
    op.execute("ALTER TABLE social_accounts DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_social_accounts_provider_external", table_name="social_accounts")
    op.drop_index("ix_social_accounts_shop_id", table_name="social_accounts")
    op.drop_table("social_accounts")
