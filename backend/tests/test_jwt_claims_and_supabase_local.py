"""Genuine Supabase local-vs-production identification and JWT-claim manipulation tests.

These tests assert on the structure that distinguishes a local throwaway Postgres
(safenestt AI dev/test) from a production Supabase project, then operate on JWT
claims with the real `jose.jwt` encoder/decoder AND the real access-token
validation path (`decode_access_token`) with a correctly configured test key.

Scope rules enforced here:
  - No network calls: all Supabase classification is pure string/URL analysis.
  - No production data is read, mutated, or logged.
  - No secrets are printed.
"""

import base64
import json
from datetime import datetime, timedelta
from urllib.parse import urlparse

import pytest
from fastapi import HTTPException, status

import jwt as jose_jwt

from app.auth.dependencies import _validate_shopify_id_token
from app.shopify.auth import (
    AuthenticationError,
    create_access_token,
    decode_access_token,
)

# ---------------------------------------------------------------------------
# 1. Supabase local-vs-production identification (structural, no network)
# ---------------------------------------------------------------------------

# Structural signals observed in the session:
#   local (dev/test) Postgres: 127.0.0.1:55432, db=safenestt_ai_test, roles
#     'safenestt_owner_test' (owner) + 'safenestt_test' (superuser).
#   production Supabase: postgres://<project>-postgres:5432/<project>_postgres
#     or /<project>_auth etc. (public project name in the DB name, service_role key).
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
LOCAL_DB_MARKERS = ("_test", "test", "safenestt_ai_test", "safenestt_dev")
PRODUCTION_DB_MARKERS = ("_postgres", "_auth", "_rest", "_realtime", "_storage", "_edge")
PRODUCTION_HOST_MARKERS = ("supabase.co", "database.cloudflare")


def identify_supabase(url: str | None, key: str | None) -> dict:
    """Return a dict with local/production booleans and an immutable evidence list."""
    evidence: list[str] = []
    verdict = {"local": False, "production": False, "source": "unknown", "evidence": evidence}

    if not url:
        evidence.append("database_url empty -> cannot identify")
        return verdict

    low = url.lower()
    host = urlparse(url).hostname or ""
    parsed = urlparse(url)

    # Local Postgres for dev/test.
    if host in LOCAL_HOSTS:
        verdict["local"] = True
        evidence.append(f"host={host}")

    # Production Supabase host shape.
    if any(m in host for m in PRODUCTION_HOST_MARKERS):
        verdict["production"] = True
        evidence.append(f"host_marker={host}")

    # Production Supabase DB name shape.
    if any(m in low for m in PRODUCTION_DB_MARKERS):
        verdict["production"] = True
        evidence.append(f"db_marker={parsed.path}")

    # Local dev/test DB name shape.
    if any(m in low for m in LOCAL_DB_MARKERS):
        if not verdict["production"]:
            verdict["local"] = True
        evidence.append(f"db_local_marker={parsed.path}")

    # A real service_role key present on an https production hostname => production.
    if key and "supabase.co" in host and key.strip():
        verdict["production"] = True
        evidence.append("service_role_key_present_on_production_host")

    verdict["evidence"] = evidence
    return verdict


@pytest.fixture(scope="module")
def local_supabase_shape():
    """The test database is a local, throwaway Postgres instance."""
    from app.shopify.config import settings

    shape = identify_supabase(settings.database_url, settings.supabase_service_role_key)
    assert shape["local"] is True, "expected local Postgres for the test db"
    assert shape["production"] is False
    return shape


def test_local_postgres_url_is_local_not_production():
    shape = identify_supabase("postgresql://127.0.0.1:55432/safenestt_ai_test", None)
    assert shape["local"] is True
    assert shape["production"] is False


def test_production_supabase_db_name_shape_is_production():
    url = "postgresql://mia-prod-postgres:5432/mia_prod_postgres"
    shape = identify_supabase(url, None)
    assert shape["local"] is False
    assert shape["production"] is True


def test_production_supabase_hostname_marker_is_production():
    url = "postgresql://mia-prod.supabase.co:5432/mia_prod"
    shape = identify_supabase(url, None)
    assert shape["local"] is False
    assert shape["production"] is True


def test_malformed_and_missing_urls_are_unknown():
    for bad in (None, "", "not a url", "postgresql://", ":///bad", "127.0.0.1"):
        shape = identify_supabase(bad, None)
        assert shape["local"] is False
        assert shape["production"] is False
        assert shape["source"] == "unknown"


# ---------------------------------------------------------------------------
# 2. Real JWT creation + claim mutation (NO pre-signed/replayed tokens)
# ---------------------------------------------------------------------------

TEST_SIGNING_KEY = "test-local-signing-key-0123456789abcdef"
WRONG_KEY = "completely-different-signing-key-forged"


def _make_claims(sub: str, org: str, aud: str | None = None, expires_in_hours: int = 1) -> dict:
    now = datetime.now()
    claims = {
        "sub": sub,
        "org_id": org,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=expires_in_hours)).timestamp()),
    }
    if aud is not None:
        claims["aud"] = aud
    return claims


def _encode_with(payload: dict, key: str, algorithm: str = "HS256") -> str:
    return jose_jwt.encode(payload, key, algorithm=algorithm)


@pytest.fixture(autouse=True)
def _patch_settings_key(monkeypatch):
    from app.shopify.config import settings

    monkeypatch.setattr(settings, "secret_key", TEST_SIGNING_KEY)
    monkeypatch.setattr(settings, "jwt_algorithm", "HS256")
    monkeypatch.setattr(settings, "access_token_expire_minutes", 60)


# ---------------------------------------------------------------------------
# 3. JWT claim manipulation tests (against the real validation path)
# ---------------------------------------------------------------------------


def test_valid_access_token_decodes_with_test_key():
    payload = _make_claims("sub-ok", "org-ok")
    token = create_access_token(payload)
    decoded = decode_access_token(token)
    assert decoded["sub"] == "sub-ok"
    assert decoded["org_id"] == "org-ok"


def test_forged_signature_with_wrong_key_is_rejected():
    payload = _make_claims("subject-a", "org-b", "shopify")
    forged = _encode_with(payload, WRONG_KEY)
    with pytest.raises(AuthenticationError):
        decode_access_token(forged)


def test_expired_token_rejected():
    old = _make_claims("sub-ev", "org-ev", "shopify")
    old["exp"] = int((datetime.now() - timedelta(hours=2)).timestamp())
    token = _encode_with(old, TEST_SIGNING_KEY)
    with pytest.raises(AuthenticationError):
        decode_access_token(token)


def test_unsigned_token_rejected():
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode("ascii")
    part = base64.urlsafe_b64encode(json.dumps({"sub": "x"}).encode()).rstrip(b"=").decode("ascii")
    token = f"{header}.{part}."
    with pytest.raises(AuthenticationError):
        decode_access_token(token)


def test_subject_tampering_returns_expected_claim():
    clean = _make_claims("subject-normal", "org-canonical")
    tampered = dict(clean)
    tampered["sub"] = "subject-tampered"
    token = _encode_with(tampered, TEST_SIGNING_KEY)
    decoded = decode_access_token(token)
    # The real access-token path only enforces signature+expiry: the mutated
    # sub is carried through exactly as signed.
    assert decoded["sub"] == "subject-tampered"
    assert decoded["sub"] != "subject-normal"


def test_organization_tampering_returns_expected_claim():
    clean = _make_claims("sub-ok", "org-canonical")
    tampered = dict(clean)
    tampered["org_id"] = "org-not-the-original"
    token = _encode_with(tampered, TEST_SIGNING_KEY)
    decoded = decode_access_token(token)
    assert decoded["org_id"] == "org-not-the-original"
    assert decoded["org_id"] != clean["org_id"]


def test_audience_tampering_rejected():
    """The real access-token path enforces the audience claim (PyJWT default)."""
    clean = _make_claims("sub-ok", "org-ok")
    tampered = dict(clean)
    tampered["aud"] = "shopify-not-the-original"
    token = _encode_with(tampered, TEST_SIGNING_KEY)
    with pytest.raises(AuthenticationError):
        decode_access_token(token)


def test_wrong_key_rejected_even_for_tampered_subject():
    clean = _make_claims("sub-normal", "org-ok", "shopify")
    tampered = dict(clean)
    tampered["sub"] = "subject-tampered"
    forged = _encode_with(tampered, WRONG_KEY)
    with pytest.raises(AuthenticationError):
        decode_access_token(forged)


def test_empty_token_raises_http_exception_for_shopify_id_token(monkeypatch):
    """Empty token: _validate_shopify_id_token raises HTTPException 401.

    The implementation reads the unverified JWT header before checking claims,
    and an empty string fails to decode -> HTTPException (401), not None.
    """
    from app.shopify.config import settings

    monkeypatch.setattr(settings, "shopify_api_secret", "dummy-secret-for-test")
    monkeypatch.setattr(settings, "shopify_api_key", "dummy-api-key-for-test")
    with pytest.raises(HTTPException) as exc_info:
        _validate_shopify_id_token("")
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
