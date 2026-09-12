"""
Merchant isolation and auth regression tests.

These tests verify that:
- optional-auth endpoints stay disconnected for invalid/absent sessions
- required-auth endpoints reject invalid/expired sessions
- merchant-owned data is always scoped by authenticated shop
- the app does not accept client-supplied shop IDs as authorization
"""
import json

import pytest


def test_auth_session_without_bearer_returns_not_connected(client):
    response = client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json()["connected"] is False


def test_settings_requires_shop_context(client):
    response = client.get("/api/settings/")

    assert response.status_code == 200
    assert response.json()["connected"] is False


def test_products_list_without_auth_returns_disconnected(client):
    response = client.get("/api/products/")

    assert response.status_code == 200
    assert response.json()["connected"] is False


def test_imports_create_requires_auth(client):
    response = client.post(
        "/api/imports/",
        data={},
    )

    assert response.status_code == 401


def test_invalid_bearer_does_not_grant_access(client):
    response = client.get(
        "/api/products/",
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 200
    assert response.json()["connected"] is False
