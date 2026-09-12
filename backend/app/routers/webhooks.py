import base64
import hashlib
import hmac
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.models.schemas import Shop, WebhookEvent
from app.shopify.auth import ShopifyOAuthFlow
from app.shopify.config import settings

router = APIRouter()


SUPPORTED_TOPICS = [
    'app/uninstalled',
    'products/create',
    'products/update',
    'products/delete',
    'orders/create',
    'orders/updated',
]


async def _get_shop_by_domain(db: AsyncSession, shop_domain: str) -> Optional[Shop]:
    result = await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))
    return result.scalar_one_or_none()


def _verify_webhook_signature(shop: str, hmac_header: str, body: bytes) -> bool:
    if not settings.shopify_api_secret:
        return False

    digest = hmac.new(
        settings.shopify_api_secret.encode('utf-8'),
        body,
        hashlib.sha256,
    ).digest()

    expected = base64.b64encode(digest).decode('utf-8')

    return hmac.compare_digest(expected, hmac_header)


def _extract_event_id(payload_json: str) -> Optional[str]:
    try:
        import json
        data = json.loads(payload_json)
        if isinstance(data, dict):
            return str(data.get('id') or data.get('order_id') or data.get('product_id') or '')
    except Exception:
        pass
    return None


@router.post('/webhooks')
async def receive_webhook(request: Request, response: Response, db=Depends(get_db)):
    body = await request.body()
    topic = request.headers.get('X-Shopify-Topic', '')
    shop = request.headers.get('X-Shopify-Shop-Domain', '')
    hmac_header = request.headers.get('X-Shopify-Hmac-SHA256', '')
    event_id = request.headers.get('X-Shopify-Event-Id', '') or _extract_event_id(body.decode('utf-8', errors='replace'))

    if not topic or not shop:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Missing webhook headers')

    if settings.shopify_api_secret:
        if not hmac_header:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid webhook signature')
        valid = _verify_webhook_signature(shop, hmac_header, body)
        if not valid:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid webhook signature')

    shop_obj = await _get_shop_by_domain(db, shop)
    shop_id = shop_obj.id if shop_obj else None

    if event_id:
        existing = await db.execute(
            select(WebhookEvent).where(WebhookEvent.shop_id == shop_id, WebhookEvent.event_id == event_id).limit(1)
        )
        if existing.scalar_one_or_none():
            response.headers['ETag'] = 'W/"' + event_id + '"'
            return {'status': 'received'}

    payload = body.decode('utf-8', errors='replace')

    if not settings.shopify_api_secret:
        return {'status': 'received', 'processed': False}

    event = WebhookEvent(
        shop_id=shop_id,
        topic=topic,
        event_id=event_id,
        payload_json=payload,
        processed=False,
    )
    db.add(event)
    await db.commit()
    response.headers['ETag'] = 'W/"' + (event_id or '') + '"'
    return {'status': 'received'}
