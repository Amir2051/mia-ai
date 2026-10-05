import json
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx

from app.security.tokens import encrypt_token, decrypt_token
from app.shopify.config import settings


class MetaAPIError(Exception):
    def __init__(self, message: str, status_code: int = 502, response: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response or {}


class MetaClient:
    def __init__(self, access_token: Optional[str] = None):
        self.access_token = access_token
        version = (settings.meta_graph_api_version or "").strip().strip("/")
        base = settings.meta_graph_api_base_url.rstrip("/")
        self.base_url = f"{base}/{version}" if version else base

    def authorization_url(self, state: str) -> str:
        if not settings.meta_app_id:
            raise MetaAPIError("META_APP_ID is not configured", 500)
        params = {
            "client_id": settings.meta_app_id,
            "redirect_uri": settings.meta_redirect_uri or f"{settings.shopify_app_url.rstrip('/')}/api/social/meta/callback",
            "state": state,
            "response_type": "code",
            "scope": settings.meta_oauth_scopes,
        }
        return f"https://www.facebook.com/{settings.meta_oauth_version}/dialog/oauth?{urlencode(params)}"

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params = dict(params or {})
        token = params.pop("access_token", None) or self.access_token
        if token:
            params["access_token"] = token
        if not token:
            raise MetaAPIError("Meta access token is not configured", 500)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.base_url}/{path.lstrip('/')}", params=params)
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text}
        if response.status_code >= 400 or data.get("error"):
            error = data.get("error") or {}
            message = error.get("message") or f"Meta API returned HTTP {response.status_code}"
            raise MetaAPIError(message, response.status_code, data)
        return data

    async def _post(self, path: str, data: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(data)
        token = payload.pop("access_token", None) or self.access_token
        if token:
            payload["access_token"] = token
        if not token:
            raise MetaAPIError("Meta access token is not configured", 500)
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{self.base_url}/{path.lstrip('/')}", data=payload)
        try:
            result = response.json()
        except ValueError:
            result = {"raw": response.text}
        if response.status_code >= 400 or result.get("error"):
            error = result.get("error") or {}
            message = error.get("message") or f"Meta API returned HTTP {response.status_code}"
            raise MetaAPIError(message, response.status_code, result)
        return result

    async def exchange_code(self, code: str) -> Dict[str, Any]:
        return await self._get(
            "/oauth/access_token",
            {
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "redirect_uri": settings.meta_redirect_uri or f"{settings.shopify_app_url.rstrip('/')}/api/social/meta/callback",
                "code": code,
            },
        )

    async def me(self) -> Dict[str, Any]:
        return await self._get("/me", {"fields": "id,name"})

    async def pages(self) -> Dict[str, Any]:
        return await self._get("/me/accounts", {
            "fields": "id,name,access_token,category,tasks,instagram_business_account"
        })

    async def page_feed(self, page_id: str, page_access_token: str) -> Dict[str, Any]:
        return await self._get(f"/{page_id}/feed", {
            "access_token": page_access_token,
            "fields": "id,message,created_time,permalink_url,attachments",
            "limit": 25,
        })

    async def publish_page_post(self, page_id: str, page_access_token: str, message: str, link: Optional[str] = None) -> Dict[str, Any]:
        data: Dict[str, Any] = {"message": message, "access_token": page_access_token}
        if link:
            data["link"] = link
        return await self._post(f"/{page_id}/feed", data)

    async def instagram_media(self, instagram_account_id: str) -> Dict[str, Any]:
        return await self._get(f"/{instagram_account_id}/media", {
            "fields": "id,caption,media_type,media_url,permalink,timestamp",
            "limit": 25,
        })

    async def create_instagram_media(self, instagram_account_id: str, image_url: str, caption: str, access_token: str) -> Dict[str, Any]:
        return await self._post(f"/{instagram_account_id}/media", {
            "image_url": image_url,
            "caption": caption,
            "access_token": access_token,
        })

    async def publish_instagram_media(self, instagram_account_id: str, creation_id: str, access_token: str) -> Dict[str, Any]:
        return await self._post(f"/{instagram_account_id}/media_publish", {
            "creation_id": creation_id,
            "access_token": access_token,
        })


def serialize_meta_metadata(value: Dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"))


def encrypt_meta_token(token: str) -> str:
    return encrypt_token(token)


def decrypt_meta_token(token: str) -> str:
    return decrypt_token(token)
