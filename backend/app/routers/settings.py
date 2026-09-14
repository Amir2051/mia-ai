from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.dependencies import CurrentUser, get_optional_shop
from app.models.database import get_db
from app.models.schemas import Shop, AppSetting
from app.security.tokens import decrypt_token


router = APIRouter()


class AppSettingsResponse(BaseModel):
    connected: bool
    shop_domain: Optional[str] = None
    settings: Dict[str, Any] = {}


class UpsertAppSettingsRequest(BaseModel):
    settings: Dict[str, Any]


def _shop_from_current(current: Optional[CurrentUser], db):
    if not current:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Shopify session")

    return current


async def _get_shop_from_current(current: Optional[CurrentUser], db):
    current = _shop_from_current(current, db)
    result = await db.execute(select(Shop).where(Shop.shop_domain == current.shop_domain))
    shop = result.scalar_one_or_none()

    if not shop or not shop.access_token_encrypted or not shop.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Shopify is not connected")

    try:
        access_token = decrypt_token(shop.access_token_encrypted)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Stored Shopify access token could not be decrypted") from exc

    return shop, access_token


@router.get("/", response_model=AppSettingsResponse)
async def get_app_settings(current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    if not current:
        return AppSettingsResponse(connected=False)

    try:
        shop, _ = await _get_shop_from_current(current, db)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            raise
        return AppSettingsResponse(connected=False)

    result = await db.execute(select(AppSetting).where(AppSetting.shop_id == shop.id))
    rows = result.scalars().all()
    settings: Dict[str, Any] = {}
    for row in rows:
        if not row.key:
            continue
        try:
            settings[row.key] = __import__("json").loads(row.value_json) if row.value_json is not None else None
        except Exception:  # noqa: BLE001
            settings[row.key] = row.value_json

    return AppSettingsResponse(connected=True, shop_domain=shop.shop_domain, settings=settings)


@router.post("/", response_model=AppSettingsResponse)
async def upsert_app_settings(body: UpsertAppSettingsRequest, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    if not current:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Shopify session")

    shop, _ = await _get_shop_from_current(current, db)

    for key, value in body.settings.items():
        if not isinstance(key, str) or not key:
            continue
        value_json = __import__("json").dumps(value) if value is not None else None

        result = await db.execute(select(AppSetting).where(AppSetting.shop_id == shop.id, AppSetting.key == key))
        existing = result.scalar_one_or_none()
        if existing:
            existing.value_json = value_json
        else:
            db.add(AppSetting(shop_id=shop.id, key=key, value_json=value_json))

    await db.commit()

    result = await db.execute(select(AppSetting).where(AppSetting.shop_id == shop.id))
    rows = result.scalars().all()
    settings: Dict[str, Any] = {}
    for row in rows:
        if not row.key:
            continue
        try:
            settings[row.key] = __import__("json").loads(row.value_json) if row.value_json is not None else None
        except Exception:  # noqa: BLE001
            settings[row.key] = row.value_json

    return AppSettingsResponse(connected=True, shop_domain=shop.shop_domain, settings=settings)
