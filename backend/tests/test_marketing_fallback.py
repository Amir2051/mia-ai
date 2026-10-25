from app.services.marketing_fallback import build_fallback_campaign

PRODUCT = {
    "id": "gid://shopify/Product/1",
    "title": "Aurora Desk Lamp",
    "vendor": "Lumen Co",
    "productType": "Lighting",
    "tags": ["modern", "LED", "home office"],
    "descriptionHtml": "<p>A warm, dimmable LED desk lamp with a brushed aluminum body.</p>",
    "variants": {"edges": [{"node": {"price": "49.00"}}]},
}


def test_fallback_is_product_grounded_and_distinct():
    c = build_fallback_campaign(PRODUCT)
    for ch in ("facebook", "instagram", "tiktok"):
        assert "Aurora Desk Lamp" in c[ch]["copy"]
    assert len({c["facebook"]["copy"], c["instagram"]["copy"], c["tiktok"]["copy"]}) == 3
    assert "49.00" in c["facebook"]["copy"] or "49.00" in c["ad"]["primary_text"]
    assert "LED desk lamp" in c["facebook"]["copy"] or "LED desk lamp" in c["email"]["body"]
    assert any(h.lower() == "#led" for h in c["hashtags"])
    assert c["ad"]["headline"] and len(c["ad"]["headline"]) <= 40
    assert c["email"]["subject"] and len(c["email"]["body"]) > 40


def test_fallback_handles_missing_fields_without_inventing():
    c = build_fallback_campaign({"title": "Mystery Item"})
    assert c["facebook"]["copy"] and c["instagram"]["copy"] and c["tiktok"]["copy"]
    assert c["hashtags"] == []
    assert "$" not in c["facebook"]["copy"]
    assert "Available now for" not in c["facebook"]["copy"]


def test_fallback_ad_does_not_claim_stock_or_fake_price():
    from app.services.marketing_fallback import build_fallback_campaign

    result = build_fallback_campaign({
        "id": "gid://shopify/Product/1",
        "title": "Inventory Not Tracked Snowboard",
        "productType": "Snowboard",
        "vendor": "SafeNestT",
        "descriptionHtml": "<p>Review the product details.</p>",
        "images": [],
    })
    ad = result["ad"]
    assert "available now" not in ad["primary_text"].lower()
    assert "in stock" not in ad["primary_text"].lower()
    assert "Listed price:" not in ad["primary_text"]
    assert ad["headline"]
    assert ad["cta"] == "Learn More"
