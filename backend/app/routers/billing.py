"""Shopify Billing endpoints (App Store compliant).

POST /api/billing/subscribe -> creates a subscription via Shopify's Billing API and
returns a confirmationUrl for merchant approval. GET /api/billing/status re-reads the
subscription LIVE from Shopify (authoritative; never trusts callback params).
External payment processors are intentionally not used.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.dependencies import CurrentUser, get_current_shop, get_optional_shop
from app.models.database import get_db
from app.models.schemas import Shop
from app.security.tokens import decrypt_token
from app.services import billing
from app.shopify.client import ShopifyAPIClient, ShopifyAPIError

router = APIRouter()


class SubscribeRequest(BaseModel):
    plan_id: str = billing.DEFAULT_PLAN


def _public_plans() -> list[dict]:
    return [
        {"id": p["id"], "name": p["name"], "amount": p["amount"], "currency": p["currency"],
         "interval": p["interval"], "trial_days": p["trial_days"], "features": p["features"]}
        for p in billing.PLANS.values()
    ]


async def _shop(current: CurrentUser, db) -> Shop:
    result = await db.execute(select(Shop).where(Shop.shop_domain == current.shop_domain))
    shop = result.scalar_one_or_none()
    if not shop or not shop.access_token_encrypted or not shop.is_active:
        raise HTTPException(status_code=401, detail="Shop is not connected")
    return shop


def _client(shop: Shop) -> ShopifyAPIClient:
    try:
        token = decrypt_token(shop.access_token_encrypted or "")
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail="Stored Shopify access token could not be decrypted") from exc
    return ShopifyAPIClient(shop_domain=shop.shop_domain, access_token=token)


@router.get("/status")
async def billing_status(current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    if not current:
        return {"connected": False, "enforced": billing.settings.billing_enforced, "plans": _public_plans(), "subscription": None, "entitled": False}
    result = await db.execute(select(Shop).where(Shop.shop_domain == current.shop_domain))
    shop = result.scalar_one_or_none()
    if not shop or not shop.access_token_encrypted or not shop.is_active:
        return {"connected": False, "enforced": billing.settings.billing_enforced, "plans": _public_plans(), "subscription": None, "entitled": False}
    client = _client(shop)
    try:
        subscription = await billing.get_active_subscription(client)
    except ShopifyAPIError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not read billing status from Shopify. Please retry.")
    entitled = (subscription is not None) or (not billing.settings.billing_enforced)
    return {
        "connected": True,
        "enforced": billing.settings.billing_enforced,
        "test_mode": bool(billing.settings.billing_test),
        "plans": _public_plans(),
        "subscription": subscription,
        "entitled": entitled,
    }


@router.post("/subscribe")
async def billing_subscribe(body: SubscribeRequest, current: CurrentUser = Depends(get_current_shop), db=Depends(get_db)):
    if not billing.get_plan(body.plan_id):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown plan")
    shop = await _shop(current, db)
    client = _client(shop)
    await db.commit()
    try:
        result = await billing.create_subscription(client, body.plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ShopifyAPIError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Shopify could not create the subscription. Please try again.")
    return {
        "confirmation_url": result["confirmation_url"],
        "subscription_id": result.get("subscription_id"),
        "status": result.get("status"),
        "test_mode": bool(billing.settings.billing_test),
    }
