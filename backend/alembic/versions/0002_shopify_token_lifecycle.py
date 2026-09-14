"""Track Shopify expiring offline token lifecycle.

Revision ID: 0002_shopify_token_lifecycle
Revises: 0001_initial_schema
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_shopify_token_lifecycle"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shops", sa.Column("access_token_expires_at", sa.DateTime(), nullable=True))
    op.add_column("shops", sa.Column("refresh_token_encrypted", sa.Text(), nullable=True))
    op.add_column("shops", sa.Column("refresh_token_expires_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("shops", "refresh_token_expires_at")
    op.drop_column("shops", "refresh_token_encrypted")
    op.drop_column("shops", "access_token_expires_at")
