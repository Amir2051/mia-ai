from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.dependencies import (
    CurrentUser,
    get_optional_shop,
)
from app.models.database import get_db
from app.models.schemas import Shop
from app.security.tokens import decrypt_token
from app.services.orders import OrderService
from app.shopify.client import (
    ShopifyAPIClient,
    ShopifyAPIError,
)


router = APIRouter()


class OrderListResponse(BaseModel):
    data: Optional[Dict[str, Any]] = None
    connected: bool = False


@router.get(
    "/",
    response_model=OrderListResponse,
)
async def list_orders(
    q: str = Query(""),
    current: Optional[CurrentUser] = Depends(
        get_optional_shop
    ),
    db=Depends(get_db),
):
    if not current:
        return OrderListResponse(
            data=None,
            connected=False,
        )

    result = await db.execute(
        select(Shop).where(
            Shop.shop_domain == current.shop_domain
        )
    )

    shop = result.scalar_one_or_none()

    if (
        not shop
        or not shop.access_token_encrypted
        or not shop.is_active
    ):
        return OrderListResponse(
            data=None,
            connected=False,
        )

    try:
        access_token = decrypt_token(
            shop.access_token_encrypted
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored Shopify access token could not be decrypted",
        ) from exc

    client = ShopifyAPIClient(
        shop_domain=shop.shop_domain,
        access_token=access_token,
    )

    try:
        service = OrderService(client)
        orders = []
        after = None
        # Shopify caps each connection page at 250. Walk the full live order
        # history so the Orders screen is not limited to the newest 250.
        for _ in range(100):
            page = await service.list_orders(query=q, first=250, after=after)
            connection = page.get("orders") or {}
            orders.extend(
                edge for edge in (connection.get("edges") or [])
                if edge.get("node")
            )
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            next_cursor = page_info.get("endCursor")
            if not next_cursor or next_cursor == after:
                break
            after = next_cursor
        data = {
            "orders": {
                "edges": orders,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }

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

    return OrderListResponse(
        data=data,
        connected=True,
    )


@router.get(
    "/{order_id}",
    response_model=OrderListResponse,
)
async def get_order(
    order_id: str,
    current: Optional[CurrentUser] = Depends(
        get_optional_shop
    ),
    db=Depends(get_db),
):
    if not current:
        return OrderListResponse(
            data=None,
            connected=False,
        )

    result = await db.execute(
        select(Shop).where(
            Shop.shop_domain == current.shop_domain
        )
    )

    shop = result.scalar_one_or_none()

    if (
        not shop
        or not shop.access_token_encrypted
        or not shop.is_active
    ):
        return OrderListResponse(
            data=None,
            connected=False,
        )

    try:
        access_token = decrypt_token(
            shop.access_token_encrypted
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored Shopify access token could not be decrypted",
        ) from exc

    client = ShopifyAPIClient(
        shop_domain=shop.shop_domain,
        access_token=access_token,
    )

    try:
        data = await OrderService(
            client
        ).get_order(order_id)

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

    return OrderListResponse(
        data=data,
        connected=True,
    )
