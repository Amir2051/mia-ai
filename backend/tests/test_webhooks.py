import base64
import hashlib
import hmac
import json

import pytest
from app.shopify.config import settings as router_settings


def test_webhook_requires_headers(client):
    response = client.post(
        "/api/webhooks",
        content=b'{"id":123}',
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Missing webhook headers"


def test_webhook_without_shopify_secret(client):
    original_secret = router_settings.shopify_api_secret
    router_settings.shopify_api_secret = ""

    try:
        response = client.post(
            "/api/webhooks",
            headers={
                "X-Shopify-Topic": "products/create",
                "X-Shopify-Shop-Domain": "example.myshopify.com",
                "X-Shopify-Event-Id": "event-123",
            },
            content=json.dumps({"id": 123}).encode(),
        )

        assert response.status_code == 503
        assert response.json()["detail"] == "Webhook verification is not configured"
    finally:
        router_settings.shopify_api_secret = original_secret


def test_webhook_rejects_missing_signature_when_secret_configured(client):
    original_secret = router_settings.shopify_api_secret
    router_settings.shopify_api_secret = "test-secret"

    try:
        response = client.post(
            "/api/webhooks",
            headers={
                "X-Shopify-Topic": "products/create",
                "X-Shopify-Shop-Domain": "example.myshopify.com",
                "X-Shopify-Event-Id": "event-123",
            },
            content=b'{"id":123}',
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid webhook signature"
    finally:
        router_settings.shopify_api_secret = original_secret


def test_webhook_rejects_invalid_signature(client):
    original_secret = router_settings.shopify_api_secret
    router_settings.shopify_api_secret = "test-secret"

    try:
        response = client.post(
            "/api/webhooks",
            headers={
                "X-Shopify-Topic": "products/create",
                "X-Shopify-Shop-Domain": "example.myshopify.com",
                "X-Shopify-Event-Id": "event-123",
                "X-Shopify-Hmac-SHA256": "bad-signature",
            },
            content=b'{"id":123}',
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid webhook signature"
    finally:
        router_settings.shopify_api_secret = original_secret


def test_webhook_accepts_valid_signature(client):
    original_secret = router_settings.shopify_api_secret
    router_settings.shopify_api_secret = "test-secret"

    body = b'{"id":123,"title":"Test Product"}'
    signature = base64.b64encode(
        hmac.new(
            b"test-secret",
            body,
            hashlib.sha256,
        ).digest()
    ).decode()

    try:
        response = client.post(
            "/api/webhooks",
            headers={
                "X-Shopify-Topic": "products/create",
                "X-Shopify-Shop-Domain": "example.myshopify.com",
                "X-Shopify-Event-Id": "event-123",
                "X-Shopify-Hmac-SHA256": signature,
            },
            content=body,
        )

        assert response.status_code == 200
        assert response.json()["status"] == "received"
    finally:
        router_settings.shopify_api_secret = original_secret
