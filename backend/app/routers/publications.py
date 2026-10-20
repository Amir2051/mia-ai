from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.auth.dependencies import CurrentUser, get_optional_shop
from app.models.database import get_db
from app.models.schemas import Shop
from app.security.tokens import decrypt_token
from app.shopify.client import ShopifyAPIClient, ShopifyAPIError

router = APIRouter()


def _shopify_error(exc: ShopifyAPIError) -> HTTPException:
    response = exc.response or {}
    errors = response.get("errors") or []
    if isinstance(errors, dict):
        errors = [errors]
    messages = [
        str(item.get("message", ""))
        for item in errors
        if isinstance(item, dict)
    ]
    combined = " ".join(messages).lower()

    if exc.status_code == 401:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Shopify authorization expired. Reopen Mia in Shopify Admin to refresh the session.",
            headers={"X-Shopify-Retry-Invalid-Session-Request": "1"},
        )
    if exc.status_code == 403 or any(
        marker in combined
        for marker in ("access denied", "not authorized", "read_publications", "scope")
    ):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Mia cannot read Shopify sales channels with its current granted permissions. Confirm read_publications is granted to the app, then reinstall or reauthorize Mia from Shopify Admin.",
        )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Shopify sales-channel data is temporarily unavailable. Retry the request.",
    )


@router.get("/sales-channels")
async def list_sales_channels(
    current: Optional[CurrentUser] = Depends(get_optional_shop),
    db=Depends(get_db),
) -> Dict[str, Any]:
    """Read the current shop's Shopify sales-channel publications."""
    if not current:
        raise HTTPException(status_code=401, detail="Missing Shopify session")

    result = await db.execute(
        select(Shop).where(Shop.shop_domain == current.shop_domain)
    )
    shop = result.scalar_one_or_none()
    if not shop or not shop.access_token_encrypted or not shop.is_active:
        raise HTTPException(status_code=400, detail="Shopify is not connected")

    try:
        token = decrypt_token(shop.access_token_encrypted)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=500,
            detail="Stored Shopify access token could not be decrypted",
        ) from exc

    client = ShopifyAPIClient(shop_domain=shop.shop_domain, access_token=token)
    channels = []
    after = None
    try:
        for _ in range(20):
            data = await client.list_publications(first=100, after=after)
            connection = data.get("publications") or {}
            edges = connection.get("edges") or []
            channels.extend(
                edge["node"]
                for edge in edges
                if isinstance(edge, dict) and isinstance(edge.get("node"), dict)
            )
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            next_cursor = page_info.get("endCursor")
            if not next_cursor or next_cursor == after:
                break
            after = next_cursor
    except ShopifyAPIError as exc:
        raise _shopify_error(exc) from exc

    return {
        "connected": True,
        "shop_domain": shop.shop_domain,
        "count": len(channels),
        "sales_channels": channels,
    }
