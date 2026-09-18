import base64
import hashlib
import hmac
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.models.schemas import AppSetting, AuditLog, ProductImport, Shop, ShopSession, SyncJob, WebhookEvent
from app.security.rls import set_shop_context
from app.shopify.config import settings

router = APIRouter()

SUPPORTED_TOPICS = [
    "app/uninstalled",
    "customers/data_request",
    "customers/redact",
    "shop/redact",
    "products/create",
    "products/update",
    "products/delete",
    "orders/create",
    "orders/updated",
]


async def _get_shop_by_domain(db: AsyncSession, shop_domain: str) -> Optional[Shop]:
    result = await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))
    return result.scalar_one_or_none()


def _verify_webhook_signature(hmac_header: str, body: bytes) -> bool:
    if not settings.shopify_api_secret:
        return False
    digest = hmac.new(settings.shopify_api_secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, hmac_header)


def _extract_event_id(payload_json: str) -> Optional[str]:
    try:
        data = json.loads(payload_json)
        if isinstance(data, dict):
            return str(data.get("id") or data.get("order_id") or data.get("product_id") or "") or None
    except Exception:
        pass
    return None


async def _process_event(db: AsyncSession, shop: Optional[Shop], topic: str, payload: dict) -> None:
    if not shop:
        return

    if topic == "app/uninstalled":
        shop.is_active = False
        await db.execute(update(ShopSession).where(ShopSession.shop_id == shop.id).values(is_valid=False))
        shop.access_token_encrypted = None
        shop.access_token_expires_at = None
        shop.refresh_token_encrypted = None
        shop.refresh_token_expires_at = None
        action = "shop.uninstalled"
        entity_type = "shop"
        entity_id = str(shop.id)
    elif topic == "customers/data_request":
        # Mia does not persist a separate customer profile store. Customer/order
        # data is fetched from Shopify on demand. Acknowledge the request and
        # record only the compliance event metadata, not the submitted PII.
        action = "privacy.customer_data_request"
        entity_type = "customer"
        entity_id = None
    elif topic == "customers/redact":
        customer = payload.get("customer") or {}
        customer_id = str(customer.get("id") or "")
        customer_email = str(customer.get("email") or "")
        patterns = [v for v in (customer_id, customer_email) if v]
        for pattern in patterns:
            await db.execute(
                delete(WebhookEvent).where(
                    WebhookEvent.shop_id == shop.id, WebhookEvent.payload_json.contains(pattern)
                )
            )
            await db.execute(
                delete(AuditLog).where(
                    AuditLog.shop_id == shop.id, AuditLog.details_json.contains(pattern)
                )
            )
        action = "privacy.customer_redact"
        entity_type = "customer"
        entity_id = customer_id or None
    elif topic == "shop/redact":
        # Shopify sends this after uninstall. Erase every Mia-owned record.
        shop_id = shop.id
        await db.execute(delete(ShopSession).where(ShopSession.shop_id == shop_id))
        await db.execute(delete(WebhookEvent).where(WebhookEvent.shop_id == shop_id))
        await db.execute(delete(AuditLog).where(AuditLog.shop_id == shop_id))
        await db.execute(delete(SyncJob).where(SyncJob.shop_id == shop_id))
        await db.execute(delete(ProductImport).where(ProductImport.shop_id == shop_id))
        await db.execute(delete(AppSetting).where(AppSetting.shop_id == shop_id))
        await db.delete(shop)
        action = "privacy.shop_redact"
        entity_type = "shop"
        entity_id = str(shop_id)
    else:
        object_key = "product" if topic.startswith("products/") else "order"
        raw_id = payload.get("id") or payload.get(f"{object_key}_id")
        action = f"shopify.{topic.replace('/', '.') }"
        entity_type = object_key
        entity_id = str(raw_id) if raw_id is not None else None

    if topic != "shop/redact":
        # Audit only metadata; never persist Shopify/customer payloads.
        db.add(AuditLog(
            shop_id=shop.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details_json="{}",
        ))


@router.post("/webhooks")
async def receive_webhook(request: Request, response: Response, db=Depends(get_db)):
    body = await request.body()
    topic = request.headers.get("X-Shopify-Topic", "").strip().lower()
    shop_domain = request.headers.get("X-Shopify-Shop-Domain", "").strip().lower()
    hmac_header = request.headers.get("X-Shopify-Hmac-SHA256", "")
    event_id = request.headers.get("X-Shopify-Event-Id", "") or _extract_event_id(body.decode("utf-8", errors="replace"))

    if not topic or not shop_domain:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing webhook headers")
    if topic not in SUPPORTED_TOPICS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported webhook topic")
    if not settings.shopify_api_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Webhook verification is not configured")
    if not hmac_header or not _verify_webhook_signature(hmac_header, body):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")

    # Set the domain context before the first tenant-scoped query. PostgreSQL RLS
    # evaluates this context while selecting the shop itself.
    await set_shop_context(db, shop_domain)
    shop_obj = await _get_shop_by_domain(db, shop_domain)
    if shop_obj is None:
        # A valid Shopify compliance webhook can arrive after uninstall/redaction.
        # Acknowledge it without touching tenant data so Shopify does not retry forever.
        return {"status": "received", "shop_known": False}
    await set_shop_context(db, shop_domain, shop_obj.id)
    shop_id = shop_obj.id

    if event_id:
        existing = await db.execute(select(WebhookEvent).where(WebhookEvent.shop_id == shop_id, WebhookEvent.event_id == event_id).limit(1))
        if existing.scalar_one_or_none():
            response.headers["ETag"] = f'W/"{event_id}"'
            return {"status": "received", "duplicate": True}

    payload_text = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(payload_text) if payload_text else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook JSON") from exc

    # Never persist raw Shopify webhook bodies; product/order payloads can contain PII.
    event = WebhookEvent(shop_id=shop_id, topic=topic, event_id=event_id, payload_json="{}", processed=False)
    db.add(event)
    await db.flush()

    try:
        await _process_event(db, shop_obj, topic, payload)
        event.processed = True
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook processing failed") from exc

    response.headers["ETag"] = f'W/"{event_id or event.id}"'
    return {"status": "received", "processed": True}
