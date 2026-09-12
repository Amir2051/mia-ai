from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.dependencies import CurrentUser, get_optional_shop
from app.models.database import get_db
from app.models.schemas import Shop
from app.security.tokens import decrypt_token
from app.services.analytics import compute_top_products
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


def _current_shop(current: Optional[CurrentUser], db) -> Optional[Shop]:
    if not current:
        return None

    result = db.execute(
        select(Shop).where(Shop.shop_domain == current.shop_domain)
    )
    shop = result.scalar_one_or_none()

    if (
        not shop
        or not shop.access_token_encrypted
        or not shop.is_active
    ):
        return None

    return shop


def _shop_client(shop: Shop) -> ShopifyAPIClient:
    try:
        access_token = decrypt_token(
            shop.access_token_encrypted or ""
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored Shopify access token could not be decrypted",
        ) from exc

    return ShopifyAPIClient(
        shop_domain=shop.shop_domain,
        access_token=access_token,
    )


def _build_product_promotion(product: Dict[str, Any]) -> Dict[str, Any]:
    title = product.get("title") or "this product"
    status = product.get("status") or "draft"
    inventory = product.get("totalInventory")
    price = None
    variants = (((product.get("variants") or {}).get("edges")) or [])
    if variants:
        first_variant = variants[0].get("node") or {}
        price = first_variant.get("price")

    price_copy = f" at {price}" if price else ""
    inventory_copy = (
        " Low stock — create urgency."
        if inventory is not None and inventory < 10
        else (
            " In stock and ready to sell."
            if inventory is not None and inventory > 0
            else " Out of stock — consider a preorder or restock campaign."
        )
    )

    return {
        "title": title,
        "message": (
            f"Promote {title}{price_copy}. "
            f"Status: {status}.{inventory_copy}"
        ),
        "cta": (
            "Shop now"
            if inventory is None or inventory > 0
            else "Join waitlist"
        ),
        "channel": "social",
    }


def _build_email_draft(product: Dict[str, Any]) -> Dict[str, Any]:
    title = product.get("title") or "our latest product"
    description = product.get("descriptionHtml") or ""
    plain_description = description.replace("<br>", "\n").replace("</p>", "\n")
    plain_description = " ".join(plain_description.split())

    subject = f"New arrival: {title}"
    body = (
        f"Hi there,\n\n"
        f"Take a look at {title}.\n\n"
        f"{plain_description[:240]}\n\n"
        f"View it in store before it’s gone.\n"
    )

    return {
        "subject": subject,
        "body": body,
        "channel": "email",
    }


def _build_seo_suggestion(product: Dict[str, Any]) -> Dict[str, Any]:
    title = product.get("title") or "Product"
    description = product.get("descriptionHtml") or ""
    plain_description = description.replace("<br>", " ").replace("</p>", " ")
    plain_description = " ".join(plain_description.split())
    seo_description = plain_description[:160] or f"Shop {title} online."

    return {
        "seo_title": title,
        "seo_description": seo_description,
        "product_title": title,
        "channel": "seo",
    }


@router.get('/', response_model=MarketingResponse)
async def marketing_overview(
    current: Optional[CurrentUser] = Depends(
        get_optional_shop
    ),
    db=Depends(get_db),
):
    if not current:
        return MarketingResponse(connected=False)

    shop = _current_shop(current, db)
    if not shop:
        return MarketingResponse(connected=False)

    try:
        client = _shop_client(shop)
        products_data = await ProductService(client).list_products()
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except ShopifyAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    products = (((products_data.get("products") or {}).get("edges")) or [])
    nodes = [edge.get("node", {}) for edge in products[:5]]

    suggestions = []
    seo_items = []
    campaigns = []
    for node in nodes:
        suggestions.append(_build_product_promotion(node))
        campaigns.append(_build_email_draft(node))
        seo_items.append(_build_seo_suggestion(node))

    return MarketingResponse(
        connected=True,
        shop_domain=shop.shop_domain,
        suggestions=suggestions,
        seo={
            "items": seo_items,
            "note": "SEO suggestions are derived from current product titles and descriptions.",
        },
        campaigns=campaigns,
    )


@router.get('/product/{product_id}', response_model=MarketingResponse)
async def product_marketing(
    product_id: str,
    current: Optional[CurrentUser] = Depends(
        get_optional_shop
    ),
    db=Depends(get_db),
):
    if not current:
        return MarketingResponse(connected=False)

    shop = _current_shop(current, db)
    if not shop:
        return MarketingResponse(connected=False)

    try:
        client = _shop_client(shop)
        product_data = await ProductService(client).get_product(product_id)
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except ShopifyAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    product = product_data.get("product") or {}

    return MarketingResponse(
        connected=True,
        shop_domain=shop.shop_domain,
        suggestions=[_build_product_promotion(product)],
        seo=_build_seo_suggestion(product),
        campaigns=[_build_email_draft(product)],
    )


@router.get('/social', response_model=MarketingResponse)
async def social_copy(
    current: Optional[CurrentUser] = Depends(
        get_optional_shop
    ),
    db=Depends(get_db),
):
    if not current:
        return MarketingResponse(connected=False)

    shop = _current_shop(current, db)
    if not shop:
        return MarketingResponse(connected=False)

    try:
        client = _shop_client(shop)
        products_data = await ProductService(client).list_products()
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except ShopifyAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    products = (((products_data.get("products") or {}).get("edges")) or [])
    nodes = [edge.get("node", {}) for edge in products[:5]]
    copy_lines = []
    for node in nodes:
        title = node.get("title") or "our latest product"
        copy_lines.append(
            {
                "channel": "social",
                "message": f"New in store: {title}. Shop now.",
            }
        )

    return MarketingResponse(
        connected=True,
        shop_domain=shop.shop_domain,
        suggestions=copy_lines,
        error=None,
    )
