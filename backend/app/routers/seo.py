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
from app.services.seo import SEOResult, SEOService, normalize_handle, sanitize_html
from app.shopify.client import ShopifyAPIClient, ShopifyAPIError

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
        media.append({"alt": node.get("alt"), "altText": image.get("altText"), "url": image.get("url")})
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
    title = product.get("seo_title") or ""
    description = product.get("seo_description") or ""
    issues: list[str] = []
    recommendations: list[str] = []
    score = 100
    if not title:
        score -= 30; issues.append("Missing SEO title")
    elif not 30 <= len(title) <= 70:
        score -= 15; issues.append("SEO title length could be improved")
    if not description:
        score -= 30; issues.append("Missing meta description")
    elif not 120 <= len(description) <= 170:
        score -= 15; issues.append("Meta description length could be improved")
    if not product.get("tags"):
        score -= 10; issues.append("No Shopify tags")
    if not product.get("descriptionHtml"):
        score -= 15; issues.append("Product description is empty")
    recommendations.extend(["Use one clear primary keyword", "Keep title and meta description specific to the product"])
    return {"product": product, "seo_score": max(0, score), "issues": issues, "recommendations": recommendations}


@router.post("/generate")
async def generate_seo(
    body: GenerateRequest,
    current: CurrentUser = Depends(get_current_shop),
    db=Depends(get_db),
):
    shop = await _shop(current, db)
    try:
        product = await _get_product(_client(shop), body.product_id)
        result, model, latency = await SEOService(OpenRouterService()).generate(product, model=body.model)
    except ShopifyAPIError as exc:
        _shopify_error(exc)
    except OpenRouterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"product": product, "result": result.model_dump(), "model": model, "latency_ms": round(latency, 2)}


@router.post("/apply")
async def apply_seo(
    body: ApplyRequest,
    current: CurrentUser = Depends(get_current_shop),
    db=Depends(get_db),
):
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required before applying SEO changes")
    allowed = {"title", "descriptionHtml", "seo", "tags"}
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
        changes["handle"] = normalize_handle(body.changes["handle"])
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
    if not changes:
        raise HTTPException(status_code=400, detail="No permitted SEO changes supplied")
    shop = await _shop(current, db)
    try:
        result = await _client(shop).update_product(body.product_id, changes)
        updated = await _get_product(_client(shop), body.product_id)
    except ShopifyAPIError as exc:
        _shopify_error(exc)
    return {"success": True, "changes": changes, "shopify": result, "product": updated}
