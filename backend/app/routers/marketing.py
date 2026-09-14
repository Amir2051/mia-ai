import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.dependencies import CurrentUser, get_optional_shop
from app.models.database import get_db
from app.models.schemas import Shop
from app.security.tokens import decrypt_token
from app.services.products import ProductService
from app.shopify.client import ShopifyAPIClient, ShopifyAPIError

router = APIRouter()


class MarketingResponse(BaseModel):
    connected: bool = False
    shop_domain: Optional[str] = None
    suggestions: Optional[List[Dict[str, Any]]] = None
    seo: Optional[Dict[str, Any]] = None
    campaigns: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None


async def _current_shop(current: Optional[CurrentUser], db) -> Optional[Shop]:
    if not current:
        return None
    result = await db.execute(select(Shop).where(Shop.shop_domain == current.shop_domain))
    shop = result.scalar_one_or_none()
    if not shop or not shop.access_token_encrypted or not shop.is_active:
        return None
    return shop


def _shop_client(shop: Shop) -> ShopifyAPIClient:
    try:
        access_token = decrypt_token(shop.access_token_encrypted or "")
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Stored Shopify access token could not be decrypted") from exc
    return ShopifyAPIClient(shop_domain=shop.shop_domain, access_token=access_token)


def _raise_shopify_error(exc: ShopifyAPIError) -> None:
    if exc.status_code == 401:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Shopify session credentials expired or were revoked. Please retry the Shopify session.",
            headers={"X-Shopify-Retry-Invalid-Session-Request": "1"},
        ) from exc
    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Shopify marketing data is temporarily unavailable. Please try again.") from exc


def _plain_text(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</p\s*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(value.split())


def _build_product_promotion(product: Dict[str, Any]) -> Dict[str, Any]:
    title = product.get("title") or "this product"
    product_status = product.get("status") or "draft"
    inventory = product.get("totalInventory")
    price = None
    variants = (((product.get("variants") or {}).get("edges")) or [])
    if variants:
        price = (variants[0].get("node") or {}).get("price")
    price_copy = f" at {price}" if price else ""
    inventory_copy = " Low stock — create urgency." if inventory is not None and inventory < 10 else (" In stock and ready to sell." if inventory is not None and inventory > 0 else " Out of stock — consider a preorder or restock campaign.")
    return {
        "title": title,
        "message": f"Promote {title}{price_copy}. Status: {product_status}.{inventory_copy}",
        "cta": "Shop now" if inventory is None or inventory > 0 else "Join waitlist",
        "channel": "social",
    }


def _build_email_draft(product: Dict[str, Any]) -> Dict[str, Any]:
    title = product.get("title") or "our latest product"
    description = _plain_text(product.get("descriptionHtml") or "")
    return {
        "subject": f"New arrival: {title}",
        "body": f"Hi there,\n\nTake a look at {title}.\n\n{description[:240]}\n\nView it in store before it’s gone.\n",
        "channel": "email",
    }


def _build_seo_suggestion(product: Dict[str, Any]) -> Dict[str, Any]:
    title = product.get("title") or "Product"
    description = _plain_text(product.get("descriptionHtml") or "")
    return {
        "seo_title": title,
        "seo_description": description[:160] or f"Shop {title} online.",
        "product_title": title,
        "channel": "seo",
    }


async def _products(shop: Shop) -> List[Dict[str, Any]]:
    data = await ProductService(_shop_client(shop)).list_products()
    edges = (((data.get("products") or {}).get("edges")) or [])
    return [edge.get("node", {}) for edge in edges[:5]]


@router.get('/', response_model=MarketingResponse)
async def marketing_overview(current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    if not current:
        return MarketingResponse(connected=False)
    shop = await _current_shop(current, db)
    if not shop:
        return MarketingResponse(connected=False)
    try:
        nodes = await _products(shop)
    except NotImplementedError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except ShopifyAPIError as exc:
        _raise_shopify_error(exc)
    return MarketingResponse(
        connected=True,
        shop_domain=shop.shop_domain,
        suggestions=[_build_product_promotion(node) for node in nodes],
        seo={"items": [_build_seo_suggestion(node) for node in nodes], "note": "SEO suggestions are derived from current product titles and descriptions."},
        campaigns=[_build_email_draft(node) for node in nodes],
    )


@router.get('/product/{product_id}', response_model=MarketingResponse)
async def product_marketing(product_id: str, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    if not current:
        return MarketingResponse(connected=False)
    shop = await _current_shop(current, db)
    if not shop:
        return MarketingResponse(connected=False)
    try:
        product = (await ProductService(_shop_client(shop)).get_product(product_id)).get("product") or {}
    except NotImplementedError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except ShopifyAPIError as exc:
        _raise_shopify_error(exc)
    return MarketingResponse(connected=True, shop_domain=shop.shop_domain, suggestions=[_build_product_promotion(product)], seo=_build_seo_suggestion(product), campaigns=[_build_email_draft(product)])


@router.get('/social', response_model=MarketingResponse)
async def social_copy(current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    if not current:
        return MarketingResponse(connected=False)
    shop = await _current_shop(current, db)
    if not shop:
        return MarketingResponse(connected=False)
    try:
        nodes = await _products(shop)
    except NotImplementedError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except ShopifyAPIError as exc:
        _raise_shopify_error(exc)
    return MarketingResponse(
        connected=True,
        shop_domain=shop.shop_domain,
        suggestions=[{"channel": "social", "message": f"New in store: {node.get('title') or 'our latest product'}. Shop now."} for node in nodes],
        error=None,
    )
