import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.dependencies import CurrentUser, get_optional_shop
from app.models.database import get_db
from app.models.schemas import AppSetting, Shop
from app.security.tokens import decrypt_token

router = APIRouter()


class AppSettingsResponse(BaseModel):
    connected: bool
    shop_domain: Optional[str] = None
    settings: Dict[str, Any] = Field(default_factory=dict)


class UpsertAppSettingsRequest(BaseModel):
    settings: Dict[str, Any]


async def _get_shop_from_current(current: Optional[CurrentUser], db):
    if not current:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Shopify session")

    result = await db.execute(select(Shop).where(Shop.shop_domain == current.shop_domain))
    shop = result.scalar_one_or_none()
    if not shop or not shop.access_token_encrypted or not shop.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Shopify is not connected")

    try:
        access_token = decrypt_token(shop.access_token_encrypted)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Stored Shopify access token could not be decrypted") from exc

    return shop, access_token


async def _read_settings(db, shop_id: int) -> Dict[str, Any]:
    result = await db.execute(select(AppSetting).where(AppSetting.shop_id == shop_id))
    rows = result.scalars().all()
    values: Dict[str, Any] = {}
    for row in rows:
        if not row.key:
            continue
        try:
            values[row.key] = json.loads(row.value_json) if row.value_json is not None else None
        except (TypeError, ValueError):
            values[row.key] = row.value_json
    return values


@router.get("/", response_model=AppSettingsResponse)
async def get_app_settings(current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    if not current:
        return AppSettingsResponse(connected=False)
    shop, _ = await _get_shop_from_current(current, db)
    return AppSettingsResponse(connected=True, shop_domain=shop.shop_domain, settings=await _read_settings(db, shop.id))


@router.post("/", response_model=AppSettingsResponse)
async def upsert_app_settings(body: UpsertAppSettingsRequest, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    if not current:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Shopify session")

    shop, _ = await _get_shop_from_current(current, db)

    for key, value in body.settings.items():
        if not isinstance(key, str) or not key.strip():
            continue
        key = key.strip()
        value_json = json.dumps(value) if value is not None else None
        result = await db.execute(select(AppSetting).where(AppSetting.shop_id == shop.id, AppSetting.key == key))
        existing = result.scalar_one_or_none()
        if existing:
            existing.value_json = value_json
        else:
            db.add(AppSetting(shop_id=shop.id, key=key, value_json=value_json))

    await db.commit()
    return AppSettingsResponse(connected=True, shop_domain=shop.shop_domain, settings=await _read_settings(db, shop.id))
