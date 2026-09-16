from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.services.openrouter import OpenRouterError, OpenRouterService


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

    @field_validator("keywords", "tags", "image_alt_text")
    @classmethod
    def clean_lists(cls, values: list[str]) -> list[str]:
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
                    "Use concise, useful keywords and tags. optimized_description_html may use only p,strong,em,b,i,ul,ol,li,h2,h3,br,a."
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
        raw, selected_model, latency = await self.ai.chat_json(self.prompt(product), model=model)
        raw["optimized_description_html"] = sanitize_html(str(raw.get("optimized_description_html", "")))
        raw["handle_suggestion"] = normalize_handle(str(raw.get("handle_suggestion", "")))
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
