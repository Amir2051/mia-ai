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
from app.services.products import ProductService
from app.shopify.client import (
    ShopifyAPIClient,
    ShopifyAPIError,
)


router = APIRouter()


class ShopifyNotConnected(BaseModel):
    detail: str = "Shopify is not connected"


class ProductListResponse(BaseModel):
    data: Optional[Dict[str, Any]] = None
    connected: bool = False


class ProductUpdateResponse(BaseModel):
    data: Optional[Dict[str, Any]] = None
    connected: bool = False


class UpdateProductPayload(BaseModel):
    product: Dict[str, Any]


@router.put("/{product_id}", response_model=ProductUpdateResponse)
async def update_product(
    product_id: str,
    body: UpdateProductPayload,
    current: Optional[CurrentUser] = Depends(get_optional_shop),
    db=Depends(get_db),
):
    if not current:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Shopify session",
        )

    result = await db.execute(
        select(Shop).where(Shop.shop_domain == current.shop_domain)
    )
    shop = result.scalar_one_or_none()

    if not shop or not shop.access_token_encrypted or not shop.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Shopify is not connected",
        )

    try:
        access_token = decrypt_token(shop.access_token_encrypted)
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
        data = await ProductService(client).update_product(
            product_id,
            body.product,
        )
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

    return ProductUpdateResponse(data=data, connected=True)


@router.get(
    "/",
    response_model=ProductListResponse,
)
async def list_products(
    q: str = Query(""),
    current: Optional[CurrentUser] = Depends(
        get_optional_shop
    ),
    db=Depends(get_db),
):
    if not current:
        return ProductListResponse(
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
        return ProductListResponse(
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
        data = await ProductService(
            client
        ).list_products(query=q)

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

    return ProductListResponse(
        data=data,
        connected=True,
    )


@router.get(
    "/{product_id}",
    response_model=ProductListResponse,
)
async def get_product(
    product_id: str,
    current: Optional[CurrentUser] = Depends(
        get_optional_shop
    ),
    db=Depends(get_db),
):
    if not current:
        return ProductListResponse(
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
        return ProductListResponse(
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
        data = await ProductService(
            client
        ).get_product(product_id)

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

    return ProductListResponse(
        data=data,
        connected=True,
    )
