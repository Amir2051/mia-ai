from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.dependencies import CurrentUser, get_current_shop
from app.models.database import get_db
from app.models.schemas import Shop
from app.security.tokens import decrypt_token
from app.services.openrouter import OpenRouterError, OpenRouterService
from app.services.seo import SEOResult, SEOService, generate_marketing, normalize_handle, sanitize_html
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
        id title handle descriptionHtml productType vendor tags
        seo { title description }
        media(first: 50) { nodes { alt preview { image { url altText } } } }
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
        media.append({"id": node.get("id"), "alt": node.get("alt"), "altText": image.get("altText"), "url": image.get("url")})
    seo = product.get("seo") or {}
    return {
        **product,
        "seo_title": seo.get("title"),
        "seo_description": seo.get("description"),
        "images": media,
    }


def _shopify_error(exc: ShopifyAPIError) -> None:
    if exc.status_code == 401:
        raise HTTPException(status_code=401, detail="Shopify credentials expired or were revoked") from exc
    raise HTTPException(status_code=502, detail="Shopify data is temporarily unavailable") from exc


def _score_product_seo(product: dict[str, Any]) -> tuple[int, list[str], list[str]]:
    title = str(product.get("seo_title") or "").strip()
    description = str(product.get("seo_description") or "").strip()
    body = str(product.get("descriptionHtml") or "").strip()
    tags = product.get("tags") or []
    issues: list[str] = []
    recommendations = ["Use one clear primary keyword", "Keep title and meta description specific to the product"]
    score = 100
    if not title:
        score -= 30; issues.append("Missing SEO title")
    elif not 30 <= len(title) <= 70:
        score -= 15; issues.append("SEO title length could be improved")
    if not description:
        score -= 30; issues.append("Missing meta description")
    elif not 120 <= len(description) <= 170:
        score -= 15; issues.append("Meta description length could be improved")
    if not tags:
        score -= 10; issues.append("No Shopify tags")
    if not body:
        score -= 15; issues.append("Product description is empty")
    return max(0, score), issues, recommendations


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
    try:
        product = await _get_product(_client(shop), body.product_id)
        await db.commit()
        result, model, latency = await SEOService(OpenRouterService()).generate(product, model=body.model)
    except ShopifyAPIError as exc:
        _shopify_error(exc)
    except OpenRouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    generated = result.model_dump()
    # Report the score against the generated fields, using the same deterministic
    # scoring rules as Analyze rather than trusting a model-supplied score.
    generated_product = {**product, "seo_title": result.seo_title, "seo_description": result.meta_description, "descriptionHtml": result.optimized_description_html, "tags": result.tags}
    generated["seo_score"], generated["issues"], generated["recommendations"] = _score_product_seo(generated_product)
    return {"product": product, "result": generated, "model": model, "latency_ms": round(latency, 2)}


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
    try:
        result = await client.update_product(body.product_id, changes) if changes else {"productUpdate": {"product": {"id": body.product_id}, "userErrors": []}}
        media_result = None
        if image_alt_text:
            current_product = await _get_product(client, body.product_id)
            media_nodes = current_product.get("images") or []
            media_updates = []
            for index, alt in enumerate(image_alt_text):
                if index < len(media_nodes) and media_nodes[index].get("id"):
                    media_updates.append({"id": media_nodes[index]["id"], "alt": alt})
            if media_updates:
                media_gql = """
                mutation UpdateProductMedia($productId: ID!, $media: [UpdateMediaInput!]!) {
                  productUpdateMedia(productId: $productId, media: $media) {
                    media { id alt }
                    mediaUserErrors { field message }
                  }
                }
                """
                media_result = await client.graphql(media_gql, {"productId": body.product_id, "media": media_updates})
                media_payload = media_result.get("productUpdateMedia") or {}
                if media_payload.get("mediaUserErrors"):
                    raise ShopifyAPIError("Shopify image alt-text update failed", status_code=200, response=media_result)
        updated = await _get_product(client, body.product_id)
    except ShopifyAPIError as exc:
        _shopify_error(exc)
    return {"success": True, "changes": {**changes, **({"image_alt_text": image_alt_text} if image_alt_text else {})}, "shopify": result, "media": media_result, "product": updated}


@router.post("/creative")
async def generate_product_creative(
    body: GenerateRequest,
    current: CurrentUser = Depends(get_current_shop),
    db=Depends(get_db),
):
    shop = await _shop(current, db)
    try:
        product = await _get_product(_client(shop), body.product_id)
        await db.commit()
        marketing_result, _, _ = await generate_marketing(OpenRouterService(), product, model=body.model)
        image = marketing_result.enhanced_image_url or marketing_result.image_url
        model = settings.openrouter_image_model if marketing_result.enhanced_image_url else None
        latency = 0.0
    except ShopifyAPIError as exc:
        _shopify_error(exc)
    except OpenRouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "product": product,
        "image": image,
        "model": model,
        "latency_ms": round(latency, 2),
        "used_reference_image": True,
        "image_source": "shopify",
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
    try:
        product = await _get_product(client, body.product_id)
        # Release the tenant DB transaction before the long-running external AI calls.
        # The dependency otherwise holds a PostgreSQL connection open for the entire
        # 60-120s workflow, which can leave the transaction's connection stale/closed
        # before FastAPI attempts the dependency's final commit. No DB work follows.
        await db.commit()
        ai = OpenRouterService()
        seo_result, seo_model, seo_latency = await SEOService(ai).generate(product, model=body.model)
        marketing_result, marketing_model, marketing_latency = await generate_marketing(ai, product, model=body.model)
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
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "product": product,
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


@router.post("/marketing")
async def generate_product_marketing(
    body: GenerateRequest,
    current: CurrentUser = Depends(get_current_shop),
    db=Depends(get_db),
):
    shop = await _shop(current, db)
    try:
        product = await _get_product(_client(shop), body.product_id)
        await db.commit()
        result, model, latency = await generate_marketing(OpenRouterService(), product, model=body.model)
    except ShopifyAPIError as exc:
        _shopify_error(exc)
    except OpenRouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"product": product, "result": result.model_dump(), "model": model, "latency_ms": round(latency, 2)}
