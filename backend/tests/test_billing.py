import asyncio

import pytest

from app.services import billing
from app.shopify.config import settings


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def graphql(self, query, variables=None, *, allow_graphql_errors=False):
        self.calls.append((query, variables))
        return self.response


def test_create_subscription_returns_confirmation_url(monkeypatch):
    monkeypatch.setattr(settings, "billing_test", True)
    monkeypatch.setattr(settings, "shopify_app_url", "https://mia.drivenest.info")
    client = FakeClient({
        "appSubscriptionCreate": {
            "userErrors": [],
            "confirmationUrl": "https://admin.shopify.com/confirm/123",
            "appSubscription": {"id": "gid://shopify/AppSubscription/1", "name": "Mia AI Pro", "status": "PENDING"},
        }
    })
    out = asyncio.run(billing.create_subscription(client, "pro"))
    assert out["confirmation_url"] == "https://admin.shopify.com/confirm/123"
    assert out["subscription_id"] == "gid://shopify/AppSubscription/1"
    _, variables = client.calls[0]
    assert variables["test"] is True
    assert variables["returnUrl"].startswith("https://mia.drivenest.info/billing")
    assert variables["lineItems"][0]["plan"]["appRecurringPricingDetails"]["price"]["amount"] == "29.00"


def test_create_subscription_unknown_plan_raises():
    with pytest.raises(ValueError):
        asyncio.run(billing.create_subscription(FakeClient({}), "nope"))


def test_create_subscription_surfaces_user_errors():
    client = FakeClient({"appSubscriptionCreate": {"userErrors": [{"field": "plan", "message": "bad plan"}], "confirmationUrl": None}})
    with pytest.raises(ValueError):
        asyncio.run(billing.create_subscription(client, "pro"))


def test_active_subscription_only_counts_active_status():
    active = FakeClient({"currentAppInstallation": {"activeSubscriptions": [
        {"id": "gid://s/1", "name": "Mia AI Pro", "status": "ACTIVE", "test": True, "lineItems": []}]}})
    assert asyncio.run(billing.get_active_subscription(active))["status"] == "ACTIVE"

    pending = FakeClient({"currentAppInstallation": {"activeSubscriptions": [
        {"id": "gid://s/1", "name": "Mia AI Pro", "status": "PENDING", "lineItems": []}]}})
    assert asyncio.run(billing.get_active_subscription(pending)) is None

    none = FakeClient({"currentAppInstallation": {"activeSubscriptions": []}})
    assert asyncio.run(billing.get_active_subscription(none)) is None


def test_entitlement_disabled_does_not_call_shopify(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", False)
    client = FakeClient({"currentAppInstallation": {"activeSubscriptions": []}})
    assert asyncio.run(billing.is_entitled(client)) is True
    assert client.calls == []


def test_entitlement_enforced_requires_active_subscription(monkeypatch):
    monkeypatch.setattr(settings, "billing_enforced", True)
    with_active = FakeClient({"currentAppInstallation": {"activeSubscriptions": [
        {"id": "gid://s/1", "status": "ACTIVE", "lineItems": []}]}})
    assert asyncio.run(billing.is_entitled(with_active)) is True

    without = FakeClient({"currentAppInstallation": {"activeSubscriptions": []}})
    assert asyncio.run(billing.is_entitled(without)) is False
