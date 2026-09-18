"""Allow product imports to store multiple Shopify product IDs.

Revision ID: 0003_import_product_id_text
Revises: 0002_shopify_token_lifecycle
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_import_product_id_text"
down_revision = "0002_shopify_token_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "product_imports",
        "shopify_product_id",
        existing_type=sa.String(length=255),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "product_imports",
        "shopify_product_id",
        existing_type=sa.Text(),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
