from urllib.parse import parse_qs, urlparse
import time

import pytest
from fastapi import HTTPException

from app.auth.dependencies import CurrentUser, get_optional_shop
from app.routers.social import _sign_state, _verify_state
from app.shopify.config import settings


def test_meta_callback_defers_token_exchange_until_authenticated_completion(client, monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "test-oauth-signing-secret")
    monkeypatch.setattr(settings, "shopify_app_url", "https://mia.example.test")
    state = _sign_state("owner.myshopify.com", "one-time-random-nonce", int(time.time()))

    response = client.get("/api/social/meta/callback", params={"code": "short-lived-code", "state": state}, follow_redirects=False)

    assert response.status_code == 303
    location = urlparse(response.headers["location"])
    assert location.path == "/social"
    query = parse_qs(location.fragment)
    assert query["oauth_provider"] == ["meta"]
    assert query["oauth_code"] == ["short-lived-code"]
    assert query["oauth_state"] == [state]


@pytest.mark.parametrize("provider", ["tiktok", "pinterest", "youtube", "linkedin"])
def test_provider_callback_defers_token_exchange_until_authenticated_completion(client, monkeypatch, provider):
    monkeypatch.setattr(settings, "secret_key", "test-oauth-signing-secret")
    monkeypatch.setattr(settings, "shopify_app_url", "https://mia.example.test")
    state = _sign_state("owner.myshopify.com", "one-time-random-nonce", int(time.time()), provider)

    response = client.get(f"/api/social/{provider}/callback", params={"code": "short-lived-code", "state": state}, follow_redirects=False)

    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).fragment)
    assert query["oauth_provider"] == [provider]
    assert query["oauth_code"] == ["short-lived-code"]
    assert query["oauth_state"] == [state]


def test_meta_completion_rejects_a_different_authenticated_shop_before_token_exchange(client, monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "test-oauth-signing-secret")
    state = _sign_state("attacker-shop.myshopify.com", "random-nonce", int(time.time()))

    async def authenticated_victim_shop():
        return CurrentUser(shop_domain="victim-shop.myshopify.com")

    client.app.dependency_overrides[get_optional_shop] = authenticated_victim_shop
    try:
        response = client.post("/api/social/meta/complete", json={"code": "short-lived-code", "state": state})
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)

    assert response.status_code == 403
    assert "does not match" in response.json()["detail"]


@pytest.mark.parametrize("provider", ["tiktok", "pinterest", "youtube", "linkedin"])
def test_provider_completion_rejects_a_different_authenticated_shop(client, monkeypatch, provider):
    monkeypatch.setattr(settings, "secret_key", "test-oauth-signing-secret")
    state = _sign_state("attacker-shop.myshopify.com", "random-nonce", int(time.time()), provider)

    async def authenticated_victim_shop():
        return CurrentUser(shop_domain="victim-shop.myshopify.com")

    client.app.dependency_overrides[get_optional_shop] = authenticated_victim_shop
    try:
        response = client.post(f"/api/social/{provider}/complete", json={"code": "short-lived-code", "state": state})
    finally:
        client.app.dependency_overrides.pop(get_optional_shop, None)

    assert response.status_code == 403
    assert "does not match" in response.json()["detail"]


def test_oauth_state_rejects_tampering(client, monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "test-oauth-signing-secret")
    state = _sign_state("owner.myshopify.com", "random-nonce", int(time.time()), "youtube")
    with pytest.raises(HTTPException) as exc:
        _verify_state(state, "linkedin")
    assert exc.value.status_code == 400
