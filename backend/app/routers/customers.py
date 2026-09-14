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
from app.services.customers import CustomerService
from app.shopify.client import (
    ShopifyAPIClient,
    ShopifyAPIError,
)


router = APIRouter()


class CustomerListResponse(BaseModel):
    data: Optional[Dict[str, Any]] = None
    connected: bool = False
    available: bool = True
    message: Optional[str] = None


def _is_protected_customer_access_denied(exc: ShopifyAPIError) -> bool:
    """Return True when Shopify rejected Customer access because PCD is not approved."""
    response = exc.response or {}
    errors = response.get("errors") or []
    if isinstance(errors, dict):
        errors = [errors]
    for error in errors:
        if not isinstance(error, dict):
            continue
        if (error.get("extensions") or {}).get("code") == "ACCESS_DENIED":
            message = str(error.get("message") or "").lower()
            if "customer" in message or "protected" in message:
                return True
    return False


def _empty_customer_connection() -> Dict[str, Any]:
    """Keep the response shape valid so the UI can render an empty state."""
    return {
        "customers": {
            "edges": [],
            "pageInfo": {
                "hasNextPage": False,
                "endCursor": None,
            },
        }
    }


@router.get(
    "/",
    response_model=CustomerListResponse,
)
async def list_customers(
    q: str = Query(""),
    current: Optional[CurrentUser] = Depends(
        get_optional_shop
    ),
    db=Depends(get_db),
):
    if not current:
        return CustomerListResponse(
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
        return CustomerListResponse(
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
        data = await CustomerService(
            client
        ).list_customers(query=q)

    except NotImplementedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc

    except ShopifyAPIError as exc:
        # Shopify returns HTTP 200 + ACCESS_DENIED when protected Customer
        # data has not been approved. Do not turn that expected capability
        # state into a 502 that breaks the Customers screen.
        if _is_protected_customer_access_denied(exc):
            return CustomerListResponse(
                data=_empty_customer_connection(),
                connected=True,
                available=False,
                message=(
                    "Shopify has not approved this app for protected customer "
                    "data. Customers will appear after that access is approved."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return CustomerListResponse(
        data=data,
        connected=True,
    )


@router.get(
    "/{customer_id:path}",
    response_model=CustomerListResponse,
)
async def get_customer(
    customer_id: str,
    current: Optional[CurrentUser] = Depends(
        get_optional_shop
    ),
    db=Depends(get_db),
):
    if not current:
        return CustomerListResponse(
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
        return CustomerListResponse(
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
        data = await CustomerService(
            client
        ).get_customer(customer_id)

    except NotImplementedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc

    except ShopifyAPIError as exc:
        if _is_protected_customer_access_denied(exc):
            return CustomerListResponse(
                data=None,
                connected=True,
                available=False,
                message=(
                    "Shopify has not approved this app for protected customer "
                    "data. Customer details are unavailable until access is approved."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return CustomerListResponse(
        data=data,
        connected=True,
    )
