"""
Application data models.

These models store app-specific data only. Shopify data is fetched via API adapters.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.database import Base


def utcnow() -> datetime:
    """
    Return the current UTC time as a naive datetime.

    The database currently uses SQLAlchemy DateTime columns without
    timezone=True, so we preserve the existing database representation
    while avoiding the deprecated datetime.utcnow().
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Shop(Base):
    __tablename__ = 'shops'

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    shop_domain: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
    )
    shop_name: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    access_token_encrypted: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    scope: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    installed_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )

    sessions: Mapped[list['ShopSession']] = relationship(
        back_populates='shop',
        cascade='all, delete-orphan',
    )
    imports: Mapped[list['ProductImport']] = relationship(
        back_populates='shop',
        cascade='all, delete-orphan',
    )
    settings: Mapped[list['AppSetting']] = relationship(
        back_populates='shop',
        cascade='all, delete-orphan',
    )


class ShopSession(Base):
    __tablename__ = 'shop_sessions'

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    shop_id: Mapped[int] = mapped_column(
        ForeignKey(
            'shops.id',
            ondelete='CASCADE',
        )
    )
    session_token: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
    )
    state: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    nonce: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
    )
    is_valid: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )

    shop: Mapped['Shop'] = relationship(
        back_populates='sessions',
    )


class Supplier(Base):
    __tablename__ = 'suppliers'

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
    )
    contact_email: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    api_endpoint: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )
    metadata_json: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )


class ProductImport(Base):
    __tablename__ = 'product_imports'

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    shop_id: Mapped[int] = mapped_column(
        ForeignKey(
            'shops.id',
            ondelete='CASCADE',
        )
    )
    supplier_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey('suppliers.id'),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(
        String(100),
    )
    supplier_sku: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    title: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    images: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    cost: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )
    retail_price: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )
    variants_json: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    category: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    tags: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    inventory: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(50),
        default='pending',
    )
    sync_status: Mapped[str] = mapped_column(
        String(50),
        default='pending',
    )
    error: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    shopify_product_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        onupdate=utcnow,
    )

    shop: Mapped['Shop'] = relationship(
        back_populates='imports',
    )


class SyncJob(Base):
    __tablename__ = 'sync_jobs'

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    shop_id: Mapped[int] = mapped_column(
        ForeignKey(
            'shops.id',
            ondelete='CASCADE',
        )
    )
    entity: Mapped[str] = mapped_column(
        String(100),
    )
    status: Mapped[str] = mapped_column(
        String(50),
        default='queued',
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
    )
    details_json: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    error: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )


class WebhookEvent(Base):
    __tablename__ = 'webhook_events'

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    shop_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(
            'shops.id',
            ondelete='SET NULL',
        ),
        nullable=True,
    )
    topic: Mapped[str] = mapped_column(
        String(255),
    )
    event_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    payload_json: Mapped[str] = mapped_column(
        Text,
    )
    processed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
    )


class AppSetting(Base):
    __tablename__ = 'app_settings'

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    shop_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(
            'shops.id',
            ondelete='CASCADE',
        ),
        nullable=True,
    )
    key: Mapped[str] = mapped_column(
        String(255),
    )
    value_json: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        onupdate=utcnow,
    )

    shop: Mapped[Optional['Shop']] = relationship(
        back_populates='settings',
    )


class AuditLog(Base):
    __tablename__ = 'audit_logs'

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    shop_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(
            'shops.id',
            ondelete='SET NULL',
        ),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(
        String(255),
    )
    entity_type: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    entity_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    details_json: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
    )
