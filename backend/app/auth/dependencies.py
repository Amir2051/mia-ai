from typing import Optional
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select

from app.models.database import get_db
from app.models.schemas import Shop
from app.shopify.auth import AuthenticationError, decode_access_token
from app.shopify.config import settings


security = HTTPBearer(auto_error=False)


class CurrentUser:
    def __init__(
        self,
        shop_domain: str,
        scopes: Optional[str] = None,
        user_id: Optional[str] = None,
    ):
        self.shop_domain = shop_domain
        self.scopes = scopes
        self.user_id = user_id


def _normalize_bearer(
    credentials: Optional[HTTPAuthorizationCredentials],
) -> Optional[str]:
    if not credentials:
        return None

    token = (
        getattr(credentials, "credentials", "") or ""
    ).strip()

    if not token:
        return None

    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    return token or None


def _hostname(value: str) -> Optional[str]:
    try:
        parsed = urlparse(value)

        return (
            parsed.hostname.lower()
            if parsed.hostname
            else None
        )

    except Exception:
        return None


def _validate_shopify_id_token(token: str) -> dict:
    """
    Validate a Shopify App Bridge ID token.

    Shopify embedded apps send a short-lived JWT in:

        Authorization: Bearer <token>

    The token is signed with the Shopify app client secret.

    Temporary diagnostic logging below intentionally prints
    only non-secret JWT header/claim information. The raw
    JWT and Shopify API secret are never printed.
    """

    if not settings.shopify_api_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Shopify API secret is not configured",
        )

    if not settings.shopify_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Shopify API key is not configured",
        )

    # ---------------------------------------------------------
    # Read the JWT header without verifying the signature.
    # ---------------------------------------------------------

    try:
        header = jwt.get_unverified_header(token)

    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Shopify ID token",
            headers={
                "X-Shopify-Retry-Invalid-Session-Request": "1",
            },
        ) from exc

    # Shopify ID tokens should use HS256.
    if header.get("alg") != "HS256":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Shopify ID token algorithm",
            headers={
                "X-Shopify-Retry-Invalid-Session-Request": "1",
            },
        )

    # ---------------------------------------------------------
    # Verify the Shopify ID token cryptographically.
    # ---------------------------------------------------------

    try:
        payload = jwt.decode(
            token,
            settings.shopify_api_secret,
            algorithms=["HS256"],
            audience=settings.shopify_api_key,
            options={
                "verify_exp": True,
                "verify_nbf": True,
                "verify_aud": True,
            },
        )

    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Shopify ID token validation failed: "
                f"{type(exc).__name__}: {str(exc)}"
            ),
            headers={
                "X-Shopify-Retry-Invalid-Session-Request": "1",
            },
        ) from exc

    # --------------------------------------------------------
    # Validate required Shopify claims.
    # --------------------------------------------------------

    issuer = payload.get("iss")
    destination = payload.get("dest")
    subject = payload.get("sub")

    if not issuer or not destination or not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Shopify ID token claims",
            headers={
                "X-Shopify-Retry-Invalid-Session-Request": "1",
            },
        )

    issuer_host = _hostname(issuer)
    destination_host = _hostname(destination)

    if not issuer_host or not destination_host:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Shopify ID token destination",
            headers={
                "X-Shopify-Retry-Invalid-Session-Request": "1",
            },
        )

    # Shopify issuer and destination must refer to the same shop.
    if issuer_host != destination_host:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Shopify ID token issuer and "
                "destination do not match"
            ),
            headers={
                "X-Shopify-Retry-Invalid-Session-Request": "1",
            },
        )

    # The destination must be a Shopify *.myshopify.com domain.
    if not destination_host.endswith(
        ".myshopify.com"
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Shopify shop domain",
            headers={
                "X-Shopify-Retry-Invalid-Session-Request": "1",
            },
        )

    expected_issuer = (
        f"https://{destination_host}/admin"
    )

    if issuer != expected_issuer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Shopify ID token issuer",
            headers={
                "X-Shopify-Retry-Invalid-Session-Request": "1",
            },
        )

    payload["shop_domain"] = destination_host

    return payload


async def _optional_cookie_session(
    request: Request,
    db=Depends(get_db),
) -> Optional[CurrentUser]:
    """
    Legacy cookie fallback.

    Shopify embedded authentication should use the ID
    token above. This fallback remains available for
    local/legacy flows.
    """

    if not settings.session_cookie_name:
        return None

    cookie_token = request.cookies.get(
        settings.session_cookie_name
    )

    if not cookie_token:
        return None

    try:
        payload = decode_access_token(
            cookie_token
        )

    except AuthenticationError:
        return None

    shop_domain = payload.get("sub")

    if not shop_domain:
        return None

    result = await db.execute(
        select(Shop).where(
            Shop.shop_domain == shop_domain
        )
    )

    shop = result.scalar_one_or_none()

    if not shop or not shop.is_active:
        return None

    return CurrentUser(
        shop_domain=shop_domain,
        scopes=payload.get("scopes"),
    )


async def get_current_shop(
    credentials: Optional[
        HTTPAuthorizationCredentials
    ] = Depends(security),
    db=Depends(get_db),
    cookie_current: Optional[
        CurrentUser
    ] = Depends(_optional_cookie_session),
) -> CurrentUser:
    """
    Return the currently authenticated Shopify shop.

    Priority:

    1. Shopify App Bridge ID token
    2. Legacy application session cookie
    """

    token = _normalize_bearer(credentials)

    if token:
        try:
            payload = _validate_shopify_id_token(
                token
            )

            shop_domain = payload["shop_domain"]
            user_id = str(
                payload.get("sub")
            )

            result = await db.execute(
                select(Shop).where(
                    Shop.shop_domain == shop_domain
                )
            )

            shop = result.scalar_one_or_none()

            if not shop:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Shop is not connected",
                    headers={
                        "X-Shopify-Retry-Invalid-Session-Request": "1",
                    },
                )

            if not shop.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Shop is not active",
                    headers={
                        "X-Shopify-Retry-Invalid-Session-Request": "1",
                    },
                )

            return CurrentUser(
                shop_domain=shop_domain,
                scopes=shop.scope,
                user_id=user_id,
            )

        except HTTPException:
            raise

        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )

    if cookie_current:
        return cookie_current

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


async def get_optional_shop(
    credentials: Optional[
        HTTPAuthorizationCredentials
    ] = Depends(security),
    db=Depends(get_db),
    cookie_current: Optional[
        CurrentUser
    ] = Depends(_optional_cookie_session),
) -> Optional[CurrentUser]:
    """
    Optional authentication dependency.

    Returns None when no valid authentication
    is available.
    """

    token = _normalize_bearer(credentials)

    if token:
        try:
            return await get_current_shop(
                credentials=credentials,
                db=db,
                cookie_current=cookie_current,
            )

        except HTTPException:
            return None

    return cookie_current
