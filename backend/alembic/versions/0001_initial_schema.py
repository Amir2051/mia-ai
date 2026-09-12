"""Create initial Mia AI application schema.

Revision ID: 0001_initial_schema
Revises:
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shops",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("shop_domain", sa.String(length=255), nullable=False),
        sa.Column("shop_name", sa.String(length=255), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("installed_at", sa.DateTime(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("shop_domain"),
    )
    op.create_index("ix_shops_shop_domain", "shops", ["shop_domain"], unique=False)

    op.create_table(
        "shop_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_token", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=255), nullable=True),
        sa.Column("nonce", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("is_valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("session_token"),
    )
    op.create_index("ix_shop_sessions_session_token", "shop_sessions", ["session_token"], unique=False)

    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("contact_email", sa.String(length=255), nullable=True),
        sa.Column("api_endpoint", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata_json", sa.Text(), nullable=True),
    )

    op.create_table(
        "product_imports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id", ondelete="CASCADE"), nullable=False),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("supplier_sku", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("images", sa.Text(), nullable=True),
        sa.Column("cost", sa.Numeric(10, 2), nullable=True),
        sa.Column("retail_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("variants_json", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("inventory", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("sync_status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("shopify_product_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "sync_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="queued"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id", ondelete="SET NULL"), nullable=True),
        sa.Column("topic", sa.String(length=255), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("received_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_webhook_events_event_id", "webhook_events", ["event_id"], unique=False)

    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id", ondelete="CASCADE"), nullable=True),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=True),
        sa.Column("entity_id", sa.String(length=255), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("app_settings")
    op.drop_index("ix_webhook_events_event_id", table_name="webhook_events")
    op.drop_table("webhook_events")
    op.drop_table("sync_jobs")
    op.drop_table("product_imports")
    op.drop_table("suppliers")
    op.drop_index("ix_shop_sessions_session_token", table_name="shop_sessions")
    op.drop_table("shop_sessions")
    op.drop_index("ix_shops_shop_domain", table_name="shops")
    op.drop_table("shops")
