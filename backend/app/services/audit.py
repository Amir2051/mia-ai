"""Merchant-scoped audit events for SEO, marketing and publishing outcomes."""
import json
import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import AuditLog
from app.security.rls import set_shop_context

logger = logging.getLogger(__name__)


async def record_audit_event(
    db: AsyncSession,
    *,
    shop_id: int,
    shop_domain: str,
    action: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> bool:
    """Persist a small outcome record without making successful external work fail.

    Only pass metadata here. Provider tokens, full customer data, and full social
    post bodies should never be stored in the audit trail.
    """
    try:
        await set_shop_context(db, shop_domain, shop_id)
        db.add(
            AuditLog(
                shop_id=shop_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details_json=json.dumps(details or {}, ensure_ascii=False),
            )
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        logger.exception("Could not persist merchant audit event: %s", action)
        return False
