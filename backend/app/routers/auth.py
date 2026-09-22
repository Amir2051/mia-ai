from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.dependencies import CurrentUser, get_optional_shop
from app.models.database import get_db
from app.models.schemas import Shop
from app.security.rls import set_shop_context
from app.security.tokens import encrypt_token
from app.shopify.auth import AuthenticationError, AuthorizationError, ShopifyOAuthFlow, _validate_shop_domain, now_utc
from app.shopify.config import settings


router = APIRouter()


class SessionResponse(BaseModel):
    shop_domain: Optional[str] = None
    scopes: Optional[str] = None
    connected: bool = False


class AuthorizationResponse(BaseModel):
    authorization_url: str


def _token_expiry(token_data: dict):
    expires_in = token_data.get("expires_in")
    if expires_in is None:
        return None
    try:
        return now_utc().replace(tzinfo=None) + timedelta(seconds=int(expires_in))
    except (TypeError, ValueError):
        return None


def _refresh_expiry(token_data: dict):
    expires_in = token_data.get("refresh_token_expires_in")
    if expires_in is None:
        return None
    try:
        return now_utc().replace(tzinfo=None) + timedelta(seconds=int(expires_in))
    except (TypeError, ValueError):
        return None


def _needs_token_exchange(shop: Shop) -> bool:
    if not shop.access_token_encrypted or not shop.is_active:
        return True
    if shop.access_token_expires_at is None:
        return True
    return shop.access_token_expires_at <= now_utc().replace(tzinfo=None) + timedelta(minutes=5)


@router.get("/session", response_model=SessionResponse)
async def session(request: Request, response: Response, force: bool = Query(False), db=Depends(get_db)):
    """Validate the embedded Shopify ID token and establish/refresh the offline Admin API token."""
    authorization = request.headers.get("Authorization", "").strip()
    if not authorization or not authorization.lower().startswith("bearer "):
        return SessionResponse(connected=False)

    id_token = authorization[7:].strip()
    if not id_token:
        return SessionResponse(connected=False)

    from app.auth.dependencies import _validate_shopify_id_token

    try:
        payload = _validate_shopify_id_token(id_token)
    except HTTPException as exc:
        response.headers["X-Shopify-Retry-Invalid-Session-Request"] = "1"
        raise exc

    shop_domain = payload.get("shop_domain")
    if not shop_domain:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Shopify shop could not be determined", headers={"X-Shopify-Retry-Invalid-Session-Request": "1"})

    await set_shop_context(db, shop_domain)
    result = await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))
    shop = result.scalar_one_or_none()
    if shop is not None:
        await set_shop_context(db, shop_domain, shop.id)
    if shop is not None and not force and not _needs_token_exchange(shop):
        return SessionResponse(shop_domain=shop.shop_domain, scopes=shop.scope, connected=True)

    result = await db.execute(select(Shop).where(Shop.shop_domain == shop_domain).with_for_update())
    shop = result.scalar_one_or_none()
    if shop is not None and not force and not _needs_token_exchange(shop):
        return SessionResponse(shop_domain=shop.shop_domain, scopes=shop.scope, connected=True)

    oauth = ShopifyOAuthFlow()
    try:
        token_data = oauth.exchange_id_token_for_access_token(shop=shop_domain, id_token=id_token)
    except (AuthenticationError, AuthorizationError) as exc:
        response.headers["X-Shopify-Retry-Invalid-Session-Request"] = "1"
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc), headers={"X-Shopify-Retry-Invalid-Session-Request": "1"}) from exc

    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Shopify token exchange returned no access token")

    encrypted_access_token = encrypt_token(access_token)
    refresh_token = token_data.get("refresh_token")
    scope = token_data.get("scope") or settings.shopify_scopes

    if shop is None:
        shop = Shop(
            shop_domain=shop_domain,
            access_token_encrypted=encrypted_access_token,
            access_token_expires_at=_token_expiry(token_data),
            refresh_token_encrypted=encrypt_token(str(refresh_token)) if refresh_token else None,
            refresh_token_expires_at=_refresh_expiry(token_data),
            scope=scope,
            is_active=True,
        )
        db.add(shop)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))
            shop = existing.scalar_one_or_none()
            if shop is None or not shop.is_active or not shop.access_token_encrypted:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Shop connection is being established. Please retry.")
    else:
        shop.access_token_encrypted = encrypted_access_token
        shop.access_token_expires_at = _token_expiry(token_data)
        shop.refresh_token_encrypted = encrypt_token(str(refresh_token)) if refresh_token else shop.refresh_token_encrypted
        shop.refresh_token_expires_at = _refresh_expiry(token_data) if refresh_token else shop.refresh_token_expires_at
        shop.scope = scope
        shop.is_active = True
        await db.commit()

    return SessionResponse(shop_domain=shop_domain, scopes=shop.scope, connected=True)


@router.post("/logout")
async def logout(current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    """Disconnect a Shopify shop by deactivating the local shop record."""
    if not current:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Shopify session")
    result = await db.execute(select(Shop).where(Shop.shop_domain == current.shop_domain))
    shop = result.scalar_one_or_none()
    if not shop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop not found")
    shop.is_active = False
    shop.access_token_encrypted = None
    shop.access_token_expires_at = None
    shop.refresh_token_encrypted = None
    shop.refresh_token_expires_at = None
    shop.scope = None
    await db.commit()
    return {"status": "ok"}
