from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.services.openrouter import OpenRouterError, OpenRouterService
from app.services.product_image import ProductImageError, enhance_product_image


class SEOResult(BaseModel):
    seo_title: str = Field(max_length=70)
    meta_description: str = Field(max_length=320)
    optimized_title: str = Field(max_length=255)
    optimized_description_html: str = Field(max_length=20000)
    keywords: list[str] = Field(default_factory=list, max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=30)
    handle_suggestion: str = Field(max_length=255)
    image_alt_text: list[str] = Field(default_factory=list, max_length=50)
    social_title: str = Field(max_length=255)
    social_description: str = Field(max_length=1000)
    seo_score: int = Field(ge=0, le=100)
    issues: list[str] = Field(default_factory=list, max_length=30)
    recommendations: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("keywords", "tags", "image_alt_text", mode="before")
    @classmethod
    def clean_lists(cls, values: Any) -> list[str]:
        # Free/routed models sometimes return a single string instead of an array.
        # Normalize that harmless shape variation before strict schema validation.
        if values is None:
            return []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, (list, tuple)):
            values = [values]
        return [str(v).strip() for v in values if str(v).strip()][:50]


class _SafeHTML(HTMLParser):
    allowed = {"p", "strong", "em", "b", "i", "ul", "ol", "li", "h2", "h3", "br", "a"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in self.allowed:
            return
        if tag == "br":
            self.out.append("<br>")
            return
        if tag == "a":
            href = next((v for k, v in attrs if k == "href"), "") or ""
            if href.startswith(("https://", "http://")):
                self.out.append(f'<a href="{html.escape(href, quote=True)}" rel="nofollow noopener">')
                return
        self.out.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.allowed and tag != "br":
            self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.out.append(html.escape(data))


def sanitize_html(value: str) -> str:
    parser = _SafeHTML()
    parser.feed(value or "")
    parser.close()
    return "".join(parser.out)


def plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", value or "")).strip()


def normalize_handle(value: str) -> str:
    value = plain_text(value).lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:255]


class SEOService:
    def __init__(self, ai: OpenRouterService | None = None):
        self.ai = ai or OpenRouterService()

    @staticmethod
    def prompt(product: dict[str, Any]) -> list[dict[str, str]]:
        compact = {
            "id": product.get("id"),
            "title": product.get("title"),
            "description": product.get("descriptionHtml"),
            "product_type": product.get("productType"),
            "vendor": product.get("vendor"),
            "tags": product.get("tags") or [],
            "seo_title": product.get("seo_title"),
            "seo_description": product.get("seo_description"),
            "handle": product.get("handle"),
            "images": product.get("images") or [],
        }
        return [
            {
                "role": "system",
                "content": (
                    "You are Mia's Shopify SEO and marketing engine. Return ONLY valid JSON matching "
                    "the requested schema. Do not invent product facts. Keep claims grounded in the product context. "
                    "SEO title should normally be 50-60 characters; meta description about 140-160 characters. "
                    "Use one clear primary keyword derived from the product title/type and repeat it naturally in the SEO title, meta description, and product description. "
                    "Never leave seo_title, meta_description, optimized_title, or optimized_description_html empty. If the source description is empty, write a useful product description using only facts available in the title, product type, vendor, tags, and image context; do not invent specifications. "
                    "optimized_description_html may use only p,strong,em,b,i,ul,ol,li,h2,h3,br,a."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Analyze and improve this Shopify product. Return keys exactly: "
                    "seo_title, meta_description, optimized_title, optimized_description_html, keywords, tags, "
                    "handle_suggestion, image_alt_text, social_title, social_description, seo_score, issues, recommendations.\n\n"
                    f"PRODUCT CONTEXT JSON:\n{compact}"
                ),
            },
        ]

    async def generate(self, product: dict[str, Any], model: str | None = None) -> tuple[SEOResult, str, float]:
        title = str(product.get("title") or "Product").strip()
        product_type = str(product.get("productType") or "").strip()
        vendor = str(product.get("vendor") or "").strip()
        tags = [str(v).strip() for v in (product.get("tags") or []) if str(v).strip()][:30]
        images = product.get("images") or []
        primary_keyword = product_type or title
        try:
            raw, selected_model, latency = await self.ai.chat_json(self.prompt(product), model=model)
        except OpenRouterError:
            # Keep the production campaign usable when a routed/free model returns
            # malformed JSON. The fallback is deterministic and uses only live
            # Shopify product facts; marketing generation can still run afterward.
            raw = {
                "seo_title": f"{title} | {primary_keyword}"[:70].strip(" |"),
                "meta_description": (f"Shop {title}. Explore this {primary_keyword.lower()} from {vendor}. View product details and shop online." if vendor else f"Shop {title}. Explore this {primary_keyword.lower()} and view product details online.")[:320],
                "optimized_title": title[:255],
                "optimized_description_html": f"<p>Discover {html.escape(title)}{html.escape(f' from {vendor}' if vendor else '')}. View the product details and shop online.</p>",
                "keywords": [primary_keyword],
                "tags": tags,
                "handle_suggestion": normalize_handle(title),
                "image_alt_text": [title[:255]] if images else [],
                "social_title": title[:255],
                "social_description": f"Discover {title} in the Shopify store."[:1000],
                "seo_score": 50,
                "issues": ["AI SEO generation was unavailable; deterministic product-grounded fallback used."],
                "recommendations": ["Retry AI SEO generation when the configured model is available."],
            }
            selected_model = "deterministic-fallback"
            latency = 0.0
        product_type = str(product.get("productType") or "").strip()
        vendor = str(product.get("vendor") or "").strip()
        primary_keyword = str((raw.get("keywords") or [""])[0]).strip() if isinstance(raw.get("keywords"), list) else str(raw.get("keywords") or "").strip()
        primary_keyword = primary_keyword or product_type or title
        raw["seo_title"] = str(raw.get("seo_title") or f"{title} | {primary_keyword}")[:70].strip(" |")
        raw["meta_description"] = str(raw.get("meta_description") or f"Shop {title}. Explore this {primary_keyword.lower()} from {vendor}. View product details and shop online." if vendor else f"Shop {title}. Explore this {primary_keyword.lower()} and view product details online.")[:320].strip()
        raw["optimized_title"] = str(raw.get("optimized_title") or title)[:255].strip()
        description = sanitize_html(str(raw.get("optimized_description_html") or ""))
        if not description:
            context = f" from {vendor}" if vendor else ""
            description = f"<p>Discover {html.escape(title)}{html.escape(context)}. This {html.escape(primary_keyword.lower())} is available in the Shopify store.</p>"
        raw["optimized_description_html"] = description
        raw["handle_suggestion"] = normalize_handle(str(raw.get("handle_suggestion") or title))
        raw["keywords"] = raw.get("keywords") or [primary_keyword]
        raw["issues"] = raw.get("issues") if isinstance(raw.get("issues"), list) else []
        raw["recommendations"] = raw.get("recommendations") if isinstance(raw.get("recommendations"), list) else []
        try:
            result = SEOResult.model_validate(raw)
        except Exception as exc:
            raise OpenRouterError("AI SEO result failed schema validation") from exc
        return result, selected_model, latency

    @staticmethod
    def changes(result: SEOResult) -> dict[str, Any]:
        return {
            "title": result.optimized_title,
            "descriptionHtml": result.optimized_description_html,
            "seo": {"title": result.seo_title, "description": result.meta_description},
            "tags": result.tags,
        }


class MarketingResult(BaseModel):
    product_id: str
    product_title: str
    image_url: str | None = None
    enhanced_image_url: str | None = None
    image_enhancement_source: str | None = None
    image_enhancement_error: str | None = None
    image_retry_after_seconds: int | None = None
    facebook: dict[str, str]
    instagram: dict[str, str]
    tiktok: dict[str, str]
    email: dict[str, str]
    ad: dict[str, str]
    creative_prompt: str
    hashtags: list[str] = Field(default_factory=list, max_length=30)


def marketing_prompt(product: dict[str, Any]) -> list[dict[str, str]]:
    compact = {
        "id": product.get("id"), "title": product.get("title"),
        "description": product.get("descriptionHtml"), "product_type": product.get("productType"),
        "vendor": product.get("vendor"), "tags": product.get("tags") or [],
        "handle": product.get("handle"), "images": product.get("images") or [],
    }
    return [
        {"role": "system", "content": "You are Mia's production ecommerce marketing engine. Return ONLY JSON. Ground every claim in the supplied Shopify product. Never invent discounts, features, guarantees, reviews, stock claims, or specifications. Create copy that can be posted immediately."},
        {"role": "user", "content": "Create a complete ready-to-post marketing package for this Shopify product. Return exactly: product_id, product_title, image_url, enhanced_image_url, facebook{copy,cta}, instagram{copy,cta}, tiktok{copy,cta}, email{subject,body}, ad{primary_text,headline,description,cta}, creative_prompt, hashtags. Make each channel distinct. creative_prompt must be a detailed commercial product-photo prompt that uses the actual product appearance when an image exists, with no invented product features.\n\nPRODUCT JSON:\n" + json.dumps(compact, ensure_ascii=False)},
    ]


async def generate_marketing(ai: OpenRouterService, product: dict[str, Any], model: str | None = None, image_ai: OpenRouterService | None = None) -> tuple[MarketingResult, str, float]:
    try:
        raw, selected_model, latency = await ai.chat_json(marketing_prompt(product), model=model, temperature=0.7)
    except OpenRouterError:
        # Free/routed models can return policy text or malformed JSON instead of the
        # requested marketing object. Keep the campaign usable with deterministic,
        # product-grounded copy rather than turning the whole endpoint into a 502.
        title = str(product.get("title") or "Product").strip()
        raw = {
            "product_id": product.get("id") or "",
            "product_title": title,
            "image_url": None,
            "enhanced_image_url": None,
            "facebook": {"copy": f"Discover {title} in the store.", "cta": "Shop Now"},
            "instagram": {"copy": f"Take a closer look at {title}.", "cta": "Shop Now"},
            "tiktok": {"copy": f"Check out {title} and see it in the store.", "cta": "Shop Now"},
            "email": {"subject": f"Discover {title}", "body": f"Take a closer look at {title} in the store."},
            "ad": {"primary_text": f"Discover {title}.", "headline": title[:255], "description": "Shop it in the store.", "cta": "Shop Now"},
            "creative_prompt": f"Create a clean commercial product photograph of {title}, preserving the exact product appearance from the supplied Shopify reference image. Do not invent features, packaging, labels, or text.",
            "hashtags": [],
        }
        selected_model = "deterministic-fallback"
        latency = 0.0
    raw["product_id"] = product.get("id") or ""
    raw["product_title"] = product.get("title") or "Product"
    images = product.get("images") or []
    if not raw.get("image_url") and images:
        raw["image_url"] = images[0].get("url") if isinstance(images[0], dict) else None
    # Enhance the actual Shopify image while preserving the source product.
    raw["enhanced_image_url"] = None
    raw["image_enhancement_source"] = None
    raw["image_enhancement_error"] = None
    raw["image_retry_after_seconds"] = None
    if raw.get("image_url"):
        try:
            enhanced_prompt = (
                "Edit and enhance this exact Shopify product photo for ecommerce marketing. "
                "Preserve the exact product identity, shape, packaging, logo, label text, colors, "
                "proportions, and real details. Improve lighting, sharpness, clarity, exposure, "
                "color balance, and professional presentation. Do not redesign, replace, add, remove, "
                "or invent product features. Return the same product, professionally enhanced."
            )
            enhancer = image_ai or OpenRouterService()
            enhanced, _, _ = await enhancer.generate_image(
                enhanced_prompt,
                reference_url=raw["image_url"],
            )
            raw["enhanced_image_url"] = enhanced
            raw["image_enhancement_source"] = "openrouter"
        except OpenRouterError:
            # Keep the original Shopify image safe and use the local enhancement fallback.
            try:
                raw["enhanced_image_url"] = await enhance_product_image(raw["image_url"])
                raw["image_enhancement_source"] = "local"
            except ProductImageError:
                raw["enhanced_image_url"] = None
            if raw.get("enhanced_image_url") is None:
                raw["image_enhancement_error"] = "Image enhancement was unavailable; the original Shopify image is retained."
    # Normalize optional channel fields so one malformed response cannot fail the campaign.
    def _channel(name: str, defaults: dict[str, str]) -> dict[str, str]:
        value = raw.get(name)
        if not isinstance(value, dict):
            value = {}
        return {k: str(value.get(k) or defaults[k]) for k in defaults}

    raw["facebook"] = _channel("facebook", {"copy": f"Discover {raw.get('product_title') or product.get('title') or 'this product'}.", "cta": "Shop Now"})
    raw["instagram"] = _channel("instagram", {"copy": f"Discover {raw.get('product_title') or product.get('title') or 'this product'}.", "cta": "Shop Now"})
    raw["tiktok"] = _channel("tiktok", {"copy": f"Take a closer look at {raw.get('product_title') or product.get('title') or 'this product'}.", "cta": "Shop Now"})
    raw["email"] = _channel("email", {"subject": f"Discover {raw.get('product_title') or product.get('title') or 'this product'}", "body": f"Take a closer look at {raw.get('product_title') or product.get('title') or 'this product'} in the store."})
    raw["ad"] = _channel("ad", {"primary_text": f"Discover {raw.get('product_title') or product.get('title') or 'this product'}.", "headline": str(raw.get('product_title') or product.get('title') or 'Shop now'), "description": "Discover it in store.", "cta": "Shop Now"})
    raw["creative_prompt"] = str(raw.get("creative_prompt") or f"Create a clean commercial product photograph of {product.get('title') or 'the Shopify product'}, preserving the actual product appearance from the supplied reference image. No invented features or text.")
    raw["hashtags"] = raw.get("hashtags") if isinstance(raw.get("hashtags"), list) else []
    try:
        result = MarketingResult.model_validate(raw)
    except Exception as exc:
        raise OpenRouterError("AI marketing result failed schema validation") from exc
    return result, selected_model, latency
