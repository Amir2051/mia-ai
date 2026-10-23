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
    for generic in ("#ShopNow", "#NewArrival"):
        if generic.lower() not in {t.lower() for t in out}:
            out.append(generic)
    return out[:8]


def build_fallback_campaign(product: dict) -> dict:
    title = str(product.get("title") or "this product").strip()
    vendor = str(product.get("vendor") or "").strip()
    ptype = str(product.get("productType") or "").strip()
    snippet = _text(product.get("descriptionHtml") or "", 200)
    price = _price(product)
    brandline = f" by {vendor}" if vendor else ""
    price_line = f" Available now for {price}." if price else ""
    detail = snippet or (f"A {ptype.lower()} available in our store." if ptype else "Available in our store.")
    hashtags = build_fallback_hashtags(product)
    tag_str = " ".join(hashtags[:5])
    return {
        "product_id": product.get("id") or "",
        "product_title": title,
        "image_url": None,
        "enhanced_image_url": None,
        "facebook": {
            "copy": f"Meet {title}{brandline}. {detail}{price_line} Tap to see it in our store.",
            "cta": "Shop Now",
        },
        "instagram": {
            "copy": f"{title} is here. {detail}\n\n{tag_str}".strip(),
            "cta": "Shop Now",
        },
        "tiktok": {
            "copy": f"POV: you just found {title}. {detail} Link in bio to shop.",
            "cta": "Shop Now",
        },
        "email": {
            "subject": f"{title} is now in our store",
            "body": f"Hi there,\n\nWe'd love to introduce {title}{brandline}.\n\n{detail}{price_line}\n\nTake a closer look and shop it in our store today.\n\nThanks for being here.",
        },
        "ad": {
            "primary_text": f"{title}{brandline}. {detail}{price_line}",
            "headline": title[:40].strip(),
            "description": (f"Shop {ptype.lower()} in our store." if ptype else "Shop it in our store."),
            "cta": "Shop Now",
        },
        "creative_prompt": (
            f"Create a clean, professional commercial product photograph of {title}, "
            "preserving the exact product appearance from the supplied Shopify reference image. "
            "Bright even lighting, neutral background, ecommerce style. "
            "Do not invent features, packaging, labels, or text."
        ),
        "hashtags": hashtags,
    }
