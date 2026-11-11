"""Keep server-side OAuth scope defaults aligned with the Shopify app manifest."""

from app.shopify.config import Settings


def test_default_shopify_scopes_include_sales_channel_permissions():
    default_scopes = set(Settings.model_fields["shopify_scopes"].default.split(","))

    assert {
        "read_publications",
        "write_publications",
    }.issubset(default_scopes)
