import base64
import hashlib
import hmac
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.models.schemas import AuditLog, Shop, ShopSession, WebhookEvent
from app.shopify.config import settings

router = APIRouter()

SUPPORTED_TOPICS = [
    "app/uninstalled",
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
        await db.execute(
            update(ShopSession)
            .where(ShopSession.shop_id == shop.id)
            .values(is_valid=False)
        )
        shop.access_token_encrypted = None
        action = "shop.uninstalled"
        entity_type = "shop"
        entity_id = str(shop.id)
    else:
        object_key = "product" if topic.startswith("products/") else "order"
        raw_id = payload.get("id") or payload.get(f"{object_key}_id")
        action = f"shopify.{topic.replace('/', '.') }"
        entity_type = object_key
        entity_id = str(raw_id) if raw_id is not None else None

    db.add(AuditLog(
        shop_id=shop.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details_json=json.dumps(payload, separators=(",", ":")),
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

    shop_obj = await _get_shop_by_domain(db, shop_domain)
    shop_id = shop_obj.id if shop_obj else None

    if event_id:
        existing = await db.execute(
            select(WebhookEvent).where(
                WebhookEvent.shop_id == shop_id,
                WebhookEvent.event_id == event_id,
            ).limit(1)
        )
        if existing.scalar_one_or_none():
            response.headers["ETag"] = f'W/"{event_id}"'
            return {"status": "received", "duplicate": True}

    payload_text = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(payload_text) if payload_text else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook JSON") from exc

    event = WebhookEvent(
        shop_id=shop_id,
        topic=topic,
        event_id=event_id,
        payload_json=payload_text,
        processed=False,
    )
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
