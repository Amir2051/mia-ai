from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.dependencies import CurrentUser, get_optional_shop
from app.models.database import get_db
from app.models.schemas import Shop
from app.security.tokens import decrypt_token
from app.services.analytics import compute_dashboard, compute_period_comparison, compute_recent_sales, compute_top_products, filter_orders_by_date, filter_orders_response
from app.services.orders import OrderService
from app.shopify.http_errors import shopify_http_error
from app.shopify.client import ShopifyAPIClient, ShopifyAPIError

router = APIRouter()

class ShopifyNotConnected(BaseModel):
    detail: str = "Shopify is not connected"

class AnalyticsResponse(BaseModel):
    connected: bool = False
    shop_domain: Optional[str] = None
    revenue: Optional[float] = None
    orders: Optional[int] = None
    customers: Optional[int] = None
    average_order_value: Optional[float] = None
    top_products: Optional[list[Dict[str, Any]]] = None
    recent_sales: Optional[list[Dict[str, Any]]] = None
    filtered: Optional[Dict[str, Any]] = None
    net_sales: Optional[float] = None
    gross_order_value: Optional[float] = None
    paid_orders: Optional[int] = None
    unpaid_orders: Optional[int] = None
    refunded_orders: Optional[int] = None
    cancelled_orders: Optional[int] = None
    customer_count_precision: Optional[str] = None
    customer_count_warning: Optional[str] = None
    coverage_warning: Optional[str] = None
    data_complete: Optional[bool] = None
    product_revenue_note: Optional[str] = None
    period_comparison: Optional[Dict[str, Any]] = None

async def _current_shop_or_connected_error(current: Optional[CurrentUser], db) -> Optional[Shop]:
    if not current:
        return None
    result = await db.execute(select(Shop).where(Shop.shop_domain == current.shop_domain))
    shop = result.scalar_one_or_none()
    if not shop or not shop.access_token_encrypted or not shop.is_active:
        return None
    return shop

async def _shop_client(shop: Shop) -> ShopifyAPIClient:
    try:
        access_token = decrypt_token(shop.access_token_encrypted or "")
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Stored Shopify access token could not be decrypted") from exc
    return ShopifyAPIClient(shop_domain=shop.shop_domain, access_token=access_token)

def _raise_shopify_error(exc: ShopifyAPIError, shop: Shop) -> None:
    raise shopify_http_error(exc) from exc

def _date_bound(value: Optional[str], *, end_of_day: bool = False) -> Optional[datetime]:
    if not value:
        return None
    try:
        if len(value) == 10:
            parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if end_of_day:
                parsed = parsed + timedelta(days=1) - timedelta(seconds=1)
            return parsed
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Date filters must use YYYY-MM-DD or an ISO-8601 date-time.",
        ) from exc


def _shopify_order_query(start: Optional[str] = None, end: Optional[str] = None) -> str:
    start_dt = _date_bound(start)
    end_dt = _date_bound(end, end_of_day=True)
    if start_dt and end_dt and start_dt > end_dt:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The start date must be on or before the end date.",
        )
    terms = []
    if start_dt:
        terms.append(f"created_at:>='{start_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}'")
    if end_dt:
        terms.append(f"created_at:<='{end_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}'")
    return " ".join(terms)


async def _load_orders(shop: Shop, query: str = "") -> tuple[dict, bool]:
    service = OrderService(await _shop_client(shop))
    edges = []
    after = None
    complete = False
    for _ in range(100):
        page = await service.list_orders(query=query, first=250, after=after)
        connection = page.get("orders") or {}
        edges.extend(connection.get("edges") or [])
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            complete = True
            break
        next_cursor = page_info.get("endCursor")
        if not next_cursor or next_cursor == after:
            break
        after = next_cursor
    return (
        {"orders": {"edges": edges, "pageInfo": {"hasNextPage": False, "endCursor": None}}},
        complete,
    )


async def _load_customer_count(shop: Shop) -> tuple[Optional[int], Optional[str]]:
    client = await _shop_client(shop)
    data = await client.graphql(
        "query MiaCustomersCount { customersCount { count precision } }"
    )
    result = data.get("customersCount") or {}
    count = result.get("count")
    precision = result.get("precision")
    return (int(count) if count is not None else None, str(precision) if precision else None)


def _coverage_warning(
    shop: Shop,
    start: Optional[str],
    end: Optional[str],
    data_complete: bool,
) -> Optional[str]:
    warnings = []
    scopes = {scope.strip() for scope in (shop.scope or "").split(",") if scope.strip()}
    start_dt = _date_bound(start)
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=60)
    if "read_all_orders" not in scopes and (start_dt is None or start_dt.date() < cutoff):
        warnings.append(
            "Shopify only exposes the most recent 60 days of order history to this app unless read_all_orders access is granted. "
            "Results for earlier dates may be incomplete."
        )
    if not data_complete:
        warnings.append(
            "Shopify pagination reached the 25,000-order safety limit before the result set ended; totals may be incomplete."
        )
    return " ".join(warnings) or None


PRODUCT_REVENUE_NOTE = (
    "Product revenue is estimated from discounted line-item unit prices and current quantities. "
    "Shop-level discounts may not be allocated to individual products."
)

@router.get("/", response_model=AnalyticsResponse)
async def analytics(start: Optional[str] = Query(None), end: Optional[str] = Query(None), current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    if not current:
        return AnalyticsResponse(connected=False)
    shop = await _current_shop_or_connected_error(current, db)
    if not shop:
        return AnalyticsResponse(connected=False)
    query = _shopify_order_query(start, end)
    try:
        order_data, data_complete = await _load_orders(shop, query=query)
    except NotImplementedError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except ShopifyAPIError as exc:
        _raise_shopify_error(exc, shop)

    # A count query avoids downloading customer profiles just to show a total.
    # This keeps unnecessary customer data out of memory and the AI context.
    customer_count = None
    customer_count_precision = None
    try:
        customer_count, customer_count_precision = await _load_customer_count(shop)
    except ShopifyAPIError as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            _raise_shopify_error(exc, shop)

    order_data = filter_orders_response(order_data, start=start, end=end)
    dashboard = compute_dashboard(order_data, customers_count=customer_count)
    return AnalyticsResponse(
        connected=True,
        shop_domain=shop.shop_domain,
        revenue=dashboard.get("revenue"),
        net_sales=dashboard.get("net_sales"),
        gross_order_value=dashboard.get("gross_order_value"),
        orders=dashboard.get("orders"),
        paid_orders=dashboard.get("paid_orders"),
        unpaid_orders=dashboard.get("unpaid_orders"),
        refunded_orders=dashboard.get("refunded_orders"),
        cancelled_orders=dashboard.get("cancelled_orders"),
        customers=dashboard.get("customers"),
        customer_count_precision=customer_count_precision,
        customer_count_warning=(
            "Shopify did not return a customer count; the value is unavailable, not zero."
            if customer_count is None else None
        ),
        average_order_value=dashboard.get("average_order_value"),
        top_products=compute_top_products(order_data, limit=5),
        recent_sales=compute_recent_sales(order_data, limit=10),
        coverage_warning=_coverage_warning(shop, start, end, data_complete),
        data_complete=data_complete,
        product_revenue_note=PRODUCT_REVENUE_NOTE,
        period_comparison=compute_period_comparison(order_data) if not start and not end else None,
    )

@router.get("/products", response_model=AnalyticsResponse)
async def product_performance(current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    if not current:
        return AnalyticsResponse(connected=False)
    shop = await _current_shop_or_connected_error(current, db)
    if not shop:
        return AnalyticsResponse(connected=False)
    try:
        order_data, data_complete = await _load_orders(shop)
    except NotImplementedError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except ShopifyAPIError as exc:
        _raise_shopify_error(exc, shop)
    return AnalyticsResponse(
        connected=True,
        shop_domain=shop.shop_domain,
        top_products=compute_top_products(order_data),
        coverage_warning=_coverage_warning(shop, None, None, data_complete),
        data_complete=data_complete,
        product_revenue_note=PRODUCT_REVENUE_NOTE,
    )

@router.get("/recent-sales", response_model=AnalyticsResponse)
async def recent_sales(current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    if not current:
        return AnalyticsResponse(connected=False)
    shop = await _current_shop_or_connected_error(current, db)
    if not shop:
        return AnalyticsResponse(connected=False)
    try:
        order_data, data_complete = await _load_orders(shop)
    except NotImplementedError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except ShopifyAPIError as exc:
        _raise_shopify_error(exc, shop)
    return AnalyticsResponse(
        connected=True,
        shop_domain=shop.shop_domain,
        recent_sales=compute_recent_sales(order_data),
        coverage_warning=_coverage_warning(shop, None, None, data_complete),
        data_complete=data_complete,
    )

@router.get("/filter", response_model=AnalyticsResponse)
async def filter_analytics(start: Optional[str] = Query(None), end: Optional[str] = Query(None), current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    if not current:
        return AnalyticsResponse(connected=False)
    shop = await _current_shop_or_connected_error(current, db)
    if not shop:
        return AnalyticsResponse(connected=False)
    query = _shopify_order_query(start, end)
    try:
        order_data, data_complete = await _load_orders(shop, query=query)
    except NotImplementedError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except ShopifyAPIError as exc:
        _raise_shopify_error(exc, shop)
    return AnalyticsResponse(
        connected=True,
        shop_domain=shop.shop_domain,
        filtered=filter_orders_by_date(order_data, start=start, end=end),
        coverage_warning=_coverage_warning(shop, start, end, data_complete),
        data_complete=data_complete,
    )
