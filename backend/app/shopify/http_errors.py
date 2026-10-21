"""Consistent, safe HTTP mapping for Shopify Admin API failures."""
from fastapi import HTTPException, status

from app.shopify.client import ShopifyAPIError


def shopify_http_error(
    exc: ShopifyAPIError,
    *,
    fallback_detail: str = "Shopify data is temporarily unavailable. Please try again.",
) -> HTTPException:
    """Map Shopify API failures without leaking raw provider responses.

    A 401 must remain a 401 so the frontend can exchange a fresh embedded
    session token and retry. Permission errors should be visible as 403s;
    other provider failures are transient gateway errors.
    """
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Shopify session credentials expired or were revoked. Please retry the Shopify session.",
            headers={"X-Shopify-Retry-Invalid-Session-Request": "1"},
        )

    response = exc.response or {}
    errors = response.get("errors") or []
    if isinstance(errors, dict):
        errors = [errors]
    messages = [
        str(item.get("message", ""))
        for item in errors
        if isinstance(item, dict)
    ]
    codes = [
        str((item.get("extensions") or {}).get("code", "")).upper()
        for item in errors
        if isinstance(item, dict)
    ]
    combined = " ".join(messages).lower()
    if (
        exc.status_code == status.HTTP_403_FORBIDDEN
        or "ACCESS_DENIED" in codes
        or any(marker in combined for marker in ("access denied", "not authorized", "requires the", "permission"))
    ):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Mia does not have permission to access this Shopify data. Review the app's granted permissions.",
        )

    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=fallback_detail,
    )
