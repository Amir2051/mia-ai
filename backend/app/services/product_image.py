from __future__ import annotations

import base64
import io
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageEnhance, ImageFilter, ImageOps


class ProductImageError(RuntimeError):
    pass


_ALLOWED_HOST_SUFFIXES = ("cdn.shopify.com", "shopify.com", "myshopify.com")


def _allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return host == "cdn.shopify.com" or any(host.endswith("." + suffix) for suffix in _ALLOWED_HOST_SUFFIXES)


async def enhance_product_image(image_url: str) -> str:
    """Enhance a live Shopify product image without inventing or replacing the product."""
    if not _allowed_url(image_url):
        raise ProductImageError("Product image URL is not a trusted Shopify image host")

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(image_url)
        response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        raise ProductImageError("Unable to load the Shopify product image") from exc

    if len(response.content) > 12 * 1024 * 1024:
        raise ProductImageError("Shopify product image is too large to enhance")

    try:
        source = Image.open(io.BytesIO(response.content)).convert("RGB")
        source = ImageOps.exif_transpose(source)
    except Exception as exc:
        raise ProductImageError("Shopify product image is not a supported image") from exc

    width, height = source.size
    scale = min(2.0, 1600 / max(width, height)) if max(width, height) else 1.0
    if scale > 1.0:
        source = source.resize((round(width * scale), round(height * scale)), Image.Resampling.LANCZOS)

    # Improve the supplied asset only: preserve its composition and product identity.
    enhanced = ImageOps.autocontrast(source, cutoff=1)
    enhanced = ImageEnhance.Color(enhanced).enhance(1.06)
    enhanced = ImageEnhance.Contrast(enhanced).enhance(1.04)
    enhanced = ImageEnhance.Sharpness(enhanced).enhance(1.18)
    enhanced = enhanced.filter(ImageFilter.UnsharpMask(radius=1.0, percent=110, threshold=3))

    output = io.BytesIO()
    enhanced.save(output, format="JPEG", quality=92, optimize=True)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"
