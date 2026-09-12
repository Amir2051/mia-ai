from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.dependencies import CurrentUser, get_current_shop, get_optional_shop
from app.models.database import get_db
from app.models.schemas import Shop
from app.security.tokens import decrypt_token
from app.services.analytics import (
    compute_aov,
    compute_dashboard,
    compute_recent_sales,
    compute_top_products,
    filter_orders_by_date,
)
from app.services.customers import CustomerService
from app.services.orders import OrderService
from app.services.products import ProductService
from app.shopify.client import (
    ShopifyAPIClient,
    ShopifyAPIError,
)

router = APIRouter()


class ShopifyNotConnected(BaseModel):
    detail: str = 'Shopify is not connected'


class AnalyticsResponse(BaseModel):
    connected: bool = False
    shop_domain: Optional[str] = None
    revenue: Optional[float] = None
    orders: Optional[int] = None
    customers: Optional[int] = None
    average_order_value: Optional[float] = None
    top_products: Optional[List[Dict[str, Any]]] = None
    recent_sales: Optional[List[Dict[str, Any]]] = None
    filtered: Optional[Dict[str, Any]] = None


async def _current_shop_or_connected_error(
    current: Optional[CurrentUser],
    db,
) -> Optional[Shop]:
    if not current:
        return None

    result = await db.execute(
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


async def _shop_client(
    shop: Shop,
) -> ShopifyAPIClient:
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


@router.get('/', response_model=AnalyticsResponse)
async def analytics(
    current: Optional[CurrentUser] = Depends(
        get_optional_shop
    ),
    db=Depends(get_db),
):
    if not current:
        return AnalyticsResponse(connected=False)

    shop = await _current_shop_or_connected_error(current, db)
    if not shop:
        return AnalyticsResponse(connected=False)

    try:
        client = await _shop_client(shop)
        order_data = await OrderService(client).list_orders()
        customer_data = await CustomerService(client).list_customers()
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

    dashboard = compute_dashboard(
        order_data,
        customer_data,
    )

    return AnalyticsResponse(
        connected=True,
        shop_domain=shop.shop_domain,
        revenue=dashboard.get("revenue"),
        orders=dashboard.get("orders"),
        customers=dashboard.get("customers"),
        average_order_value=dashboard.get("average_order_value"),
    )


@router.get('/products', response_model=AnalyticsResponse)
async def product_performance(
    current: Optional[CurrentUser] = Depends(
        get_optional_shop
    ),
    db=Depends(get_db),
):
    if not current:
        return AnalyticsResponse(connected=False)

    shop = await _current_shop_or_connected_error(current, db)
    if not shop:
        return AnalyticsResponse(connected=False)

    try:
        client = await _shop_client(shop)
        order_data = await OrderService(client).list_orders()
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

    top_products = compute_top_products(order_data)

    return AnalyticsResponse(
        connected=True,
        shop_domain=shop.shop_domain,
        top_products=top_products,
    )


@router.get('/recent-sales', response_model=AnalyticsResponse)
async def recent_sales(
    current: Optional[CurrentUser] = Depends(
        get_optional_shop
    ),
    db=Depends(get_db),
):
    if not current:
        return AnalyticsResponse(connected=False)

    shop = await _current_shop_or_connected_error(current, db)
    if not shop:
        return AnalyticsResponse(connected=False)

    try:
        client = await _shop_client(shop)
        order_data = await OrderService(client).list_orders()
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

    recent = compute_recent_sales(order_data)

    return AnalyticsResponse(
        connected=True,
        shop_domain=shop.shop_domain,
        recent_sales=recent,
    )


@router.get('/filter', response_model=AnalyticsResponse)
async def filter_analytics(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    current: Optional[CurrentUser] = Depends(
        get_optional_shop
    ),
    db=Depends(get_db),
):
    if not current:
        return AnalyticsResponse(connected=False)

    shop = await _current_shop_or_connected_error(current, db)
    if not shop:
        return AnalyticsResponse(connected=False)

    try:
        client = await _shop_client(shop)
        order_data = await OrderService(client).list_orders()
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

    filtered = filter_orders_by_date(order_data, start=start, end=end)

    return AnalyticsResponse(
        connected=True,
        shop_domain=shop.shop_domain,
        filtered=filtered,
    )
