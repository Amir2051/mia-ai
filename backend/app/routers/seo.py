from __future__ import annotations

import asyncio
import json
from datetime import timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app.auth.dependencies import CurrentUser, get_current_shop
from app.models.database import get_db
from app.models.schemas import AuditLog, Shop, MiaMemory
from app.security.tokens import decrypt_token
from app.services.audit import record_audit_event
from app.services.openrouter import OpenRouterError, OpenRouterService
from app.services.seo import SEOResult, SEOService, generate_marketing, normalize_handle, sanitize_html
from app.services import billing as _billing
from app.shopify.http_errors import shopify_http_error
from app.shopify.client import ShopifyAPIClient, ShopifyAPIError
from app.shopify.config import settings

router = APIRouter()


class ProductRequest(BaseModel):
    product_id: str = Field(min_length=1, max_length=255)


class ApplyRequest(ProductRequest):
    changes: dict[str, Any]
    confirmed: bool = False
    apply_handle: bool = False


class GenerateRequest(ProductRequest):
    model: Optional[str] = None


async def _shop(current: CurrentUser, db) -> Shop:
    result = await db.execute(select(Shop).where(Shop.shop_domain == current.shop_domain))
    shop = result.scalar_one_or_none()
    if not shop or not shop.access_token_encrypted or not shop.is_active:
        raise HTTPException(status_code=401, detail="Shop is not connected")
    return shop


def _client(shop: Shop) -> ShopifyAPIClient:
    try:
        token = decrypt_token(shop.access_token_encrypted or "")
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail="Stored Shopify access token could not be decrypted") from exc
    return ShopifyAPIClient(shop_domain=shop.shop_domain, access_token=token)


async def _get_product(client: ShopifyAPIClient, product_id: str) -> dict[str, Any]:
    gql = """
    query GetSEOProduct($id: ID!) {
      product(id: $id) {
        id title handle descriptionHtml productType vendor tags status onlineStoreUrl
        variants(first: 1) { edges { node { price } } }
        seo { title description }
        media(first: 50) { nodes { id mediaContentType alt preview { image { url altText } } } }
      }
    }
    """
    data = await client.graphql(gql, {"id": product_id})
    product = data.get("product")
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    media = []
    for node in ((product.get("media") or {}).get("nodes") or []):
        preview = node.get("preview") or {}
        image = preview.get("image") or {}
        # Product media also contains videos and 3D models. Shopify's alt-text
        # mutation only applies to image media, so never send non-image IDs.
        if node.get("mediaContentType") != "IMAGE" or not image.get("url"):
            continue
        media.append({
            "id": node.get("id"),
            "alt": node.get("alt") or image.get("altText"),
            "altText": image.get("altText"),
            "url": image.get("url"),
        })
    seo = product.get("seo") or {}
    return {
        **product,
        "seo_title": seo.get("title"),
        "seo_description": seo.get("description"),
        "images": media,
    }


def _shopify_error(exc: ShopifyAPIError) -> None:
    raise shopify_http_error(
        exc,
        fallback_detail="Shopify data is temporarily unavailable",
    ) from exc


def _score_product_seo(product: dict[str, Any]) -> tuple[int, list[str], list[str]]:
    title = str(product.get("seo_title") or "").strip()
    description = str(product.get("seo_description") or "").strip()
    body = str(product.get("descriptionHtml") or "").strip()
    tags = product.get("tags") or []
    images = product.get("images") or []
    issues: list[str] = []
    recommendations: list[str] = []
    score = 100
    if not title:
        score -= 30; issues.append("Missing SEO title")
        recommendations.append("Add a clear SEO title using the product name and primary keyword")
    elif not 30 <= len(title) <= 70:
        score -= 15; issues.append("SEO title length could be improved")
        recommendations.append("Aim for an SEO title between 30 and 70 characters")
    if not description:
        score -= 30; issues.append("Missing meta description")
        recommendations.append("Write a unique meta description for this product")
    elif not 120 <= len(description) <= 170:
        score -= 15; issues.append("Meta description length could be improved")
        recommendations.append("Aim for a specific meta description around 140–160 characters")
    if not tags:
        score -= 10; issues.append("No Shopify tags")
        recommendations.append("Add a small set of relevant product tags")
    if not body:
        score -= 15; issues.append("Product description is empty")
        recommendations.append("Add a useful product description based only on verified product details")
    product_type = str(product.get("productType") or "").strip()
    primary_keyword = product_type or str(product.get("title") or "").strip()
    if primary_keyword and title and primary_keyword.casefold() not in title.casefold():
        score -= 10; issues.append("Primary product keyword is missing from the SEO title")
        recommendations.append("Include the primary product keyword naturally in the SEO title")
    if images and any(not str(image.get("alt") or image.get("altText") or "").strip() for image in images):
        score -= 5; issues.append("One or more product images are missing alt text")
        recommendations.append("Add concise, accurate alt text to each product image")
    if not recommendations:
        recommendations.append("Keep the product title, description, and metadata consistent with verified product details")
    return max(0, score), issues, recommendations


@router.get("/history")
async def marketing_history(
    current: CurrentUser = Depends(get_current_shop),
    db=Depends(get_db),
):
    """Return the current merchant's recent SEO/marketing outcomes only."""
    shop = await _shop(current, db)
    result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.shop_id == shop.id,
            or_(AuditLog.action.like("seo.%"), AuditLog.action.like("marketing.%")),
        )
        .order_by(AuditLog.created_at.desc())
        .limit(50)
    )
    items = []
    for event in result.scalars().all():
        try:
            details = json.loads(event.details_json or "{}")
        except (TypeError, ValueError):
            details = {}
        created_at = event.created_at
        if created_at and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        items.append({
            "id": event.id,
            "action": event.action,
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "details": details,
            "created_at": created_at.isoformat() if created_at else None,
        })
    return {"items": items}


@router.post("/analyze")
async def analyze_seo(
    body: ProductRequest,
    current: CurrentUser = Depends(get_current_shop),
    db=Depends(get_db),
):
    shop = await _shop(current, db)
    try:
        product = await _get_product(_client(shop), body.product_id)
    except ShopifyAPIError as exc:
        _shopify_error(exc)
    # Deterministic analysis does not require an AI call.
    score, issues, recommendations = _score_product_seo(product)
    return {"product": product, "seo_score": score, "issues": issues, "recommendations": recommendations}


@router.post("/generate")
async def generate_seo(
    body: GenerateRequest,
    current: CurrentUser = Depends(get_current_shop),
    db=Depends(get_db),
):
    shop = await _shop(current, db)
    client = _client(shop)
    shop_id, shop_domain = shop.id, shop.shop_domain
    await db.commit()
    try:
        product = await _get_product(client, body.product_id)
        result, model, latency = await SEOService(OpenRouterService()).generate(product, model=body.model)
    except ShopifyAPIError as exc:
        _shopify_error(exc)
    except OpenRouterError as exc:
        await record_audit_event(
            db,
            shop_id=shop_id,
            shop_domain=shop_domain,
            action="seo.preview_failed",
            entity_type="product",
            entity_id=body.product_id,
            details={"status": "failed", "product_title": product.get("title"), "error_type": "ai_provider_error"},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    generated = result.model_dump()
    # Report the score against the generated fields, using the same deterministic
    # scoring rules as Analyze rather than trusting a model-supplied score.
    generated_product = {**product, "seo_title": result.seo_title, "seo_description": result.meta_description, "descriptionHtml": result.optimized_description_html, "tags": result.tags}
    generated["seo_score"], generated["issues"], generated["recommendations"] = _score_product_seo(generated_product)
    audit_recorded = await record_audit_event(
        db,
        shop_id=shop_id,
        shop_domain=shop_domain,
        action="seo.preview",
        entity_type="product",
        entity_id=body.product_id,
        details={
            "status": "preview_ready",
            "product_title": product.get("title"),
            "seo_score": generated.get("seo_score"),
            "model": model,
            "latency_ms": round(latency, 2),
            "suggested_fields": ["title", "descriptionHtml", "seo", "tags", "image_alt_text"],
        },
    )
    return {"product": product, "result": generated, "model": model, "latency_ms": round(latency, 2), "audit_recorded": audit_recorded}


@router.post("/apply")
async def apply_seo(
    body: ApplyRequest,
    current: CurrentUser = Depends(get_current_shop),
    db=Depends(get_db),
):
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required before applying SEO changes")
    allowed = {"title", "descriptionHtml", "seo", "tags", "handle", "image_alt_text"}
    changes = {key: value for key, value in body.changes.items() if key in allowed}
    if "title" in changes:
        if not isinstance(changes["title"], str) or len(changes["title"]) > 255:
            raise HTTPException(status_code=422, detail="title must be a string of 255 characters or fewer")
        changes["title"] = changes["title"].strip()
    if "descriptionHtml" in changes:
        if not isinstance(changes["descriptionHtml"], str) or len(changes["descriptionHtml"]) > 20000:
            raise HTTPException(status_code=422, detail="descriptionHtml is invalid")
        changes["descriptionHtml"] = sanitize_html(changes["descriptionHtml"])
    if "tags" in changes:
        if not isinstance(changes["tags"], list) or len(changes["tags"]) > 30:
            raise HTTPException(status_code=422, detail="tags must be a list of 30 items or fewer")
        changes["tags"] = [str(tag).strip()[:255] for tag in changes["tags"] if str(tag).strip()]
    if not body.apply_handle:
        changes.pop("handle", None)
    elif "handle" in body.changes:
        if not isinstance(body.changes["handle"], str):
            raise HTTPException(status_code=422, detail="handle must be a string")
        normalized_handle = normalize_handle(body.changes["handle"])
        if not normalized_handle:
            raise HTTPException(status_code=422, detail="handle must contain at least one letter or number")
        changes["handle"] = normalized_handle
    image_alt_text = changes.pop("image_alt_text", None)
    if image_alt_text is not None:
        if not isinstance(image_alt_text, list) or len(image_alt_text) > 50:
            raise HTTPException(status_code=422, detail="image_alt_text must be a list of 50 items or fewer")
        image_alt_text = [str(v).strip()[:500] for v in image_alt_text if str(v).strip()]
    if "seo" in changes:
        if not isinstance(changes["seo"], dict):
            raise HTTPException(status_code=422, detail="seo must be an object")
        seo = changes["seo"]
        seo = {k: seo[k] for k in ("title", "description") if k in seo}
        if any(not isinstance(v, str) for v in seo.values()):
            raise HTTPException(status_code=422, detail="seo title and description must be strings")
        if len(seo.get("title", "")) > 70 or len(seo.get("description", "")) > 320:
            raise HTTPException(status_code=422, detail="SEO metadata exceeds allowed length")
        changes["seo"] = seo
    if not changes and not image_alt_text:
        raise HTTPException(status_code=400, detail="No permitted SEO changes supplied")
    shop = await _shop(current, db)
    client = _client(shop)
    shop_id, shop_domain = shop.id, shop.shop_domain
    await db.commit()
    try:
        before = await _get_product(client, body.product_id)
        if image_alt_text and len(image_alt_text) > len(before.get("images") or []):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="More image alt-text values were supplied than this product has images.",
            )
        result = await client.update_product(body.product_id, changes) if changes else {"productUpdate": {"product": {"id": body.product_id}, "userErrors": []}}
        update_payload = result.get("productUpdate") or {}
        user_errors = update_payload.get("userErrors") or []
        if user_errors:
            messages = [str(item.get("message") or "Shopify rejected a field") for item in user_errors if isinstance(item, dict)]
            await record_audit_event(
                db,
                shop_id=shop_id,
                shop_domain=shop_domain,
                action="seo.apply_failed",
                entity_type="product",
                entity_id=body.product_id,
                details={
                    "status": "failed",
                    "product_title": before.get("title"),
                    "fields": sorted(changes.keys()),
                    "error_count": len(user_errors),
                    "error_type": "shopify_user_errors",
                },
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Shopify rejected the requested changes: " + "; ".join(messages[:5]),
            )
        if changes and not update_payload.get("product"):
            await record_audit_event(
                db,
                shop_id=shop_id,
                shop_domain=shop_domain,
                action="seo.apply_failed",
                entity_type="product",
                entity_id=body.product_id,
                details={
                    "status": "failed",
                    "product_title": before.get("title"),
                    "fields": sorted(changes.keys()),
                    "error_type": "shopify_update_not_confirmed",
                },
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Shopify did not confirm the requested product update. Reload the product before retrying.",
            )
        media_result = None
        media_error = None
        if image_alt_text:
            media_nodes = before.get("images") or []
            media_updates = [
                {"id": media_nodes[index]["id"], "alt": alt}
                for index, alt in enumerate(image_alt_text)
                if index < len(media_nodes) and media_nodes[index].get("id")
            ]
            if media_updates:
                media_gql = """
                mutation UpdateProductMedia($productId: ID!, $media: [UpdateMediaInput!]!) {
                  productUpdateMedia(productId: $productId, media: $media) {
                    media { id alt }
                    mediaUserErrors { field message }
                  }
                }
                """
                try:
                    media_result = await client.graphql(media_gql, {"productId": body.product_id, "media": media_updates})
                    media_payload = media_result.get("productUpdateMedia") or {}
                    media_user_errors = media_payload.get("mediaUserErrors") or []
                    if media_user_errors:
                        messages = [str(item.get("message") or "Shopify rejected an image alt-text change") for item in media_user_errors if isinstance(item, dict)]
                        media_error = "; ".join(messages[:3]) or "Shopify rejected image alt-text changes"
                    elif not media_payload.get("media"):
                        media_error = "Shopify did not return updated image media for verification"
                except ShopifyAPIError as exc:
                    # Product text may already have been updated; preserve that success
                    # while reporting the media failure and checking the actual saved state.
                    media_error = f"Image alt-text update failed (Shopify HTTP {exc.status_code})"
        updated = await _get_product(client, body.product_id)
    except ShopifyAPIError as exc:
        await record_audit_event(
            db,
            shop_id=shop_id,
            shop_domain=shop_domain,
            action="seo.apply_failed",
            entity_type="product",
            entity_id=body.product_id,
            details={
                "status": "failed",
                "product_title": before.get("title") if "before" in locals() else None,
                "error_type": "shopify_api_error",
                "http_status": exc.status_code,
            },
        )
        _shopify_error(exc)

    mismatched_fields = []
    if "title" in changes and updated.get("title") != changes["title"]:
        mismatched_fields.append("title")
    if "handle" in changes and updated.get("handle") != changes["handle"]:
        mismatched_fields.append("handle")
    if "tags" in changes and set(updated.get("tags") or []) != set(changes["tags"]):
        mismatched_fields.append("tags")
    if "seo" in changes:
        requested_seo = changes["seo"]
        if "title" in requested_seo and updated.get("seo_title") != requested_seo["title"]:
            mismatched_fields.append("seo.title")
        if "description" in requested_seo and updated.get("seo_description") != requested_seo["description"]:
            mismatched_fields.append("seo.description")
    if image_alt_text:
        updated_images = updated.get("images") or []
        for index, alt_text in enumerate(image_alt_text):
            if index < len(updated_images) and updated_images[index].get("alt") != alt_text:
                mismatched_fields.append("image_alt_text")
                break
    if mismatched_fields and set(mismatched_fields) != {"image_alt_text"}:
        await record_audit_event(
            db,
            shop_id=shop_id,
            shop_domain=shop_domain,
            action="seo.apply_failed",
            entity_type="product",
            entity_id=body.product_id,
            details={
                "status": "verification_failed",
                "product_title": updated.get("title"),
                "fields": sorted(set(mismatched_fields)),
                "error_type": "post_update_verification_mismatch",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Shopify accepted the update request, but verification did not confirm every field: "
            + ", ".join(sorted(set(mismatched_fields)))
            + ". Review the product before retrying.",
        )

    # The post-update read is authoritative. A transient mutation response/error
    # should not be labelled partial if Shopify's saved values actually match.
    partial_success = "image_alt_text" in mismatched_fields
    failed_fields = ["image_alt_text"] if partial_success else []
    if partial_success and not media_error:
        media_error = "Shopify's saved image alt text did not match the approved values"
    before_snapshot = {
        "title": before.get("title"),
        "seo_title": before.get("seo_title"),
        "seo_description": before.get("seo_description"),
        "handle": before.get("handle"),
        "tags": before.get("tags") or [],
    }
    after_snapshot = {
        "title": updated.get("title"),
        "seo_title": updated.get("seo_title"),
        "seo_description": updated.get("seo_description"),
        "handle": updated.get("handle"),
        "tags": updated.get("tags") or [],
    }
    audit_recorded = await record_audit_event(
        db,
        shop_id=shop_id,
        shop_domain=shop_domain,
        action="seo.partial_success" if partial_success else "seo.applied",
        entity_type="product",
        entity_id=body.product_id,
        details={
            "status": "partial_success" if partial_success else "applied",
            "product_title": updated.get("title"),
            "fields": sorted([*changes.keys(), *(["image_alt_text"] if image_alt_text and not partial_success else [])]),
            "failed_fields": failed_fields,
            "media_error": media_error,
            "before": before_snapshot,
            "after": after_snapshot,
        },
    )
    return {
        "success": True,
        "partial_success": partial_success,
        "failed_fields": failed_fields,
        "message": (
            "Product SEO fields were applied, but image alt text could not be confirmed."
            if partial_success else "All approved SEO changes were applied and verified."
        ),
        "audit_recorded": audit_recorded,
        "changes": {**changes, **({"image_alt_text": image_alt_text} if image_alt_text else {})},
        "shopify": result,
        "media": media_result,
        "product": updated,
    }


@router.post("/creative")
async def generate_product_creative(
    body: GenerateRequest,
    current: CurrentUser = Depends(get_current_shop),
    db=Depends(get_db),
):
    shop = await _shop(current, db)
    client = _client(shop)
    shop_id, shop_domain = shop.id, shop.shop_domain
    notes = await _merchant_notes(db, shop)
    await db.commit()
    try:
        product = await _get_product(client, body.product_id)
        marketing_result, _, _ = await generate_marketing(OpenRouterService(), product, model=body.model, merchant_notes=notes)
        image = marketing_result.enhanced_image_url or marketing_result.image_url
        model = settings.openrouter_image_model if marketing_result.enhanced_image_url else None
        latency = 0.0
    except ShopifyAPIError as exc:
        _shopify_error(exc)
    except OpenRouterError as exc:
        await record_audit_event(
            db,
            shop_id=shop_id,
            shop_domain=shop_domain,
            action="marketing.creative_failed",
            entity_type="product",
            entity_id=body.product_id,
            details={"status": "failed", "product_title": product.get("title"), "error_type": "ai_provider_error"},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audit_recorded = await record_audit_event(
        db,
        shop_id=shop_id,
        shop_domain=shop_domain,
        action="marketing.creative_preview",
        entity_type="product",
        entity_id=body.product_id,
        details={
            "status": "preview_ready" if image else "partial_success",
            "product_title": product.get("title"),
            "image_available": bool(image),
            "image_enhancement_source": marketing_result.image_enhancement_source,
            "error": marketing_result.image_enhancement_error,
        },
    )
    return {
        "product": product,
        "image": image,
        "model": model,
        "latency_ms": round(latency, 2),
        "used_reference_image": True,
        "image_source": "shopify",
        "audit_recorded": audit_recorded,
        "image_status": {
            "image_enhancement_source": marketing_result.image_enhancement_source,
            "error": marketing_result.image_enhancement_error,
            "retry_after_seconds": marketing_result.image_retry_after_seconds,
        },
    }


@router.post("/full-campaign")
async def generate_full_campaign(
    body: GenerateRequest,
    current: CurrentUser = Depends(get_current_shop),
    db=Depends(get_db),
):
    """Generate the complete SEO + marketing + creative package in one live-store workflow."""
    shop = await _shop(current, db)
    client = _client(shop)
    shop_id, shop_domain = shop.id, shop.shop_domain
    notes = await _merchant_notes(db, shop)
    # Release the tenant DB transaction before the long-running external calls.
    await db.commit()
    try:
        product = await _get_product(client, body.product_id)
        ai = OpenRouterService()
        # These are independent, read-only AI generations. Run them concurrently so
        # two slow provider responses don't stack up against Cloudflare's 120s limit.
        seo_task = SEOService(ai).generate(product, model=body.model, merchant_notes=notes)
        marketing_task = generate_marketing(ai, product, model=body.model, merchant_notes=notes)
        (seo_result, seo_model, seo_latency), (marketing_result, marketing_model, marketing_latency) = await asyncio.gather(
            seo_task,
            marketing_task,
        )
        image = marketing_result.enhanced_image_url or marketing_result.image_url
        image_model = settings.openrouter_image_model if marketing_result.image_enhancement_source == "openrouter" else None
        image_latency = None
        image_error = marketing_result.image_enhancement_error
        if not image:
            image_error = "No Shopify product image is available"
        image_status_source = marketing_result.image_enhancement_source
    except ShopifyAPIError as exc:
        _shopify_error(exc)
    except OpenRouterError as exc:
        await record_audit_event(
            db,
            shop_id=shop_id,
            shop_domain=shop_domain,
            action="marketing.campaign_failed",
            entity_type="product",
            entity_id=body.product_id,
            details={"status": "failed", "product_title": product.get("title"), "error_type": "ai_provider_error"},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    campaign_audit_recorded = await record_audit_event(
        db,
        shop_id=shop_id,
        shop_domain=shop_domain,
        action="marketing.campaign_preview",
        entity_type="product",
        entity_id=body.product_id,
        details={
            "status": "partial_success" if image_error else "preview_ready",
            "product_title": product.get("title"),
            "seo_score": _score_product_seo({
                **product,
                "seo_title": seo_result.seo_title,
                "seo_description": seo_result.meta_description,
                "descriptionHtml": seo_result.optimized_description_html,
                "tags": seo_result.tags,
            })[0],
            "has_marketing_copy": bool(marketing_result),
            "image_available": bool(image),
            "partial_success": bool(image_error),
            "image_error": image_error,
            "image_enhancement_source": image_status_source,
            "image_retry_after_seconds": marketing_result.image_retry_after_seconds,
            "models": {"seo": seo_model, "marketing": marketing_model, "image": image_model},
            "latency_ms": round(seo_latency + marketing_latency + (image_latency or 0), 2),
        },
    )
    return {
        "product": product,
        "audit_recorded": campaign_audit_recorded,
        "seo": seo_result.model_dump(),
        "marketing": marketing_result.model_dump(),
        "creative": {
            "image": image,
            "model": image_model,
            "latency_ms": round(image_latency, 2) if image_latency is not None else None,
            "error": image_error,
            "image_enhancement_source": image_status_source,
            "retry_after_seconds": marketing_result.image_retry_after_seconds,
        },
        "models": {"seo": seo_model, "marketing": marketing_model},
        "latency_ms": round(seo_latency + marketing_latency + (image_latency or 0), 2),
        "partial_success": bool(image_error),
    }




async def _merchant_notes(db, shop) -> list[str]:
    """Load this shop's saved brand/audience notes (owner-scoped) for grounding copy.
    Must be called before the route releases its tenant transaction (db.commit())."""
    try:
        row = (await db.execute(select(MiaMemory).where(MiaMemory.shop_id == shop.id))).scalar_one_or_none()
        if not row or not getattr(row, 'knowledge_json', None):
            return []
        data = json.loads(row.knowledge_json)
        return [str(x)[:500] for x in data][-30:] if isinstance(data, list) else []
    except Exception:
        return []

@router.post("/marketing")
async def generate_product_marketing(
    body: GenerateRequest,
    current: CurrentUser = Depends(get_current_shop),
    db=Depends(get_db),
):
    shop = await _shop(current, db)
    client = _client(shop)
    shop_id, shop_domain = shop.id, shop.shop_domain
    notes = await _merchant_notes(db, shop)
    await db.commit()
    try:
        product = await _get_product(client, body.product_id)
        if not await _billing.is_entitled(client):
            raise HTTPException(status_code=402, detail="An active Mia AI subscription is required. Open the Billing page to subscribe.")
        result, model, latency = await generate_marketing(OpenRouterService(), product, model=body.model, merchant_notes=notes)
    except ShopifyAPIError as exc:
        _shopify_error(exc)
    except OpenRouterError as exc:
        await record_audit_event(
            db,
            shop_id=shop_id,
            shop_domain=shop_domain,
            action="marketing.preview_failed",
            entity_type="product",
            entity_id=body.product_id,
            details={"status": "failed", "product_title": product.get("title"), "error_type": "ai_provider_error"},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audit_recorded = await record_audit_event(
        db,
        shop_id=shop_id,
        shop_domain=shop_domain,
        action="marketing.preview",
        entity_type="product",
        entity_id=body.product_id,
        details={
            "status": "preview_ready",
            "product_title": product.get("title"),
            "model": model,
            "latency_ms": round(latency, 2),
            "channels": ["facebook", "instagram", "email"],
        },
    )
    return {"product": product, "result": result.model_dump(), "model": model, "latency_ms": round(latency, 2), "audit_recorded": audit_recorded}
