import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.dependencies import CurrentUser, get_optional_shop
from app.models.database import get_db
from app.models.schemas import Shop, SocialAccount
from app.security.rls import set_shop_context
from app.security.tokens import encrypt_token, decrypt_token
from app.services.meta import MetaAPIError, MetaClient, serialize_meta_metadata
from app.shopify.config import settings


router = APIRouter()


def _sign_state(shop_domain: str, nonce: str, issued_at: int) -> str:
    payload = f"{shop_domain}|{nonce}|{issued_at}"
    signature = hmac.new(
        settings.secret_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}|{signature}"


def _verify_state(state: str) -> str:
    parts = state.split("|")
    if len(parts) != 4:
        raise HTTPException(status_code=400, detail="Invalid Meta OAuth state")
    shop_domain, nonce, issued_at, signature = parts
    payload = f"{shop_domain}|{nonce}|{issued_at}"
    expected = hmac.new(settings.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=400, detail="Invalid Meta OAuth state")
    try:
        if abs(int(time.time()) - int(issued_at)) > 600:
            raise HTTPException(status_code=400, detail="Expired Meta OAuth state")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Meta OAuth state") from exc
    return shop_domain


class MetaPublishRequest(BaseModel):
    account_id: int
    message: str
    link: Optional[str] = None


class InstagramPublishRequest(BaseModel):
    account_id: int
    image_url: str
    caption: str = ""


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
    db=Depends(get_db),
):
    shop_domain = _verify_state(state)
    await set_shop_context(db, shop_domain)
    result = await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))
    shop = result.scalar_one_or_none()
    if not shop or not shop.is_active:
        raise HTTPException(status_code=401, detail="Shopify shop is not connected")

    try:
        token_data = await MetaClient().exchange_code(code)
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
                shop_id=shop.id,
                provider="meta",
                account_type="facebook_page",
                external_account_id=page_id,
                account_name=page_name,
                access_token_encrypted=encrypt_token(page_token),
                metadata_json=serialize_meta_metadata(metadata),
                is_active=True,
            )
            db.add(account)
        else:
            account.account_name = page_name
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
        return await MetaClient().instagram_media(str(instagram_id))
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
