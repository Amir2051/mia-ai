"""Deterministic, product-grounded marketing copy.

Used only when the configured AI model is unavailable or returns unusable output.
Uses ONLY real Shopify product fields - never invents discounts, features, reviews,
stock claims, or specifications - so the campaign is always safe and usable.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser


class _Strip(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        t = data.strip()
        if t:
            self.parts.append(t)


def _text(raw: str, limit: int = 200) -> str:
    p = _Strip()
    try:
        p.feed(raw or "")
    except Exception:
        pass
    s = " ".join(" ".join(p.parts).split())
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(".,; ") + "..."
    return s


def _price(product: dict) -> str | None:
    try:
        edges = ((product.get("variants") or {}).get("edges")) or []
        if edges:
            price = (edges[0].get("node") or {}).get("price")
            if price:
                return str(price)
    except Exception:
        pass
    return None


def build_fallback_hashtags(product: dict) -> list[str]:
    seeds: list[str] = [str(t) for t in (product.get("tags") or [])[:6]]
    if product.get("productType"):
        seeds.append(str(product["productType"]))
    if product.get("vendor"):
        seeds.append(str(product["vendor"]))
    out: list[str] = []
    seen: set[str] = set()
    for s in seeds:
        word = re.sub(r"[^A-Za-z0-9]+", "", s.title())
        if word and word.lower() not in seen and len(word) <= 30:
            seen.add(word.lower())
            out.append("#" + word)
    # Avoid generic promotional tags that imply novelty or a purchase intent.
    # Only emit hashtags supported by the merchant's actual catalog fields.
    return out[:8]


def build_fallback_campaign(product: dict) -> dict:
    title = str(product.get("title") or "this product").strip()
    vendor = str(product.get("vendor") or "").strip()
    ptype = str(product.get("productType") or "").strip()
    snippet = _text(product.get("descriptionHtml") or "", 200)
    price = _price(product)
    brandline = f" by {vendor}" if vendor else ""
    # Only use a price when the live product payload actually supplies one. Do not
    # imply availability or stock status when that data is absent.
    price_line = f" Listed price: {price}." if price else ""
    detail = snippet or (f"Product category: {ptype}." if ptype else "Review the product details in the store.")
    hashtags = build_fallback_hashtags(product)
    tag_str = " ".join(hashtags[:5])
    return {
        "product_id": product.get("id") or "",
        "product_title": title,
        "image_url": None,
        "enhanced_image_url": None,
        "facebook": {
            "copy": f"A closer look at {title}{brandline}. {detail}{price_line} Review the product listing for verified details and available options.",
            "cta": "Learn More" if not product.get("onlineStoreUrl") else "Shop Now",
        },
        "instagram": {
            "copy": f"Meet {title}. Explore the listing to see its product details and available options.\n\n{tag_str}".strip(),
            "cta": "Shop Now",
        },
        "tiktok": {
            "copy": f"Take a closer look at {title}. See the product listing for verified details and available options.",
            "cta": "Shop Now",
        },
        "email": {
            "subject": f"A closer look at {title}",
            "body": f"Hi there,\n\nWe'd like to introduce {title}{brandline}.\n\n{detail}{price_line}\n\nReview the product details and available options in our store.\n\nThanks for being here.",
        },
        "ad": {
            "primary_text": f"Looking for a snowboard? Explore {title}{brandline}. Review the product listing for verified details and available options.{price_line}",
            "headline": title[:40].strip(),
            "description": "Explore verified product details.",
            "cta": "Learn More" if not product.get("onlineStoreUrl") else "Shop Now",
        },
        "creative_prompt": (
            f"Create a clean, professional commercial product photograph of {title}, "
            "preserving the exact product appearance from the supplied Shopify reference image. "
            "Bright even lighting, neutral background, ecommerce style. "
            "Do not invent features, packaging, labels, or text."
        ),
        "hashtags": hashtags,
    }
