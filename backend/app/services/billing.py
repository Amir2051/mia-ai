"""Shopify-compliant app billing.

All merchant charges go through Shopify's Billing API (appSubscriptionCreate) -
never an external processor - as required by the App Store. Entitlement is always
verified live from Shopify (currentAppInstallation.activeSubscriptions), never from
callback query parameters, so forged return URLs cannot grant access.
"""
from __future__ import annotations

from typing import Any, Optional

from app.shopify.client import ShopifyAPIClient
from app.shopify.config import settings

PLANS: dict[str, dict[str, Any]] = {
    "pro": {
        "id": "pro",
        "name": "Mia AI Pro",
        "amount": "29.00",
        "currency": "USD",
        "interval": "EVERY_30_DAYS",
        "trial_days": 7,
        "features": [
            "AI SEO optimization for every product",
            "Ready-to-post marketing campaigns (FB, IG, TikTok, email, ads)",
            "Brand-voice grounding and image enhancement",
            "Scheduled sales reports",
        ],
    },
}
DEFAULT_PLAN = "pro"


def get_plan(plan_id: str) -> Optional[dict[str, Any]]:
    return PLANS.get(plan_id)


def _return_url() -> str:
    base = (settings.shopify_app_url or "").rstrip("/")
    return f"{base}/billing?billing=return"


_CREATE_MUTATION = """
mutation MiaAppSubscriptionCreate($name: String!, $lineItems: [AppSubscriptionLineItemInput!]!, $returnUrl: URL!, $test: Boolean, $trialDays: Int) {
  appSubscriptionCreate(name: $name, lineItems: $lineItems, returnUrl: $returnUrl, test: $test, trialDays: $trialDays, replacementBehavior: STANDARD) {
    userErrors { field message }
    confirmationUrl
    appSubscription { id name status }
  }
}
""".strip()

_ACTIVE_QUERY = """
query MiaActiveSubscriptions {
  currentAppInstallation {
    activeSubscriptions {
      id
      name
      status
      test
      trialDays
      currentPeriodEnd
      createdAt
      lineItems {
        plan {
          pricingDetails {
            __typename
            ... on AppRecurringPricing { price { amount currencyCode } interval }
          }
        }
      }
    }
  }
}
""".strip()


async def create_subscription(client: ShopifyAPIClient, plan_id: str) -> dict[str, Any]:
    plan = get_plan(plan_id)
    if not plan:
        raise ValueError(f"Unknown plan: {plan_id}")
    variables = {
        "name": plan["name"],
        "returnUrl": _return_url(),
        "test": bool(settings.billing_test),
        "trialDays": int(plan.get("trial_days") or 0),
        "lineItems": [
            {
                "plan": {
                    "appRecurringPricingDetails": {
                        "price": {"amount": plan["amount"], "currencyCode": plan["currency"]},
                        "interval": plan["interval"],
                    }
                }
            }
        ],
    }
    data = await client.graphql(_CREATE_MUTATION, variables)
    payload = data.get("appSubscriptionCreate") or {}
    errors = payload.get("userErrors") or []
    if errors:
        msg = "; ".join(str(e.get("message") or "") for e in errors) or "Subscription could not be created"
        raise ValueError(msg)
    confirmation_url = payload.get("confirmationUrl")
    sub = payload.get("appSubscription") or {}
    if not confirmation_url:
        raise ValueError("Shopify did not return a confirmation URL")
    return {"confirmation_url": confirmation_url, "subscription_id": sub.get("id"), "status": sub.get("status")}


def _recurring_price(subscription: dict[str, Any]) -> Optional[dict[str, Any]]:
    for item in subscription.get("lineItems") or []:
        details = ((item.get("plan") or {}).get("pricingDetails")) or {}
        if details.get("__typename") == "AppRecurringPricing" and details.get("price"):
            return {"price": details["price"], "interval": details.get("interval")}
    return None


async def get_active_subscription(client: ShopifyAPIClient) -> Optional[dict[str, Any]]:
    data = await client.graphql(_ACTIVE_QUERY)
    install = data.get("currentAppInstallation") or {}
    for sub in install.get("activeSubscriptions") or []:
        if str(sub.get("status")).upper() == "ACTIVE":
            return {
                "id": sub.get("id"),
                "name": sub.get("name"),
                "status": sub.get("status"),
                "test": bool(sub.get("test")),
                "trial_days": sub.get("trialDays"),
                "current_period_end": sub.get("currentPeriodEnd"),
                "created_at": sub.get("createdAt"),
                "pricing": _recurring_price(sub),
            }
    return None


async def is_entitled(client: ShopifyAPIClient) -> bool:
    if not settings.billing_enforced:
        return True
    try:
        return (await get_active_subscription(client)) is not None
    except Exception:
        return False
