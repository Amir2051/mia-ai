from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.dependencies import (
    CurrentUser,
    get_optional_shop,
)
from app.models.database import get_db
from app.models.schemas import Shop, ShopSession
from app.security.tokens import encrypt_token
from app.shopify.auth import (
    AuthenticationError,
    AuthorizationError,
    ShopifyOAuthFlow,
    _validate_shop_domain,
    generate_nonce,
    generate_state,
)
from app.shopify.config import settings


router = APIRouter()


class SessionResponse(BaseModel):
    shop_domain: Optional[str] = None
    scopes: Optional[str] = None
    connected: bool = False


class AuthorizationResponse(BaseModel):
    authorization_url: str


@router.get("/session", response_model=SessionResponse)
async def session(
    request: Request,
    response: Response,
    db=Depends(get_db),
):
    """
    Return the current Shopify connection status.

    Embedded Shopify apps authenticate with a short-lived
    Shopify App Bridge ID token in the Authorization header.

    On the first authenticated request, the ID token is exchanged
    for a Shopify offline access token. The access token is encrypted
    before being stored in the local Shop record.

    Existing active shops with a stored access token do not perform
    token exchange again on every session request.
    """

    authorization = request.headers.get(
        "Authorization",
        "",
    ).strip()

    # No authentication header.
    if not authorization:
        return SessionResponse(
            connected=False,
        )

    # Only Bearer authentication is supported.
    if not authorization.lower().startswith("bearer "):
        return SessionResponse(
            connected=False,
        )

    id_token = authorization[7:].strip()

    if not id_token:
        return SessionResponse(
            connected=False,
        )

    # Validate the Shopify App Bridge ID token.
    from app.auth.dependencies import (
        _validate_shopify_id_token,
    )

    try:
        payload = _validate_shopify_id_token(
            id_token,
        )
    except HTTPException as exc:
        response.headers[
            "X-Shopify-Retry-Invalid-Session-Request"
        ] = "1"
        raise exc

    shop_domain = payload.get(
        "shop_domain",
    )

    if not shop_domain:
        response.headers[
            "X-Shopify-Retry-Invalid-Session-Request"
        ] = "1"

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Shopify shop could not be determined",
            headers={
                "X-Shopify-Retry-Invalid-Session-Request": "1",
            },
        )

    # Look for an existing local Shop record.
    result = await db.execute(
        select(Shop).where(
            Shop.shop_domain == shop_domain,
        )
    )

    shop = result.scalar_one_or_none()

    # Existing active connection.
    if (
        shop is not None
        and shop.is_active
        and shop.access_token_encrypted
    ):
        return SessionResponse(
            shop_domain=shop.shop_domain,
            scopes=shop.scope,
            connected=True,
        )

    # First connection or reactivation.
    oauth = ShopifyOAuthFlow()

    try:
        token_data = (
            oauth.exchange_id_token_for_access_token(
                shop=shop_domain,
                id_token=id_token,
            )
        )
    except (
        AuthenticationError,
        AuthorizationError,
    ) as exc:
        response.headers[
            "X-Shopify-Retry-Invalid-Session-Request"
        ] = "1"

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={
                "X-Shopify-Retry-Invalid-Session-Request": "1",
            },
        ) from exc

    access_token = token_data.get(
        "access_token",
    )

    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Shopify token exchange returned "
                "no access token"
            ),
        )

    # Never store the Shopify access token in plaintext.
    encrypted_access_token = encrypt_token(
        access_token,
    )

    scope = (
        token_data.get("scope")
        or settings.shopify_scopes
    )

    # Create the local Shop record if necessary.
    if shop is None:
        shop = Shop(
            shop_domain=shop_domain,
            access_token_encrypted=encrypted_access_token,
            scope=scope,
            is_active=True,
        )
        db.add(shop)
    else:
        shop.access_token_encrypted = (
            encrypted_access_token
        )
        shop.scope = scope
        shop.is_active = True

    await db.commit()

    return SessionResponse(
        shop_domain=shop_domain,
        scopes=scope,
        connected=True,
    )


@router.get("/install", response_model=AuthorizationResponse)
async def install(
    shop: str = Query(...),
    db=Depends(get_db),
):
    """
    Start the Shopify OAuth authorization flow.
    """
    oauth = ShopifyOAuthFlow()

    try:
        shop = _validate_shop_domain(shop)
    except AuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    state = generate_state()
    nonce = generate_nonce()

    existing = await db.execute(
        select(Shop).where(Shop.shop_domain == shop)
    )
    shop_obj = existing.scalar_one_or_none()

    if shop_obj is None:
        shop_obj = Shop(
            shop_domain=shop,
            is_active=False,
        )
        db.add(shop_obj)
        await db.commit()
        await db.refresh(shop_obj)

    session_obj = ShopSession(
        shop_id=shop_obj.id,
        state=state,
        nonce=nonce,
        is_valid=True,
    )
    db.add(session_obj)
    await db.commit()

    authorization_url = (
        oauth.build_authorization_url(
            shop=shop,
            state=state,
            nonce=nonce,
        )
    )

    return AuthorizationResponse(
        authorization_url=authorization_url,
    )


@router.post("/logout")
async def logout(
    current: Optional[CurrentUser] = Depends(get_optional_shop),
    db=Depends(get_db),
):
    """
    Disconnect a Shopify shop by deactivating the local shop record.
    """
    if not current:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Shopify session",
        )

    result = await db.execute(
        select(Shop).where(
            Shop.shop_domain == current.shop_domain,
        )
    )
    shop = result.scalar_one_or_none()

    if not shop:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Shop not found",
        )

    shop.is_active = False
    shop.access_token_encrypted = None
    shop.scope = None
    await db.commit()

    return {"status": "ok"}
