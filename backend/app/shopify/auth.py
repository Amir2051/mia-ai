from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import bcrypt
import hashlib
import hmac
import secrets

import httpx
from jose import JWTError, jwt

from app.shopify.config import settings


pwd_context = None


class AuthenticationError(Exception):
    pass


class AuthorizationError(Exception):
    pass


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    now = datetime.now(timezone.utc)
    to_encode = data.copy()
    expire = now + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode.update({"exp": expire, "iat": now})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise AuthenticationError("Invalid or expired token") from exc


def hash_api_secret(secret: str) -> str:
    return bcrypt.hashpw(secret.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_api_secret(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def generate_nonce() -> str:
    return secrets.token_urlsafe(32)


def generate_session_token() -> str:
    return secrets.token_urlsafe(48)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _validate_shop_domain(shop: str) -> str:
    shop = (shop or "").strip().lower()
    if not shop:
        raise AuthorizationError("Shop domain is required")
    if shop.startswith("https://"):
        shop = shop[8:]
    elif shop.startswith("http://"):
        shop = shop[7:]
    shop = shop.rstrip("/")
    if not shop.endswith(".myshopify.com") or "/" in shop:
        raise AuthorizationError("Invalid Shopify shop domain")
    return shop


class ShopifySession:
    def __init__(self, shop_domain: str, access_token: str, scope: Optional[str] = None):
        self.shop_domain = _validate_shop_domain(shop_domain)
        self.access_token = access_token
        self.scope = scope


class ShopifyOAuthFlow:
    def build_authorization_url(self, shop: str, state: str, nonce: Optional[str] = None) -> str:
        shop = _validate_shop_domain(shop)
        if not settings.shopify_api_key:
            raise AuthorizationError("Shopify API key is not configured")
        redirect_uri = settings.shopify_redirect_uris.split(",")[0].strip()
        params = {
            "client_id": settings.shopify_api_key,
            "scope": settings.shopify_scopes,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        if nonce:
            params["nonce"] = nonce
        return f"https://{shop}/admin/oauth/authorize?{urlencode(params)}"

    def exchange_code_for_token(self, shop: str, code: str) -> str:
        shop = _validate_shop_domain(shop)
        if not settings.shopify_api_key:
            raise AuthorizationError("Shopify API key is not configured")
        if not settings.shopify_api_secret:
            raise AuthorizationError("Shopify API secret is not configured")
        code = (code or "").strip()
        if not code:
            raise AuthorizationError("Shopify authorization code is required")

        try:
            with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
                response = client.post(
                    f"https://{shop}/admin/oauth/access_token",
                    data={"client_id": settings.shopify_api_key, "client_secret": settings.shopify_api_secret, "code": code},
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise AuthenticationError("Unable to contact Shopify token endpoint") from exc

        if response.status_code >= 400:
            try:
                error_data = response.json()
            except ValueError:
                error_data = {}
            message = error_data.get("error_description") or error_data.get("error") or f"Shopify returned HTTP {response.status_code}"
            raise AuthenticationError(f"Shopify OAuth token exchange failed: {message}")

        try:
            data = response.json()
        except ValueError as exc:
            raise AuthenticationError("Shopify returned an invalid token response") from exc
        access_token = data.get("access_token")
        if not access_token:
            raise AuthenticationError("Shopify token response did not contain an access token")
        return str(access_token)

    def exchange_id_token_for_access_token(self, shop: str, id_token: str) -> dict:
        shop = _validate_shop_domain(shop)
        if not settings.shopify_api_key:
            raise AuthorizationError("Shopify API key is not configured")
        if not settings.shopify_api_secret:
            raise AuthorizationError("Shopify API secret is not configured")
        id_token = (id_token or "").strip()
        if not id_token:
            raise AuthorizationError("Shopify ID token is required")

        payload = {
            "client_id": settings.shopify_api_key,
            "client_secret": settings.shopify_api_secret,
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": id_token,
            "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
            "requested_token_type": "urn:shopify:params:oauth:token-type:offline-access-token",
            # Public apps must use expiring offline Admin API tokens.
            "expiring": "1",
        }
        return self._post_token_exchange(shop, payload)

    def refresh_access_token(self, shop: str, refresh_token: str) -> dict:
        shop = _validate_shop_domain(shop)
        if not settings.shopify_api_key or not settings.shopify_api_secret:
            raise AuthorizationError("Shopify app credentials are not configured")
        refresh_token = (refresh_token or "").strip()
        if not refresh_token:
            raise AuthorizationError("Shopify refresh token is required")

        payload = {
            "client_id": settings.shopify_api_key,
            "client_secret": settings.shopify_api_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        return self._post_token_exchange(shop, payload)

    def _post_token_exchange(self, shop: str, payload: dict) -> dict:
        try:
            with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
                response = client.post(
                    f"https://{shop}/admin/oauth/access_token",
                    data=payload,
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise AuthenticationError("Unable to contact Shopify token endpoint") from exc

        try:
            data = response.json()
        except ValueError:
            data = {}

        if response.status_code >= 400:
            message = data.get("error_description") or data.get("error") or f"Shopify returned HTTP {response.status_code}"
            raise AuthenticationError(f"Shopify token exchange failed: {message}")

        access_token = data.get("access_token")
        if not access_token:
            raise AuthenticationError("Shopify token response did not contain an access token")

        return {
            "access_token": str(access_token),
            "scope": data.get("scope"),
            "expires_in": data.get("expires_in"),
            "refresh_token": data.get("refresh_token"),
            "refresh_token_expires_in": data.get("refresh_token_expires_in"),
        }

    def validate_webhook(self, topic: str, shop: str, signature: str, body: bytes) -> bool:
        if not settings.shopify_api_secret:
            return False
        expected = hmac.new(settings.shopify_api_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
