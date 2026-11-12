"""Keep Shopify health-check scope requirements aligned with the app manifest."""

import tomllib
from pathlib import Path

from app.routers.settings import _health_check_scopes
from app.shopify.config import REQUIRED_SHOPIFY_SCOPES, Settings


def test_default_shopify_scopes_match_app_manifest():
    manifest_path = Path(__file__).resolve().parents[2] / "shopify.app.toml"
    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_scopes = set(manifest["access_scopes"]["scopes"].split(","))
    default_scopes = set(Settings.model_fields["shopify_scopes"].default.split(","))

    assert default_scopes == manifest_scopes == set(REQUIRED_SHOPIFY_SCOPES)


def test_environment_override_cannot_hide_manifest_required_scopes():
    effective = _health_check_scopes("read_products,write_products,read_marketing_events")

    assert set(REQUIRED_SHOPIFY_SCOPES).issubset(effective)
    assert "read_publications" in effective
    assert "write_publications" in effective
    assert "read_marketing_events" in effective
