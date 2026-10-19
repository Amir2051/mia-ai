from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from app.services import social_oauth
from app.shopify.config import settings


@pytest.mark.parametrize(("provider", "expected_host", "expected_scope"), [
    ("tiktok", "www.tiktok.com", "video.publish"),
    ("pinterest", "www.pinterest.com", "pins:write"),
    ("youtube", "accounts.google.com", "youtube.upload"),
    ("linkedin", "www.linkedin.com", "w_member_social"),
])
def test_provider_authorization_url_has_redirect_state_and_required_scope(monkeypatch, provider, expected_host, expected_scope):
    monkeypatch.setattr(settings, "shopify_app_url", "https://mia.example.test")
    monkeypatch.setattr(settings, f"{provider}_redirect_uri", "")
    key_setting = social_oauth.PROVIDERS[provider]["key_setting"]
    secret_setting = social_oauth.PROVIDERS[provider]["secret_setting"]
    monkeypatch.setattr(settings, key_setting, "test-client-id")
    monkeypatch.setattr(settings, secret_setting, "test-client-secret")
    url = social_oauth.authorization_url(provider, "signed-state")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert parsed.hostname == expected_host
    assert params["state"] == ["signed-state"]
    assert params["redirect_uri"] == [f"https://mia.example.test/api/social/{provider}/callback"]
    assert expected_scope in params["scope"][0]
    assert "test-client-secret" not in url


def test_provider_credentials_are_required(monkeypatch):
    monkeypatch.setattr(settings, "tiktok_client_key", "")
    monkeypatch.setattr(settings, "tiktok_client_secret", "")
    with pytest.raises(HTTPException) as exc:
        social_oauth.authorization_url("tiktok", "state")
    assert exc.value.status_code == 503


def test_unknown_provider_is_not_available():
    assert "threads" not in social_oauth.PROVIDERS


def test_provider_oauth_state_is_bound_to_its_provider(monkeypatch):
    import time
    from app.routers.social import _sign_state, _verify_state

    monkeypatch.setattr(settings, "secret_key", "test-signing-secret")
    state = _sign_state("shop.myshopify.com", "nonce", int(time.time()), "tiktok")
    assert _verify_state(state, "tiktok") == "shop.myshopify.com"
    with pytest.raises(HTTPException) as exc:
        _verify_state(state, "youtube")
    assert exc.value.status_code == 400
