"""OAuth helpers for social providers other than Meta.

All provider credentials stay server-side. Tokens are persisted only encrypted by
calling code. Provider APIs and scopes may require app review/approval.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
import base64
import ipaddress
import socket

import httpx
from fastapi import HTTPException

from app.shopify.config import settings

PROVIDERS = {
    "tiktok": {
        "label": "TikTok", "key_setting": "tiktok_client_key", "secret_setting": "tiktok_client_secret",
        "redirect_setting": "tiktok_redirect_uri", "callback": "/api/social/tiktok/callback",
    },
    "pinterest": {
        "label": "Pinterest", "key_setting": "pinterest_client_id", "secret_setting": "pinterest_client_secret",
        "redirect_setting": "pinterest_redirect_uri", "callback": "/api/social/pinterest/callback",
    },
    "youtube": {
        "label": "YouTube", "key_setting": "youtube_client_id", "secret_setting": "youtube_client_secret",
        "redirect_setting": "youtube_redirect_uri", "callback": "/api/social/youtube/callback",
    },
    "linkedin": {
        "label": "LinkedIn", "key_setting": "linkedin_client_id", "secret_setting": "linkedin_client_secret",
        "redirect_setting": "linkedin_redirect_uri", "callback": "/api/social/linkedin/callback",
    },
}

SCOPES = {
    "tiktok": "user.info.basic,video.publish,video.upload",
    "pinterest": "user_accounts:read,boards:read,pins:read,pins:write",
    "youtube": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
    "linkedin": "openid profile w_member_social",
}


def _setting(name: str) -> str:
    return str(getattr(settings, name, "") or "").strip()


def redirect_uri(provider: str) -> str:
    cfg = PROVIDERS[provider]
    return _setting(cfg["redirect_setting"]) or f"{settings.shopify_app_url.rstrip('/')}{cfg['callback']}"


def credentials(provider: str) -> tuple[str, str]:
    cfg = PROVIDERS[provider]
    client_id, secret = _setting(cfg["key_setting"]), _setting(cfg["secret_setting"])
    if not client_id or not secret:
        raise HTTPException(status_code=503, detail=f"{cfg['label']} developer app credentials are not configured")
    return client_id, secret


def authorization_url(provider: str, state: str) -> str:
    client_id, _ = credentials(provider)
    common = {"client_id": client_id, "redirect_uri": redirect_uri(provider), "response_type": "code", "state": state}
    if provider == "tiktok":
        common.update({"client_key": client_id, "scope": SCOPES[provider]})
        common.pop("client_id", None)
        return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(common)
    if provider == "pinterest":
        common.update({"scope": SCOPES[provider], "response_type": "code"})
        return "https://www.pinterest.com/oauth/?" + urlencode(common)
    if provider == "youtube":
        common.update({"scope": SCOPES[provider], "access_type": "offline", "include_granted_scopes": "true", "prompt": "consent"})
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(common)
    common.update({"scope": SCOPES[provider]})
    return "https://www.linkedin.com/oauth/v2/authorization?" + urlencode(common)


async def _request_token(provider: str, code: str) -> dict[str, Any]:
    client_id, secret = credentials(provider)
    redirect = redirect_uri(provider)
    async with httpx.AsyncClient(timeout=30) as client:
        if provider == "tiktok":
            response = await client.post("https://open.tiktokapis.com/v2/oauth/token/", data={
                "client_key": client_id, "client_secret": secret, "code": code,
                "grant_type": "authorization_code", "redirect_uri": redirect,
            })
        elif provider == "pinterest":
            basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
            response = await client.post("https://api.pinterest.com/v5/oauth/token", headers={
                "Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded",
            }, data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect})
        elif provider == "youtube":
            response = await client.post("https://oauth2.googleapis.com/token", data={
                "client_id": client_id, "client_secret": secret, "code": code,
                "grant_type": "authorization_code", "redirect_uri": redirect,
            })
        else:
            response = await client.post("https://www.linkedin.com/oauth/v2/accessToken", data={
                "client_id": client_id, "client_secret": secret, "code": code,
                "grant_type": "authorization_code", "redirect_uri": redirect,
            }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400 or not data.get("access_token"):
        detail = data.get("error_description") or data.get("error") or data.get("message") or f"{PROVIDERS[provider]['label']} token exchange failed (HTTP {response.status_code})"
        raise HTTPException(status_code=502, detail=str(detail)[:500])
    return data


async def exchange_and_profile(provider: str, code: str) -> tuple[dict[str, Any], dict[str, Any]]:
    token = await _request_token(provider, code)
    access = token["access_token"]
    async with httpx.AsyncClient(timeout=30) as client:
        if provider == "tiktok":
            response = await client.get("https://open.tiktokapis.com/v2/user/info/", params={"fields": "open_id,display_name,avatar_url"}, headers={"Authorization": f"Bearer {access}"})
            fallback = {"id": token.get("open_id", ""), "name": "TikTok account"}
        elif provider == "pinterest":
            response = await client.get("https://api.pinterest.com/v5/user_account", headers={"Authorization": f"Bearer {access}"})
            fallback = {"id": "", "name": "Pinterest account"}
        elif provider == "youtube":
            response = await client.get("https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"}, headers={"Authorization": f"Bearer {access}"})
            fallback = {"id": "", "name": "YouTube channel"}
        else:
            response = await client.get("https://api.linkedin.com/v2/userinfo", headers={"Authorization": f"Bearer {access}"})
            fallback = {"id": "", "name": "LinkedIn member"}
    try:
        profile = response.json()
    except ValueError:
        profile = {}
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"{PROVIDERS[provider]['label']} profile lookup failed (HTTP {response.status_code}): {str(profile.get('message') or profile.get('error_description') or '')[:250]}")
    if provider == "tiktok":
        profile = profile.get("data", {}).get("user", {})
        external_id = str(profile.get("open_id") or token.get("open_id") or "")
        name = str(profile.get("display_name") or "TikTok account")
    elif provider == "pinterest":
        external_id = str(profile.get("username") or profile.get("id") or "")
        name = str(profile.get("username") or "Pinterest account")
    elif provider == "youtube":
        items = profile.get("items") or []
        channel = items[0] if items else {}
        external_id = str(channel.get("id") or "")
        name = str(channel.get("snippet", {}).get("title") or "YouTube channel")
        profile = channel or fallback
    else:
        external_id = str(profile.get("sub") or profile.get("id") or "")
        name = str(profile.get("name") or profile.get("given_name") or "LinkedIn member")
    if not external_id:
        raise HTTPException(status_code=502, detail=f"Could not identify the connected {PROVIDERS[provider]['label']} account")
    expires_in = int(token.get("expires_in") or 0)
    token["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat() if expires_in else None
    return token, {"id": external_id, "name": name, "profile": profile}


async def refresh_access_token(provider: str, refresh_token: str) -> dict[str, Any]:
    client_id, secret = credentials(provider)
    async with httpx.AsyncClient(timeout=30) as client:
        if provider == "tiktok":
            response = await client.post("https://open.tiktokapis.com/v2/oauth/token/", data={
                "client_key": client_id, "client_secret": secret, "grant_type": "refresh_token", "refresh_token": refresh_token,
            })
        elif provider == "pinterest":
            basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
            response = await client.post("https://api.pinterest.com/v5/oauth/token", headers={
                "Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded",
            }, data={"grant_type": "refresh_token", "refresh_token": refresh_token})
        elif provider == "youtube":
            response = await client.post("https://oauth2.googleapis.com/token", data={
                "client_id": client_id, "client_secret": secret, "grant_type": "refresh_token", "refresh_token": refresh_token,
            })
        else:
            raise HTTPException(status_code=401, detail="LinkedIn token expired; disconnect and reconnect your LinkedIn account")
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400 or not data.get("access_token"):
        raise HTTPException(status_code=401, detail=f"{PROVIDERS[provider]['label']} token refresh failed; reconnect this account")
    expires_in = int(data.get("expires_in") or 0)
    data["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat() if expires_in else None
    return data


async def publish_text_linkedin(access_token: str, author_urn: str, text: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post("https://api.linkedin.com/rest/posts", headers={
            "Authorization": f"Bearer {access_token}", "X-Restli-Protocol-Version": "2.0.0",
            "Linkedin-Version": _setting("linkedin_api_version") or "202607", "Content-Type": "application/json",
        }, json={"author": author_urn, "commentary": text, "visibility": "PUBLIC", "distribution": {
            "feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False})
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"LinkedIn publish failed (HTTP {response.status_code}): {response.text[:300]}")
    return {"id": response.headers.get("x-restli-id", ""), "status": "published"}


async def pinterest_boards(access_token: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get("https://api.pinterest.com/v5/boards", headers={"Authorization": f"Bearer {access_token}"}, params={"page_size": 100})
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Pinterest boards lookup failed (HTTP {response.status_code})")
    return response.json().get("items", [])


async def pinterest_create_pin(access_token: str, board_id: str, title: str, description: str, image_url: str, link: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"board_id": board_id, "title": title[:100], "description": description[:800], "media_source": {"source_type": "image_url", "url": image_url}}
    if link:
        body["link"] = link
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post("https://api.pinterest.com/v5/pins", headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}, json=body)
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Pinterest pin creation failed (HTTP {response.status_code}): {response.text[:300]}")
    return response.json()


async def youtube_upload(access_token: str, video_url: str, title: str, description: str, privacy: str = "private") -> dict[str, Any]:
    """Download a public HTTPS video with a hard size cap, then upload via resumable-capable Google endpoint."""
    from urllib.parse import urlparse
    parsed = urlparse(video_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(status_code=422, detail="YouTube video URL must be public HTTPS without embedded credentials")
    try:
        addresses = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not address.is_global for address in addresses):
            raise HTTPException(status_code=422, detail="Video URL host must resolve only to public IP addresses")
    except socket.gaierror as exc:
        raise HTTPException(status_code=422, detail="Video URL host could not be resolved") from exc
    max_bytes = 100 * 1024 * 1024
    async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
        async with client.stream("GET", video_url) as media:
            if media.status_code >= 400 or media.status_code >= 300:
                raise HTTPException(status_code=422, detail="Could not fetch the video URL (redirects are not followed)")
            content_type = media.headers.get("content-type", "video/mp4").split(";")[0]
            if not content_type.startswith("video/"):
                raise HTTPException(status_code=422, detail="The supplied URL did not return a video content type")
            length = int(media.headers.get("content-length") or 0)
            if length > max_bytes:
                raise HTTPException(status_code=413, detail="Video exceeds Mia's current 100 MB upload limit")
            chunks = bytearray()
            async for chunk in media.aiter_bytes():
                if len(chunks) + len(chunk) > max_bytes:
                    raise HTTPException(status_code=413, detail="Video exceeds Mia's current 100 MB upload limit")
                chunks.extend(chunk)
        metadata = {"snippet": {"title": title[:100], "description": description[:5000], "categoryId": "22"}, "status": {"privacyStatus": privacy}}
        boundary = "mia_upload_" + __import__("secrets").token_hex(16)
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
            + __import__("json").dumps(metadata).encode("utf-8")
            + f"\r\n--{boundary}\r\nContent-Type: {content_type}\r\n\r\n".encode()
            + bytes(chunks)
            + f"\r\n--{boundary}--\r\n".encode()
        )
        upload = await client.post("https://www.googleapis.com/upload/youtube/v3/videos", params={"uploadType": "multipart", "part": "snippet,status"}, headers={"Authorization": f"Bearer {access_token}", "Content-Type": f"multipart/related; boundary={boundary}"}, content=body)
    if upload.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"YouTube upload failed (HTTP {upload.status_code}): {upload.text[:300]}")
    data = upload.json()
    return {"id": data.get("id"), "url": f"https://www.youtube.com/watch?v={data.get('id')}" if data.get("id") else None, "status": data.get("status", {}).get("uploadStatus", "uploaded")}
