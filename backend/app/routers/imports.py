from typing import Any, Dict, List, Optional
import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.dependencies import (
    CurrentUser,
    get_current_shop,
    get_optional_shop,
)
from app.models.database import get_db
from app.models.schemas import Shop
from app.security.tokens import decrypt_token
from app.services.products import ImportService
from app.shopify.client import (
    ShopifyAPIClient,
    ShopifyAPIError,
)


router = APIRouter()

class ImportResponse(BaseModel):
    data: Optional[Dict[str, Any]] = None
    connected: bool = False


@router.get(
    "/",
    response_model=ImportResponse,
)
async def list_imports(
    current: Optional[CurrentUser] = Depends(
        get_optional_shop
    ),
    db=Depends(get_db),
):
    if not current:
        return ImportResponse(
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
        return ImportResponse(
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
        data = await ImportService(
            db_session=db,
            shop=shop,
        ).list_imports()

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

    return ImportResponse(
        data=data,
        connected=True,
    )


@router.get("/{import_id}", response_model=ImportResponse)
async def get_import(
    import_id: str,
    current: Optional[CurrentUser] = Depends(
        get_optional_shop
    ),
    db=Depends(get_db),
):
    if not current:
        return ImportResponse(
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
        return ImportResponse(
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

    try:
        data = await ImportService(
            db_session=db,
            shop=shop,
        ).get_import(int(import_id))

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

    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Import not found",
        )

    return ImportResponse(
        data=data,
        connected=True,
    )


class ImportCreateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    supplier: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    cost: Optional[float] = None
    retail_price: Optional[float] = None
    inventory: Optional[int] = None
    images: Optional[str] = None
    variants_json: Optional[str] = None
    source: Optional[str] = "manual"
    supplier_sku: Optional[str] = None


class ImportCreateResponse(BaseModel):
    data: Optional[Dict[str, Any]] = None
    connected: bool = False
    error: Optional[str] = None
    userErrors: Optional[List[Dict[str, Any]]] = None


def _is_shopify_validation_error(exc: ShopifyAPIError) -> bool:
    return exc.status_code == 200 and bool(exc.response.get("userErrors"))


class ImportPreviewRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    status: Optional[str] = "DRAFT"
    content: Optional[str] = None
    mapping: Optional[Dict[str, str]] = None
    duplicate_action: Optional[str] = "skip"
    validate_only: Optional[bool] = False


class ImportRunRequest(BaseModel):
    mapping: Optional[Dict[str, str]] = None
    status: Optional[str] = "DRAFT"
    duplicate_action: Optional[str] = "skip"
    content: Optional[str] = None


@router.post("/preview", response_model=ImportCreateResponse)
async def preview_import(
    request: Request,
    payload: ImportPreviewRequest,
    current: Optional[CurrentUser] = Depends(get_optional_shop),
    db=Depends(get_db),
):
    if not current:
        return ImportCreateResponse(data=None, connected=False)

    result = await db.execute(
        select(Shop).where(Shop.shop_domain == current.shop_domain)
    )
    shop = result.scalar_one_or_none()

    if not shop or not shop.access_token_encrypted or not shop.is_active:
        return ImportCreateResponse(data=None, connected=False)

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
        data = await ImportService(
            db_session=db,
            shop=shop,
            api_client=client,
        ).create_import_from_csv(
            shop_id=shop.id,
            payload={**payload.dict(exclude_none=True), "validate_only": True},
        )
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except ShopifyAPIError as exc:
        if _is_shopify_validation_error(exc):
            return ImportCreateResponse(
                data=None,
                connected=True,
                error=str(exc),
                userErrors=(
                    exc.response.get("userErrors") if isinstance(exc.response, dict) else None
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return ImportCreateResponse(data=data, connected=True)


@router.post("/{import_id}/run", response_model=ImportCreateResponse)
async def run_import(
    import_id: int,
    payload: Optional[ImportRunRequest] = None,
    current: Optional[CurrentUser] = Depends(get_optional_shop),
    db=Depends(get_db),
):
    if not current:
        return ImportCreateResponse(data=None, connected=False)

    result = await db.execute(
        select(Shop).where(Shop.shop_domain == current.shop_domain)
    )
    shop = result.scalar_one_or_none()

    if not shop or not shop.access_token_encrypted or not shop.is_active:
        return ImportCreateResponse(data=None, connected=False)

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

    import_service = ImportService(
        db_session=db,
        shop=shop,
        api_client=client,
    )
    existing = await import_service.get_import(import_id)

    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Import not found",
        )

    run_payload = (payload.dict(exclude_none=True) if payload else {}) or {}
    run_payload["validate_only"] = False
    if not run_payload.get("content"):
        run_payload["content"] = existing.get("description") or ""

    try:
        data = await import_service.update_import_from_csv(
            import_id=import_id,
            payload=run_payload,
        )
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except ShopifyAPIError as exc:
        if _is_shopify_validation_error(exc):
            return ImportCreateResponse(
                data=None,
                connected=True,
                error=str(exc),
                userErrors=(
                    exc.response.get("userErrors") if isinstance(exc.response, dict) else None
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return ImportCreateResponse(data=data, connected=True)


@router.post("/", response_model=ImportCreateResponse)
async def create_import(
    payload: ImportCreateRequest,
    current: Optional[CurrentUser] = Depends(
        get_current_shop
    ),
    db=Depends(get_db),
):
    if not current:
        return ImportCreateResponse(
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
        return ImportCreateResponse(
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
        data = await ImportService(
            db_session=db,
            shop=shop,
        ).create_import(
            payload.dict(exclude_none=True),
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

    return ImportCreateResponse(
        data=data,
        connected=True,
    )
