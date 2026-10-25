"""Approval-gated, idempotent publishing actions for Shopify and Meta."""
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.auth.dependencies import CurrentUser, get_optional_shop
from app.models.database import get_db
from app.models.schemas import PublishingAction, Shop, SocialAccount
from app.routers.products import _publication_client
from app.security.rls import set_shop_context
from app.security.tokens import decrypt_token
from app.services.audit import record_audit_event
from app.services.meta import MetaAPIError, MetaClient, page_capabilities
from app.shopify.client import ShopifyAPIError

router = APIRouter()
logger = logging.getLogger(__name__)
ACTION_TYPES = {"shopify_product_channels", "meta_facebook", "meta_instagram"}
TERMINAL_STATUSES = {"published", "failed", "needs_review"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _safe_payload(action_type: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Whitelist the only payload fields allowed to reach an external publisher."""
    if action_type == "shopify_product_channels":
        product_id = str(raw.get("product_id") or "")
        publication_ids = raw.get("publication_ids")
        publish = raw.get("publish", True)
        if not product_id.startswith("gid://shopify/Product/"):
            raise HTTPException(422, "product_id must be a Shopify Product GID")
        if not isinstance(publication_ids, list) or not publication_ids or len(publication_ids) > 50:
            raise HTTPException(422, "Select between 1 and 50 sales channels")
        if any(not isinstance(pid, str) or not pid.startswith("gid://shopify/Publication/") for pid in publication_ids):
            raise HTTPException(422, "publication_ids must contain Shopify Publication GIDs")
        if not isinstance(publish, bool):
            raise HTTPException(422, "publish must be true or false")
        return {"product_id": product_id, "publication_ids": sorted(set(publication_ids)), "publish": publish}
    account_id = raw.get("account_id")
    if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id < 1:
        raise HTTPException(422, "account_id must be a positive integer")
    if action_type == "meta_facebook":
        message = raw.get("message")
        link = raw.get("link")
        if not isinstance(message, str) or not message.strip() or len(message) > 63206:
            raise HTTPException(422, "message must contain 1 to 63,206 characters")
        payload = {"account_id": account_id, "message": message.strip(), "link": None}
        if link is not None:
            if not isinstance(link, str) or len(link) > 2048:
                raise HTTPException(422, "link must be a URL of at most 2,048 characters")
            parsed = urlparse(link)
            if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
                raise HTTPException(422, "link must be a valid HTTP(S) URL without embedded credentials")
            payload["link"] = link
        return payload
    image_url = raw.get("image_url")
    caption = raw.get("caption", "")
    if not isinstance(image_url, str) or len(image_url) > 2048:
        raise HTTPException(422, "image_url must be a valid public HTTPS image URL")
    parsed = urlparse(image_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise HTTPException(422, "Instagram images must use a public HTTPS image URL")
    if not isinstance(caption, str) or len(caption) > 2200:
        raise HTTPException(422, "Instagram captions must be 2,200 characters or fewer")
    return {"account_id": account_id, "image_url": image_url, "caption": caption}


async def _current_shop(current: CurrentUser | None, db):
    if not current:
        raise HTTPException(401, "Missing Shopify session")
    await set_shop_context(db, current.shop_domain)
    result = await db.execute(select(Shop).where(Shop.shop_domain == current.shop_domain))
    shop = result.scalar_one_or_none()
    if not shop or not shop.is_active:
        raise HTTPException(404, "Shop not found or inactive")
    await set_shop_context(db, current.shop_domain, shop.id)
    return shop


def _serialize(job: PublishingAction) -> dict[str, Any]:
    return {
        "id": job.id,
        "action_type": job.action_type,
        "status": job.status,
        "idempotency_key": job.idempotency_key,
        "payload": json.loads(job.payload_json),
        "result": json.loads(job.result_json) if job.result_json else None,
        "error": job.error,
        "attempt_count": job.attempt_count,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "approved_at": job.approved_at.isoformat() if job.approved_at else None,
        "executed_at": job.executed_at.isoformat() if job.executed_at else None,
    }


class CreatePublishingRequest(BaseModel):
    action_type: Literal["shopify_product_channels", "meta_facebook", "meta_instagram"]
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    payload: dict[str, Any]

    @field_validator("payload")
    @classmethod
    def require_payload_object(cls, value):
        if not isinstance(value, dict):
            raise ValueError("payload must be an object")
        return value


@router.post("/requests", status_code=status.HTTP_201_CREATED)
async def create_publishing_request(
    body: CreatePublishingRequest,
    current: CurrentUser | None = Depends(get_optional_shop),
    db=Depends(get_db),
):
    shop = await _current_shop(current, db)
    payload = _safe_payload(body.action_type, body.payload)
    if body.action_type.startswith("meta_"):
        from app.routers.social import _account_match
        result = await db.execute(select(SocialAccount).where(
            _account_match(payload["account_id"]),
            SocialAccount.shop_id == shop.id,
            SocialAccount.provider == "meta",
            SocialAccount.account_type == "facebook_page",
            SocialAccount.is_active.is_(True),
        ))
        if result.scalar_one_or_none() is None:
            raise HTTPException(404, "Active Meta Page not found for this shop")
    payload_json = _canonical(payload)
    request_hash = hashlib.sha256((body.action_type + "\n" + payload_json).encode()).hexdigest()
    existing = await db.execute(select(PublishingAction).where(
        PublishingAction.shop_id == shop.id,
        PublishingAction.idempotency_key == body.idempotency_key,
    ))
    job = existing.scalar_one_or_none()
    if job:
        if job.request_hash != request_hash:
            raise HTTPException(409, "This idempotency key was already used for a different request")
        return JSONResponse(status_code=200, content={"created": False, "action": _serialize(job)})
    job = PublishingAction(
        shop_id=shop.id, action_type=body.action_type, status="pending_approval",
        idempotency_key=body.idempotency_key, request_hash=request_hash, payload_json=payload_json,
    )
    db.add(job)
    try:
        await db.commit()
        await db.refresh(job)
    except IntegrityError:
        await db.rollback()
        shop = await _current_shop(current, db)
        result = await db.execute(select(PublishingAction).where(
            PublishingAction.shop_id == shop.id,
            PublishingAction.idempotency_key == body.idempotency_key,
        ))
        job = result.scalar_one_or_none()
        if not job:
            raise HTTPException(409, "Publishing request could not be created; retry with a new idempotency key")
        if job.request_hash != request_hash:
            raise HTTPException(409, "This idempotency key was already used for a different request")
        return JSONResponse(status_code=200, content={"created": False, "action": _serialize(job)})
    await record_audit_event(db, shop_id=shop.id, shop_domain=shop.shop_domain,
        action="publishing.requested", entity_type="publishing_action", entity_id=str(job.id),
        details={"action_type": job.action_type, "status": job.status})
    return {"created": True, "action": _serialize(job)}


@router.get("/requests")
async def list_publishing_requests(
    current: CurrentUser | None = Depends(get_optional_shop), db=Depends(get_db),
):
    shop = await _current_shop(current, db)
    rows = await db.execute(select(PublishingAction).where(
        PublishingAction.shop_id == shop.id).order_by(PublishingAction.created_at.desc()).limit(100))
    return {"actions": [_serialize(row) for row in rows.scalars().all()]}


@router.get("/requests/{action_id}")
async def get_publishing_request(
    action_id: int, current: CurrentUser | None = Depends(get_optional_shop), db=Depends(get_db),
):
    shop = await _current_shop(current, db)
    result = await db.execute(select(PublishingAction).where(
        PublishingAction.id == action_id, PublishingAction.shop_id == shop.id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Publishing request not found")
    return {"action": _serialize(job)}


@router.post("/requests/{action_id}/approve")
async def approve_publishing_request(
    action_id: int, current: CurrentUser | None = Depends(get_optional_shop), db=Depends(get_db),
):
    shop = await _current_shop(current, db)
    shop_id = shop.id
    shop_domain = shop.shop_domain
    result = await db.execute(update(PublishingAction).where(
        PublishingAction.id == action_id, PublishingAction.shop_id == shop_id,
        PublishingAction.status == "pending_approval",
    ).values(status="approved", approved_at=_now()))
    if result.rowcount != 1:
        await db.rollback()
        check = await db.execute(select(PublishingAction).where(
            PublishingAction.id == action_id, PublishingAction.shop_id == shop_id))
        job = check.scalar_one_or_none()
        if not job:
            raise HTTPException(404, "Publishing request not found")
        raise HTTPException(409, f"Request cannot be approved from status '{job.status}'")
    await db.commit()
    check = await db.execute(select(PublishingAction).where(
        PublishingAction.id == action_id, PublishingAction.shop_id == shop_id))
    job = check.scalar_one()
    await record_audit_event(db, shop_id=shop_id, shop_domain=shop_domain,
        action="publishing.approved", entity_type="publishing_action", entity_id=str(job.id),
        details={"action_type": job.action_type, "status": job.status})
    return {"action": _serialize(job)}


async def _perform_action(job: PublishingAction, shop: Shop, current: CurrentUser, db) -> dict[str, Any]:
    payload = json.loads(job.payload_json)
    if job.action_type == "shopify_product_channels":
        if not shop.access_token_encrypted:
            raise RuntimeError("Shopify connection unavailable")
        client = await _publication_client(current, db)
        result = await client.set_product_publications(payload["product_id"], payload["publication_ids"], publish=payload["publish"])
        operation = "publishablePublish" if payload["publish"] else "publishableUnpublish"
        data = result.get(operation) or {}
        if data.get("userErrors"):
            raise ShopifyAPIError("Shopify rejected the publication change", status_code=400, response={"userErrors": data["userErrors"]})
        return {"provider": "shopify", "operation": operation, "product_id": payload["product_id"], "publication_count": len(payload["publication_ids"])}

    from app.routers.social import _get_meta_account, _external_result_id
    account, token = await _get_meta_account(payload["account_id"], current, db, "facebook_page")
    metadata = json.loads(account.metadata_json or "{}")
    caps = page_capabilities(metadata)
    if job.action_type == "meta_facebook":
        if not caps["can_publish_facebook"]:
            raise HTTPException(403, "This Page has not granted Mia content-publishing permission")
        result = await MetaClient().publish_page_post(account.external_account_id, token, payload["message"], payload.get("link"))
        external_id = _external_result_id(result)
        if not external_id:
            raise TimeoutError("Meta response did not confirm a post ID")
        return {"provider": "meta_facebook", "external_id": external_id}
    if not caps["instagram_linked"] or not caps["can_publish_instagram"]:
        raise HTTPException(403, "This Page is not ready for Instagram publishing")
    ig = metadata.get("instagram_business_account") or {}
    ig_id = ig.get("id") if isinstance(ig, dict) else ig
    container = await MetaClient().create_instagram_media(str(ig_id), payload["image_url"], payload["caption"], token)
    creation_id = container.get("id") if isinstance(container, dict) else None
    if not creation_id:
        raise TimeoutError("Instagram media container creation was not confirmed")
    result = await MetaClient().publish_instagram_media(str(ig_id), creation_id, token)
    external_id = _external_result_id(result)
    if not external_id:
        raise TimeoutError("Meta response did not confirm an Instagram post ID")
    return {"provider": "meta_instagram", "external_id": external_id}


@router.post("/requests/{action_id}/execute")
async def execute_publishing_request(
    action_id: int,
    current: CurrentUser | None = Depends(get_optional_shop),
    db=Depends(get_db),
):
    shop = await _current_shop(current, db)
    shop_id = shop.id
    shop_domain = shop.shop_domain
    claim = await db.execute(update(PublishingAction).where(
        PublishingAction.id == action_id, PublishingAction.shop_id == shop_id,
        PublishingAction.status == "approved",
    ).values(status="publishing", attempt_count=PublishingAction.attempt_count + 1))
    if claim.rowcount != 1:
        await db.rollback()
        result = await db.execute(select(PublishingAction).where(
            PublishingAction.id == action_id, PublishingAction.shop_id == shop_id))
        job = result.scalar_one_or_none()
        if not job:
            raise HTTPException(404, "Publishing request not found")
        raise HTTPException(409, f"Request is not executable from status '{job.status}'. Publishing actions are never automatically retried.")
    await db.commit()  # claim before external I/O to prevent concurrent duplicate calls
    result = await db.execute(select(PublishingAction).where(
        PublishingAction.id == action_id, PublishingAction.shop_id == shop_id))
    job = result.scalar_one()
    try:
        safe_result = await _perform_action(job, shop, current, db)
    except HTTPException as exc:
        await db.rollback()
        result = await db.execute(select(PublishingAction).where(PublishingAction.id == action_id, PublishingAction.shop_id == shop_id))
        job = result.scalar_one()
        job.status = "failed" if exc.status_code < 500 else "needs_review"
        job.error = "Provider rejected the action" if job.status == "failed" else "Provider outcome could not be confirmed; do not retry automatically"
        job.executed_at = _now()
        await db.commit()
        await record_audit_event(db, shop_id=shop_id, shop_domain=shop_domain,
            action="publishing.failed", entity_type="publishing_action", entity_id=str(job.id),
            details={"action_type": job.action_type, "status": job.status, "http_status": exc.status_code})
        raise HTTPException(exc.status_code, job.error) from exc
    except (ShopifyAPIError, MetaAPIError) as exc:
        await db.rollback()
        result = await db.execute(select(PublishingAction).where(PublishingAction.id == action_id, PublishingAction.shop_id == shop_id))
        job = result.scalar_one()
        code = getattr(exc, "status_code", 0)
        job.status = "failed" if 400 <= code < 500 else "needs_review"
        job.error = "Provider rejected the action" if job.status == "failed" else "Provider outcome could not be confirmed; do not retry automatically"
        job.executed_at = _now()
        await db.commit()
        await record_audit_event(db, shop_id=shop_id, shop_domain=shop_domain,
            action="publishing.failed", entity_type="publishing_action", entity_id=str(job.id),
            details={"action_type": job.action_type, "status": job.status, "provider_status": code or None})
        raise HTTPException(502 if job.status == "needs_review" else 400, job.error) from exc
    except Exception as exc:
        logger.warning("Publishing action %s has an unconfirmed provider outcome (%s)", action_id, type(exc).__name__)
        await db.rollback()
        result = await db.execute(select(PublishingAction).where(PublishingAction.id == action_id, PublishingAction.shop_id == shop_id))
        job = result.scalar_one()
        job.status = "needs_review"
        job.error = "Provider outcome could not be confirmed; do not retry automatically"
        job.executed_at = _now()
        await db.commit()
        await record_audit_event(db, shop_id=shop_id, shop_domain=shop_domain,
            action="publishing.needs_review", entity_type="publishing_action", entity_id=str(job.id),
            details={"action_type": job.action_type, "status": job.status})
        raise HTTPException(502, job.error) from exc
    job.status = "published"
    job.result_json = _canonical(safe_result)
    job.error = None
    job.executed_at = _now()
    await db.commit()
    await record_audit_event(db, shop_id=shop_id, shop_domain=shop_domain,
        action="publishing.published", entity_type="publishing_action", entity_id=str(job.id),
        details={"action_type": job.action_type, "status": job.status, **safe_result})
    return {"action": _serialize(job)}
