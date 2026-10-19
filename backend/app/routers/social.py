import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select

from app.auth.dependencies import CurrentUser, get_optional_shop
from app.models.database import get_db
from app.models.schemas import Shop, SocialAccount
from app.security.rls import set_shop_context
from app.security.tokens import encrypt_token, decrypt_token
from app.services.meta import MetaAPIError, MetaClient, serialize_meta_metadata
from app.services import social_oauth
from app.shopify.config import settings


router = APIRouter()


def _sign_state(shop_domain: str, nonce: str, issued_at: int, provider: Optional[str] = None) -> str:
    payload = f"{shop_domain}|{nonce}|{issued_at}" + (f"|{provider}" if provider else "")
    signature = hmac.new(
        settings.secret_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}|{signature}"


def _verify_state(state: str, expected_provider: Optional[str] = None) -> str:
    parts = state.split("|")
    expected_parts = 5 if expected_provider else 4
    if len(parts) != expected_parts:
        raise HTTPException(status_code=400, detail="Invalid social OAuth state")
    shop_domain, nonce, issued_at = parts[:3]
    state_provider = parts[3] if expected_provider else None
    signature = parts[-1]
    if expected_provider and state_provider != expected_provider:
        raise HTTPException(status_code=400, detail="OAuth state provider mismatch")
    payload = f"{shop_domain}|{nonce}|{issued_at}" + (f"|{state_provider}" if state_provider else "")
    expected = hmac.new(settings.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=400, detail="Invalid Meta OAuth state")
    try:
        if abs(int(time.time()) - int(issued_at)) > 600:
            raise HTTPException(status_code=400, detail="Expired Meta OAuth state")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Meta OAuth state") from exc
    return shop_domain


class OAuthCompleteRequest(BaseModel):
    code: str
    state: str


def _oauth_frontend_redirect(provider: str, code: str, state: str) -> RedirectResponse:
    # Do not exchange provider credentials until the callback is completed by an
    # authenticated Shopify session for the same shop that initiated OAuth.
    query = urlencode({"oauth_provider": provider, "oauth_code": code, "oauth_state": state})
    # Use the URL fragment so the authorization code/state are not sent in the
    # frontend document request or ordinary Referer headers.
    return RedirectResponse(
        url=f"{settings.shopify_app_url.rstrip('/')}/social#{query}",
        status_code=303,
    )


class MetaPublishRequest(BaseModel):
    account_id: int
    message: str
    link: Optional[str] = None


class InstagramPublishRequest(BaseModel):
    account_id: int
    image_url: str
    caption: str = ""

    @field_validator("image_url")
    @classmethod
    def require_public_https_image_url(cls, value: str) -> str:
        from urllib.parse import urlparse
        parsed = urlparse(value.strip())
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Instagram images must use a public HTTPS image URL")
        return value.strip()

    @field_validator("caption")
    @classmethod
    def limit_caption_length(cls, value: str) -> str:
        if len(value) > 2200:
            raise ValueError("Instagram captions must be 2,200 characters or fewer")
        return value


@router.get("/accounts")
async def list_social_accounts(
    current: Optional[CurrentUser] = Depends(get_optional_shop),
    db=Depends(get_db),
):
    if not current:
        return {"accounts": [], "connected": False}
    await set_shop_context(db, current.shop_domain)
    result = await db.execute(select(Shop).where(Shop.shop_domain == current.shop_domain))
    shop = result.scalar_one_or_none()
    if not shop:
        return {"accounts": [], "connected": False}
    await set_shop_context(db, current.shop_domain, shop.id)
    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.shop_id == shop.id,
            SocialAccount.is_active.is_(True),
        )
    )
    rows = result.scalars().all()
    return {
        "connected": True,
        "accounts": [
            {
                "id": row.id,
                "provider": row.provider,
                "account_type": row.account_type,
                "external_account_id": row.external_account_id,
                "account_name": row.account_name,
                "metadata": json.loads(row.metadata_json or "{}"),
                "token_expires_at": row.token_expires_at.isoformat() if row.token_expires_at else None,
            }
            for row in rows
        ],
    }


@router.delete("/accounts/{account_id}")
async def disconnect_social_account(
    account_id: int,
    current: Optional[CurrentUser] = Depends(get_optional_shop),
    db=Depends(get_db),
):
    """Disconnect a social page from this shop without affecting other shops."""
    if not current:
        raise HTTPException(status_code=401, detail="Missing Shopify session")
    await set_shop_context(db, current.shop_domain)
    result = await db.execute(select(Shop).where(Shop.shop_domain == current.shop_domain))
    shop = result.scalar_one_or_none()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    await set_shop_context(db, current.shop_domain, shop.id)
    result = await db.execute(select(SocialAccount).where(
        SocialAccount.id == account_id,
        SocialAccount.shop_id == shop.id,
        SocialAccount.is_active.is_(True),
    ))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Connected social account not found")
    account.is_active = False
    account.access_token_encrypted = ""
    await db.flush()
    return {"status": "disconnected", "account_id": account_id}


@router.get("/meta/connect")
async def meta_connect(current: Optional[CurrentUser] = Depends(get_optional_shop)):
    if not current:
        raise HTTPException(status_code=401, detail="Missing Shopify session")
    if not settings.secret_key:
        raise HTTPException(status_code=500, detail="SECRET_KEY is not configured")
    if not settings.meta_app_id or not settings.meta_app_secret:
        raise HTTPException(status_code=503, detail="Meta app credentials are not configured")
    state = _sign_state(current.shop_domain, secrets.token_urlsafe(18), int(time.time()))
    return {"authorization_url": MetaClient().authorization_url(state)}


@router.get("/meta/callback")
async def meta_callback(
    code: str = Query(...),
    state: str = Query(...),
):
    # The provider callback is not an authenticated Shopify request. Validate
    # the signed state, then defer token exchange until the merchant's app
    # session proves it belongs to the shop embedded in that state.
    _verify_state(state)
    return _oauth_frontend_redirect("meta", code, state)


@router.post("/meta/complete")
async def meta_complete(
    payload: OAuthCompleteRequest,
    current: Optional[CurrentUser] = Depends(get_optional_shop),
    db=Depends(get_db),
):
    if not current:
        raise HTTPException(status_code=401, detail="Missing Shopify session; reopen Mia from Shopify and retry the connection")
    shop_domain = _verify_state(payload.state)
    if current.shop_domain != shop_domain:
        raise HTTPException(status_code=403, detail="OAuth session does not match the Shopify shop that initiated the connection")
    await set_shop_context(db, shop_domain)
    result = await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))
    shop = result.scalar_one_or_none()
    if not shop or not shop.is_active:
        raise HTTPException(status_code=401, detail="Shopify shop is not connected")

    try:
        token_data = await MetaClient().exchange_code(payload.code)
        user_token = token_data.get("access_token")
        if not user_token:
            raise MetaAPIError("Meta OAuth returned no access token", 502)
        profile = await MetaClient(user_token).me()
        pages = await MetaClient(user_token).pages()
    except MetaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    await set_shop_context(db, shop_domain, shop.id)
    saved = 0
    for page in pages.get("data") or []:
        page_id = str(page.get("id") or "")
        page_name = str(page.get("name") or page_id)
        if not page_id:
            continue
        page_token = str(page.get("access_token") or user_token)
        metadata = dict(page)
        metadata.pop("access_token", None)
        metadata["meta_user_id"] = profile.get("id")
        metadata["meta_user_name"] = profile.get("name")
        existing = await db.execute(
            select(SocialAccount).where(
                SocialAccount.shop_id == shop.id,
                SocialAccount.provider == "meta",
                SocialAccount.external_account_id == page_id,
            )
        )
        account = existing.scalar_one_or_none()
        if account is None:
            account = SocialAccount(
                shop_id=shop.id, provider="meta", account_type="facebook_page",
                external_account_id=page_id, account_name=page_name,
                access_token_encrypted=encrypt_token(page_token),
                metadata_json=serialize_meta_metadata(metadata), is_active=True,
            )
            db.add(account)
        else:
            account.account_name = page_name
            account.access_token_encrypted = encrypt_token(page_token)
            account.metadata_json = serialize_meta_metadata(metadata)
            account.is_active = True
        saved += 1
    await db.commit()
    return {"status": "connected", "provider": "meta", "pages_saved": saved}


@router.post("/meta/test-connect")
async def meta_test_connect(
    current: Optional[CurrentUser] = Depends(get_optional_shop),
    db=Depends(get_db),
):
    if not current:
        raise HTTPException(status_code=401, detail="Missing Shopify session")
    if not settings.meta_test_access_token:
        raise HTTPException(status_code=503, detail="META_TEST_ACCESS_TOKEN is not configured")
    await set_shop_context(db, current.shop_domain)
    result = await db.execute(select(Shop).where(Shop.shop_domain == current.shop_domain))
    shop = result.scalar_one_or_none()
    if not shop or not shop.is_active:
        raise HTTPException(status_code=401, detail="Shopify shop is not connected")

    try:
        client = MetaClient(settings.meta_test_access_token)
        profile = await client.me()
        pages = await client.pages()
    except MetaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    await set_shop_context(db, current.shop_domain, shop.id)
    saved = 0
    for page in pages.get("data") or []:
        page_id = str(page.get("id") or "")
        if not page_id:
            continue
        page_token = str(page.get("access_token") or settings.meta_test_access_token)
        metadata = dict(page)
        metadata.pop("access_token", None)
        metadata["meta_user_id"] = profile.get("id")
        metadata["meta_user_name"] = profile.get("name")
        existing = await db.execute(select(SocialAccount).where(
            SocialAccount.shop_id == shop.id,
            SocialAccount.provider == "meta",
            SocialAccount.external_account_id == page_id,
        ))
        account = existing.scalar_one_or_none()
        if account is None:
            db.add(SocialAccount(
                shop_id=shop.id,
                provider="meta",
                account_type="facebook_page",
                external_account_id=page_id,
                account_name=str(page.get("name") or page_id),
                access_token_encrypted=encrypt_token(page_token),
                metadata_json=serialize_meta_metadata(metadata),
                is_active=True,
            ))
        else:
            account.account_name = str(page.get("name") or page_id)
            account.access_token_encrypted = encrypt_token(page_token)
            account.metadata_json = serialize_meta_metadata(metadata)
            account.is_active = True
        saved += 1
    await db.commit()
    return {"status": "connected", "provider": "meta", "profile": profile, "pages_saved": saved}


@router.post("/meta/facebook/publish")
async def publish_facebook(
    payload: MetaPublishRequest,
    current: Optional[CurrentUser] = Depends(get_optional_shop),
    db=Depends(get_db),
):
    account, token = await _get_meta_account(payload.account_id, current, db, "facebook_page")
    try:
        result = await MetaClient().publish_page_post(
            account.external_account_id,
            token,
            payload.message,
            payload.link,
        )
    except MetaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"status": "published", "result": result}


@router.post("/meta/instagram/publish")
async def publish_instagram(
    payload: InstagramPublishRequest,
    current: Optional[CurrentUser] = Depends(get_optional_shop),
    db=Depends(get_db),
):
    account, token = await _get_meta_account(payload.account_id, current, db, "facebook_page")
    metadata = json.loads(account.metadata_json or "{}")
    instagram = metadata.get("instagram_business_account") or {}
    instagram_id = instagram.get("id") if isinstance(instagram, dict) else instagram
    if not instagram_id:
        raise HTTPException(status_code=400, detail="No Instagram professional account is linked to this Facebook Page")
    try:
        container = await MetaClient().create_instagram_media(str(instagram_id), payload.image_url, payload.caption, token)
        creation_id = container.get("id")
        if not creation_id:
            raise MetaAPIError("Instagram media container was not created", 502)
        result = await MetaClient().publish_instagram_media(str(instagram_id), creation_id, token)
    except MetaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"status": "published", "result": result}


@router.get("/meta/facebook/{account_id}/monitor")
async def monitor_facebook(
    account_id: int,
    current: Optional[CurrentUser] = Depends(get_optional_shop),
    db=Depends(get_db),
):
    account, token = await _get_meta_account(account_id, current, db, "facebook_page")
    try:
        return await MetaClient().page_feed(account.external_account_id, token)
    except MetaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/meta/instagram/{account_id}/monitor")
async def monitor_instagram(
    account_id: int,
    current: Optional[CurrentUser] = Depends(get_optional_shop),
    db=Depends(get_db),
):
    account, token = await _get_meta_account(account_id, current, db, "facebook_page")
    metadata = json.loads(account.metadata_json or "{}")
    instagram = metadata.get("instagram_business_account") or {}
    instagram_id = instagram.get("id") if isinstance(instagram, dict) else instagram
    if not instagram_id:
        raise HTTPException(status_code=400, detail="No Instagram professional account is linked to this Facebook Page")
    try:
        return await MetaClient().instagram_media(str(instagram_id), token)
    except MetaAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


async def _get_meta_account(
    account_id: int,
    current: Optional[CurrentUser],
    db,
    account_type: str,
):
    if not current:
        raise HTTPException(status_code=401, detail="Missing Shopify session")
    await set_shop_context(db, current.shop_domain)
    result = await db.execute(select(Shop).where(Shop.shop_domain == current.shop_domain))
    shop = result.scalar_one_or_none()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    await set_shop_context(db, current.shop_domain, shop.id)
    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.id == account_id,
            SocialAccount.shop_id == shop.id,
            SocialAccount.provider == "meta",
            SocialAccount.account_type == account_type,
            SocialAccount.is_active.is_(True),
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Meta account not found")
    return account, decrypt_token(account.access_token_encrypted)


# OAuth + publishing for TikTok, Pinterest, YouTube, and LinkedIn.
@router.get("/connect/{provider}")
async def connect_social_provider(provider: str, current: Optional[CurrentUser] = Depends(get_optional_shop)):
    if provider not in social_oauth.PROVIDERS:
        raise HTTPException(status_code=404, detail="Unsupported social provider")
    if not current:
        raise HTTPException(status_code=401, detail="Missing Shopify session")
    if not settings.secret_key:
        raise HTTPException(status_code=500, detail="SECRET_KEY is not configured")
    state = _sign_state(current.shop_domain, secrets.token_urlsafe(24), int(time.time()), provider)
    return {"authorization_url": social_oauth.authorization_url(provider, state)}


@router.get("/{provider}/callback")
async def social_provider_callback(
    provider: str,
    code: str = Query(...),
    state: str = Query(...),
):
    if provider not in social_oauth.PROVIDERS:
        raise HTTPException(status_code=404, detail="Unsupported social provider")
    _verify_state(state, provider)
    return _oauth_frontend_redirect(provider, code, state)


@router.post("/{provider}/complete")
async def social_provider_complete(
    provider: str,
    payload: OAuthCompleteRequest,
    current: Optional[CurrentUser] = Depends(get_optional_shop),
    db=Depends(get_db),
):
    if provider not in social_oauth.PROVIDERS:
        raise HTTPException(status_code=404, detail="Unsupported social provider")
    if not current:
        raise HTTPException(status_code=401, detail="Missing Shopify session; reopen Mia from Shopify and retry the connection")
    shop_domain = _verify_state(payload.state, provider)
    if current.shop_domain != shop_domain:
        raise HTTPException(status_code=403, detail="OAuth session does not match the Shopify shop that initiated the connection")
    await set_shop_context(db, shop_domain)
    result = await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))
    shop = result.scalar_one_or_none()
    if not shop or not shop.is_active:
        raise HTTPException(status_code=401, detail="Shopify shop is not connected")
    token_data, identity = await social_oauth.exchange_and_profile(provider, payload.code)
    access_token = str(token_data.get("access_token") or "")
    if not access_token:
        raise HTTPException(status_code=502, detail="Provider returned no access token")
    refresh_token = str(token_data.get("refresh_token") or "")
    profile = identity.get("profile") or {}
    metadata = {
        "profile": profile,
        "provider_user_id": identity["id"],
        "provider": provider,
        "scopes": token_data.get("scope") or "",
    }
    if refresh_token:
        metadata["refresh_token_encrypted"] = encrypt_token(refresh_token)
    await set_shop_context(db, shop_domain, shop.id)
    existing = await db.execute(select(SocialAccount).where(
        SocialAccount.shop_id == shop.id,
        SocialAccount.provider == provider,
        SocialAccount.external_account_id == identity["id"],
    ))
    account = existing.scalar_one_or_none()
    expires_at = None
    if token_data.get("expires_at"):
        from datetime import datetime
        expires_at = datetime.fromisoformat(token_data["expires_at"].replace("Z", "+00:00")).replace(tzinfo=None)
    elif token_data.get("expires_in"):
        from datetime import datetime, timedelta
        expires_at = datetime.utcnow() + timedelta(seconds=int(token_data["expires_in"]))
    if account is None:
        account = SocialAccount(
            shop_id=shop.id, provider=provider, account_type=provider,
            external_account_id=identity["id"], account_name=identity["name"],
            access_token_encrypted=encrypt_token(access_token), token_expires_at=expires_at,
            metadata_json=json.dumps(metadata, separators=(",", ":")), is_active=True,
        )
        db.add(account)
    else:
        account.account_name = identity["name"]
        account.access_token_encrypted = encrypt_token(access_token)
        account.token_expires_at = expires_at
        account.metadata_json = json.dumps(metadata, separators=(",", ":"))
        account.is_active = True
    await db.commit()
    return {"status": "connected", "provider": provider, "account_name": identity["name"]}


async def _get_provider_account(account_id: int, provider: str, current: Optional[CurrentUser], db):
    if not current:
        raise HTTPException(status_code=401, detail="Missing Shopify session")
    await set_shop_context(db, current.shop_domain)
    result = await db.execute(select(Shop).where(Shop.shop_domain == current.shop_domain))
    shop = result.scalar_one_or_none()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    await set_shop_context(db, current.shop_domain, shop.id)
    result = await db.execute(select(SocialAccount).where(
        SocialAccount.id == account_id, SocialAccount.shop_id == shop.id,
        SocialAccount.provider == provider, SocialAccount.is_active.is_(True),
    ))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail=f"Connected {provider} account not found")
    from datetime import datetime, timedelta
    from datetime import timezone
    now = datetime.utcnow()
    expires = account.token_expires_at
    if expires and expires.replace(tzinfo=None) <= now + timedelta(minutes=5):
        metadata = json.loads(account.metadata_json or "{}")
        encrypted_refresh = metadata.get("refresh_token_encrypted")
        if not encrypted_refresh:
            raise HTTPException(status_code=401, detail=f"{provider.title()} token expired; disconnect and reconnect this account")
        refreshed = await social_oauth.refresh_access_token(provider, decrypt_token(encrypted_refresh))
        account.access_token_encrypted = encrypt_token(refreshed["access_token"])
        if refreshed.get("refresh_token"):
            metadata["refresh_token_encrypted"] = encrypt_token(refreshed["refresh_token"])
        account.metadata_json = json.dumps(metadata, separators=(",", ":"))
        if refreshed.get("expires_at"):
            account.token_expires_at = datetime.fromisoformat(refreshed["expires_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        elif refreshed.get("expires_in"):
            account.token_expires_at = now + timedelta(seconds=int(refreshed["expires_in"]))
        await db.commit()
    return account, decrypt_token(account.access_token_encrypted)


class TikTokPublishRequest(BaseModel):
    account_id: int
    media_url: str
    caption: str = ""
    media_type: str = "video"
    privacy_level: str = "SELF_ONLY"

    @field_validator("media_url")
    @classmethod
    def validate_media_url(cls, value: str) -> str:
        from urllib.parse import urlparse
        parsed = urlparse(value.strip())
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("TikTok media URL must be public HTTPS without embedded credentials")
        return value.strip()

    @field_validator("media_type")
    @classmethod
    def validate_media_type(cls, value: str) -> str:
        if value not in {"video", "photo"}:
            raise ValueError("media_type must be video or photo")
        return value


class PinterestPublishRequest(BaseModel):
    account_id: int
    board_id: str
    image_url: str
    title: str = ""
    description: str = ""
    link: Optional[str] = None

    @field_validator("image_url")
    @classmethod
    def validate_image_url(cls, value: str) -> str:
        from urllib.parse import urlparse
        parsed = urlparse(value.strip())
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Pinterest image URL must be public HTTPS without embedded credentials")
        return value.strip()


class YouTubePublishRequest(BaseModel):
    account_id: int
    video_url: str
    title: str
    description: str = ""
    privacy_status: str = "private"

    @field_validator("privacy_status")
    @classmethod
    def validate_privacy(cls, value: str) -> str:
        if value not in {"private", "unlisted", "public"}:
            raise ValueError("privacy_status must be private, unlisted, or public")
        return value


class LinkedInPublishRequest(BaseModel):
    account_id: int
    text: str

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip() or len(value) > 3000:
            raise ValueError("LinkedIn post must contain 1–3,000 characters")
        return value.strip()


@router.get("/pinterest/{account_id}/boards")
async def get_pinterest_boards(account_id: int, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    account, token = await _get_provider_account(account_id, "pinterest", current, db)
    return {"boards": await social_oauth.pinterest_boards(token)}


@router.post("/tiktok/publish")
async def publish_tiktok(payload: TikTokPublishRequest, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    account, token = await _get_provider_account(payload.account_id, "tiktok", current, db)
    import httpx
    async with httpx.AsyncClient(timeout=30) as client:
        creator_response = await client.post("https://open.tiktokapis.com/v2/post/publish/creator_info/query/", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json={})
    try:
        creator_data = creator_response.json()
    except ValueError:
        creator_data = {}
    if creator_response.status_code >= 400 or (creator_data.get("error") or {}).get("code") not in (None, "ok"):
        raise HTTPException(status_code=502, detail="TikTok could not confirm creator publishing settings; verify Content Posting API access and scopes")
    allowed_privacy = (creator_data.get("data") or {}).get("privacy_level_options") or []
    if payload.privacy_level not in allowed_privacy:
        raise HTTPException(status_code=422, detail="Selected TikTok privacy setting is not allowed for this account. Available options: " + ", ".join(allowed_privacy))
    metadata = {"post_info": {"title": payload.caption[:90], "description": payload.caption[:4000], "privacy_level": payload.privacy_level}, "source_info": {"source": "PULL_FROM_URL"}}
    if payload.media_type == "photo":
        metadata.update({"media_type": "PHOTO", "post_mode": "DIRECT_POST"})
        metadata["post_info"]["photo_cover_index"] = 0
        metadata["source_info"].update({"photo_images": [payload.media_url]})
        endpoint = "https://open.tiktokapis.com/v2/post/publish/content/init/"
    else:
        metadata["source_info"]["video_url"] = payload.media_url
        endpoint = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(endpoint, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}, json=metadata)
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400 or (data.get("error") or {}).get("code") not in (None, "ok"):
        raise HTTPException(status_code=502, detail=f"TikTok publish failed: {str((data.get('error') or {}).get('message') or response.text)[:400]}")
    return {"status": "processing", "publish_id": (data.get("data") or {}).get("publish_id"), "result": data.get("data") or {}}


@router.post("/pinterest/publish")
async def publish_pinterest(payload: PinterestPublishRequest, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    _, token = await _get_provider_account(payload.account_id, "pinterest", current, db)
    result = await social_oauth.pinterest_create_pin(token, payload.board_id, payload.title, payload.description, payload.image_url, payload.link)
    return {"status": "published", "result": result}


@router.post("/youtube/publish")
async def publish_youtube(payload: YouTubePublishRequest, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    _, token = await _get_provider_account(payload.account_id, "youtube", current, db)
    result = await social_oauth.youtube_upload(token, payload.video_url, payload.title, payload.description, payload.privacy_status)
    return {"status": "uploaded", "result": result}


@router.post("/linkedin/publish")
async def publish_linkedin(payload: LinkedInPublishRequest, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    account, token = await _get_provider_account(payload.account_id, "linkedin", current, db)
    result = await social_oauth.publish_text_linkedin(token, f"urn:li:person:{account.external_account_id}", payload.text)
    return {"status": "published", "result": result}
