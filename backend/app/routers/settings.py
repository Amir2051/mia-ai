import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from app.auth.dependencies import CurrentUser, get_optional_shop
from app.models.database import get_db
from app.models.schemas import AppSetting, Shop, SocialAccount
from app.security.tokens import decrypt_token
from app.shopify.client import ShopifyAPIClient, ShopifyAPIError
from app.shopify.config import settings

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



def _scope_set(value: Optional[str]) -> set[str]:
    return {item.strip() for item in (value or "").split(",") if item.strip()}


def _effective_scopes(granted: set[str]) -> set[str]:
    effective = set(granted)
    for scope in list(granted):
        if scope.startswith("write_"):
            effective.add("read_" + scope[len("write_"):])
    return effective


def _provider_health(provider: str, label: str, configured: bool, account_count: int) -> dict[str, Any]:
    if account_count:
        status_value = "saved_account_not_verified"
        message = "An account is saved, but its provider token has not been live-tested by this check."
    elif configured:
        status_value = "configured_not_tested"
        message = "Credentials are configured; no provider API call was made."
    else:
        status_value = "not_configured"
        message = "Provider credentials are incomplete."
    return {
        "provider": provider,
        "label": label,
        "status": status_value,
        "configured": configured,
        "saved_accounts": account_count,
        "message": message,
    }


@router.get("/health")
async def integration_health(
    current: Optional[CurrentUser] = Depends(get_optional_shop),
    db=Depends(get_db),
):
    """Read-only integration diagnostics; never sends a billable AI request."""
    ai_configured = bool(settings.openrouter_api_key.strip() and settings.openrouter_model.strip())
    ai = {
        "provider": "OpenRouter",
        "status": "configured_not_tested" if ai_configured else "not_configured",
        "configured": ai_configured,
        "model": settings.openrouter_model,
        "message": (
            "Credentials and model are configured. This check does not send a billable model request."
            if ai_configured else "OpenRouter API credentials or model configuration are missing."
        ),
    }

    if not current:
        return {
            "connected": False,
            "overall_status": "not_connected",
            "database": {"status": "not_checked"},
            "shopify": {"status": "not_connected"},
            "ai": ai,
            "social": [],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        await db.rollback()
        return {
            "connected": True,
            "overall_status": "degraded",
            "database": {"status": "unavailable", "error": "The application database could not be reached."},
            "shopify": {"status": "not_checked", "shop_domain": current.shop_domain},
            "ai": ai,
            "social": [],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    shop, access_token = await _get_shop_from_current(current, db)
    granted_scopes = _scope_set(shop.scope)
    configured_scopes = _scope_set(settings.shopify_scopes)
    missing_scopes = sorted(configured_scopes - _effective_scopes(granted_scopes))

    shopify_status = "unavailable"
    shop_info = None
    shopify_error = None
    try:
        data = await ShopifyAPIClient(
            shop_domain=shop.shop_domain,
            access_token=access_token,
        ).graphql("query MiaIntegrationHealth { shop { name myshopifyDomain } }")
        shop_info = data.get("shop") or {}
        returned_domain = str(shop_info.get("myshopifyDomain") or "").lower()
        if returned_domain == shop.shop_domain.lower():
            shopify_status = "verified_with_permission_gaps" if missing_scopes else "verified"
        elif returned_domain:
            shopify_status = "shop_mismatch"
            shopify_error = "Shopify returned a different shop domain than the authenticated session."
        else:
            shopify_status = "unavailable"
            shopify_error = "Shopify did not return store information."
    except ShopifyAPIError as exc:
        response = exc.response or {}
        errors = response.get("errors") or []
        if isinstance(errors, dict):
            errors = [errors]
        access_denied = any(
            isinstance(item, dict)
            and str((item.get("extensions") or {}).get("code", "")).upper() == "ACCESS_DENIED"
            for item in errors
        )
        if exc.status_code == 401:
            shopify_status = "needs_reauth"
            shopify_error = "Shopify rejected the stored access token. Refresh the embedded session and retry."
        elif exc.status_code == 403 or access_denied:
            shopify_status = "permission_issue"
            shopify_error = "Shopify denied the health query. Review the app's granted permissions."
        else:
            shopify_status = "unavailable"
            shopify_error = "Shopify could not be reached. Retry the diagnostic check."

    if shopify_status == "verified" and missing_scopes:
        shopify_status = "verified_with_permission_gaps"

    result = await db.execute(
        select(SocialAccount.provider, func.count(SocialAccount.id))
        .where(SocialAccount.shop_id == shop.id, SocialAccount.is_active.is_(True))
        .group_by(SocialAccount.provider)
    )
    account_counts = {str(provider): int(count) for provider, count in result.all()}

    social = [
        _provider_health(
            "meta",
            "Meta (Facebook / Instagram)",
            bool(settings.meta_app_id.strip() and settings.meta_app_secret.strip() and settings.meta_login_config_id.strip()),
            account_counts.get("meta", 0),
        ),
        _provider_health(
            "tiktok",
            "TikTok",
            bool(settings.tiktok_client_key.strip() and settings.tiktok_client_secret.strip()),
            account_counts.get("tiktok", 0),
        ),
        _provider_health(
            "pinterest",
            "Pinterest",
            bool(settings.pinterest_client_id.strip() and settings.pinterest_client_secret.strip()),
            account_counts.get("pinterest", 0),
        ),
        _provider_health(
            "youtube",
            "YouTube",
            bool(settings.youtube_client_id.strip() and settings.youtube_client_secret.strip()),
            account_counts.get("youtube", 0),
        ),
        _provider_health(
            "linkedin",
            "LinkedIn",
            bool(settings.linkedin_client_id.strip() and settings.linkedin_client_secret.strip()),
            account_counts.get("linkedin", 0),
        ),
    ]

    if (
        shopify_status != "verified"
        or missing_scopes
        or not ai_configured
    ):
        overall_status = "degraded"
    else:
        overall_status = "healthy"

    return {
        "connected": True,
        "overall_status": overall_status,
        "database": {"status": "healthy", "check": "SELECT 1"},
        "shopify": {
            "status": shopify_status,
            "shop_domain": shop.shop_domain,
            "shop_name": (shop_info or {}).get("name"),
            "granted_scopes": sorted(granted_scopes),
            "missing_scopes": missing_scopes,
            "error": shopify_error,
        },
        "ai": ai,
        "social": social,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
