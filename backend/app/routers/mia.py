from datetime import datetime, timezone
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.dependencies import CurrentUser, get_optional_shop
from app.models.database import get_db
from app.models.schemas import Shop
from app.security.tokens import decrypt_token
from app.shopify.client import ShopifyAPIClient, ShopifyAPIError
from app.services.products import ProductService
from app.services.customers import CustomerService
from app.services.orders import OrderService
from app.services.openrouter import OpenRouterError, OpenRouterService
from app.services.seo import SEOService, generate_marketing

router = APIRouter()


class ChatRequest(BaseModel):
    message: str


class ApplyProductRequest(BaseModel):
    changes: Dict[str, Any] = Field(default_factory=dict)


async def _shop(current: Optional[CurrentUser], db) -> Shop:
    if not current:
        raise HTTPException(status_code=401, detail="Missing Shopify session")
    result = await db.execute(select(Shop).where(Shop.shop_domain == current.shop_domain))
    shop = result.scalar_one_or_none()
    if not shop or not shop.access_token_encrypted or not shop.is_active:
        raise HTTPException(status_code=400, detail="Shopify is not connected")
    return shop


def _client(shop: Shop) -> ShopifyAPIClient:
    try:
        token = decrypt_token(shop.access_token_encrypted or "")
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail="Stored Shopify access token could not be decrypted") from exc
    return ShopifyAPIClient(shop_domain=shop.shop_domain, access_token=token)


def _nodes(data: Dict[str, Any], key: str):
    return [e.get("node", {}) for e in (((data.get(key) or {}).get("edges")) or [])]


def _plain(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.I)
    value = re.sub(r"</p\s*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(value.split())


def _age_days(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except ValueError:
        return None


async def _all_products(shop: Shop) -> list[Dict[str, Any]]:
    client = _client(shop)
    products: list[Dict[str, Any]] = []
    after: Optional[str] = None
    # Shopify returns at most 250 records per page. Continue until the catalog is exhausted.
    for _ in range(100):
        data = await ProductService(client).list_products(first=250, after=after)
        connection = data.get("products") or {}
        products.extend(_nodes(data, "products"))
        page = connection.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        next_cursor = page.get("endCursor")
        if not next_cursor or next_cursor == after:
            break
        after = next_cursor
    return products


async def _context(shop: Shop) -> Dict[str, Any]:
    warnings: list[str] = []
    try:
        products = await _all_products(shop)
    except ShopifyAPIError:
        products = []
        warnings.append("Products could not be loaded")
    try:
        orders = _nodes(await OrderService(_client(shop)).list_orders(first=50), "orders")
    except ShopifyAPIError:
        orders = []
        warnings.append("Orders could not be loaded")
    try:
        customers = _nodes(await CustomerService(_client(shop)).list_customers(first=50), "customers")
    except ShopifyAPIError:
        customers = []
        warnings.append("Customer data is unavailable until Shopify grants the required protected-data access")

    revenue = 0.0
    for order in orders:
        money = ((order.get("totalPriceSet") or {}).get("shopMoney") or {})
        try:
            revenue += float(money.get("amount") or 0)
        except (TypeError, ValueError):
            pass

    inventory = sum(int(p.get("totalInventory") or 0) for p in products)
    low_stock = [p for p in products if p.get("totalInventory") is not None and int(p.get("totalInventory") or 0) <= 5]
    product_rows = []
    for p in products:
        variants = []
        for edge in ((p.get("variants") or {}).get("edges") or (p.get("variants") or {}).get("nodes") or []):
            v = edge.get("node") if "node" in edge else edge
            variants.append({"sku": v.get("sku"), "price": v.get("price"), "inventory": v.get("inventoryQuantity")})
        product_rows.append({
            "id": p.get("id"), "title": p.get("title"), "status": p.get("status"),
            "inventory": p.get("totalInventory"), "description": _plain(p.get("descriptionHtml") or ""),
            "tags": p.get("tags") or [], "vendor": p.get("vendor"), "productType": p.get("productType"),
            "createdAt": p.get("createdAt"), "updatedAt": p.get("updatedAt"), "variants": variants,
        })

    return {
        "shop": shop.shop_domain,
        "counts": {"products": len(products), "orders": len(orders), "customers": len(customers), "inventory_units": inventory},
        "revenue": round(revenue, 2),
        "products": product_rows,
        "customers": [{"id": c.get("id"), "name": " ".join(x for x in [c.get("firstName"), c.get("lastName")] if x), "orders": c.get("numberOfOrders"), "spent": (c.get("amountSpent") or {}).get("amount")} for c in customers],
        "orders": [{"id": o.get("id"), "name": o.get("name"), "createdAt": o.get("createdAt"), "amount": ((o.get("totalPriceSet") or {}).get("shopMoney") or {}).get("amount")} for o in orders],
        "low_stock": [{"id": p.get("id"), "title": p.get("title"), "inventory": p.get("inventory")} for p in product_rows if p.get("inventory") is not None and int(p.get("inventory") or 0) <= 5],
        "warnings": warnings,
    }


def _variants(product: Dict[str, Any]) -> list[Dict[str, Any]]:
    raw = product.get("variants") or {}
    values = raw.get("edges") or raw.get("nodes") or []
    return [(item.get("node") if isinstance(item, dict) and "node" in item else item) or {} for item in values]


def _product_analysis(product: Dict[str, Any]) -> Dict[str, Any]:
    description = _plain(product.get("descriptionHtml") or product.get("description") or "")
    title = (product.get("title") or "").strip()
    tags = product.get("tags") or []
    if isinstance(tags, str):
        tags = [x.strip() for x in tags.split(",") if x.strip()]
    variants = _variants(product)
    prices = []
    for v in variants:
        try: prices.append(float(v.get("price")))
        except (TypeError, ValueError): pass
    inventory = product.get("totalInventory", product.get("inventory"))
    issues = []
    opportunities = []
    if len(title) < 25: issues.append("Title is short and may miss useful search intent")
    if len(title) > 70: issues.append("Title is long and may be truncated in search")
    if len(description) < 120: issues.append("Description is thin; add benefits, use cases, and buying details")
    if len(description) >= 120: opportunities.append("Description has enough substance for benefit-led optimization")
    if len(tags) < 3: issues.append("Product has few discoverability tags")
    if inventory is not None and int(inventory or 0) <= 5: issues.append("Inventory is at or below the low-stock threshold")
    if prices: opportunities.append(f"Current variant pricing spans ${min(prices):,.2f}–${max(prices):,.2f}")
    if not issues: opportunities.append("No major catalog-quality issues were detected by the current heuristic checks")
    score = max(0, 100 - len(issues) * 15 + min(20, len(tags) * 2))
    return {"quality_score": score, "title_length": len(title), "description_length": len(description), "tag_count": len(tags), "inventory": inventory, "price_range": prices and [min(prices), max(prices)], "issues": issues, "opportunities": opportunities}


def _enhancement(product: Dict[str, Any]) -> Dict[str, Any]:
    title = (product.get("title") or "Product").strip()
    description = _plain(product.get("descriptionHtml") or product.get("description") or "")
    vendor = product.get("vendor") or ""
    product_type = product.get("productType") or ""
    tags = product.get("tags") or []
    if isinstance(tags, str): tags = [x.strip() for x in tags.split(",") if x.strip()]
    words = re.findall(r"[A-Za-z0-9]+", title.lower())
    inferred = [w for w in words if len(w) > 3 and w not in {"with", "from", "your", "this"}]
    new_tags = list(dict.fromkeys(tags + inferred[:5] + ([product_type.lower()] if product_type else [])))[:15]
    improved_title = title if len(title) >= 25 else (f"{title} — Premium Quality" if title else "Premium Product")
    if description:
        improved_description = f"<p><strong>{improved_title}</strong></p><p>{description}</p><p>Designed to deliver a clear, reliable experience with practical value for everyday use. Explore the details, choose the option that fits your needs, and shop with confidence.</p>"
    else:
        improved_description = f"<p><strong>{improved_title}</strong></p><p>Discover {improved_title}{(' from ' + vendor) if vendor else ''}. Built for customers who value quality, practical design, and dependable everyday performance.</p><p>Explore the available options and find the right fit for your needs.</p>"
    return {"title": improved_title, "descriptionHtml": improved_description, "tags": new_tags, "productType": product_type or None, "positioning": f"Lead with the core value of {improved_title}, then reinforce quality, use case, and buyer confidence."}


def _marketing(product: Dict[str, Any]) -> Dict[str, Any]:
    title = product.get("title") or "this product"
    price = None
    variants = _variants(product)
    if variants: price = (variants[0].get("price") or None)
    hook = f"Discover {title}{' for $' + str(price) if price else ''}."
    return {
        "facebook": {"channel": "Facebook", "copy": f"{hook} See what makes it a great fit for your everyday needs. Shop now.", "cta": "Shop Now"},
        "instagram": {"channel": "Instagram", "copy": f"✨ {hook} Quality, useful design, and a product worth discovering. #ShopNow #{re.sub(r'[^A-Za-z0-9]', '', title)[:20]}", "cta": "Shop Now"},
        "tiktok": {"channel": "TikTok", "copy": f"POV: you just found your next favorite product 👀 {hook} Tap to see it.", "cta": "Shop Now"},
        "email": {"channel": "Email", "subject": f"Meet {title}", "body": f"Hi there,\n\n{hook}\n\nTake a closer look and see whether it belongs in your next order.\n\nShop now."},
        "ad": {"channel": "Paid Ad", "primary_text": f"{hook} Explore the product and shop directly from your store.", "headline": title, "description": "Discover it today.", "cta": "Shop Now"},
    }


@router.get("/context")
async def mia_context(current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    return await _context(await _shop(current, db))


@router.get("/product/{product_id:path}/analyze")
async def analyze_product(product_id: str, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    shop = await _shop(current, db)
    try:
        product = (await ProductService(_client(shop)).get_product(product_id)).get("product") or {}
    except ShopifyAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    if not product.get("id"):
        raise HTTPException(status_code=404, detail="Product not found in the connected Shopify store")
    return {"connected": True, "product": product, "analysis": _product_analysis(product)}


@router.get("/product/{product_id:path}/enhance")
async def enhance_product(product_id: str, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    shop = await _shop(current, db)
    product = (await ProductService(_client(shop)).get_product(product_id)).get("product") or {}
    if not product.get("id"): raise HTTPException(status_code=404, detail="Product not found in the connected Shopify store")
    return {"connected": True, "product_id": product_id, "proposal": _enhancement(product), "marketing": _marketing(product), "analysis": _product_analysis(product)}


@router.post("/product/{product_id:path}/apply")
async def apply_product(product_id: str, body: ApplyProductRequest, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    shop = await _shop(current, db)
    allowed = {"title", "descriptionHtml", "tags", "vendor", "productType", "status"}
    changes = {k: v for k, v in body.changes.items() if k in allowed and v is not None}
    if not changes: raise HTTPException(status_code=400, detail="No approved Shopify product changes were supplied")
    try:
        result = await ProductService(_client(shop)).update_product(product_id, changes)
    except ShopifyAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return {"connected": True, "applied": changes, "data": result}


@router.get("/product/{product_id:path}/marketing")
async def product_marketing(product_id: str, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    shop = await _shop(current, db)
    product = (await ProductService(_client(shop)).get_product(product_id)).get("product") or {}
    if not product.get("id"): raise HTTPException(status_code=404, detail="Product not found in the connected Shopify store")
    return {"connected": True, "product_id": product_id, "marketing": _marketing(product)}


@router.post("/chat")
async def mia_chat(body: ChatRequest, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    shop = await _shop(current, db)
    ctx = await _context(shop)
    q = body.message.lower().strip()
    products = ctx["products"]

    # Real AI product workflows from chat. A product name in the request selects
    # the matching live Shopify product; otherwise the first catalog product is used.
    target = None
    selected_match = re.search(r"\[Selected Shopify product:\s*([^\]]+)\]", body.message, flags=re.I)
    if selected_match:
        selected_id = selected_match.group(1).strip()
        target = next((p for p in products if p.get("id") == selected_id), None)
    for p in products if target is None else []:
        title = (p.get("title") or "").lower()
        if title and title in q:
            target = p
            break
    if target is None and products:
        words = [w for w in re.findall(r"[a-z0-9]+", q) if len(w) >= 5]
        scored = sorted(((sum(1 for w in words if w in (p.get("title") or "").lower()), p) for p in products), key=lambda x: x[0], reverse=True)
        if scored and scored[0][0] > 0:
            target = scored[0][1]

    if any(k in q for k in ["seo", "search engine", "meta description", "meta title"]) and target:
        try:
            product_full = (await ProductService(_client(shop)).get_product(target["id"])).get("product") or {}
            seo_result, model, latency = await SEOService(OpenRouterService()).generate(product_full)
            return {"answer": f"I generated a real SEO package for {product_full.get('title')}. It is ready for review in the SEO workspace before applying to Shopify.", "artifact": {"type": "seo", "product": product_full, "result": seo_result.model_dump(), "model": model, "latency_ms": round(latency, 2)}, "data_used": {"shop": ctx["shop"], "catalog_scanned": len(products)}}
        except (ShopifyAPIError, OpenRouterError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    if any(k in q for k in ["facebook", "instagram", "tiktok", "marketing", "ready to post", "ad copy", "advertisement"]) and target:
        try:
            product_full = (await ProductService(_client(shop)).get_product(target["id"])).get("product") or {}
            marketing_result, model, latency = await generate_marketing(OpenRouterService(), product_full)
            return {"answer": f"I generated a ready-to-post marketing package for {product_full.get('title')} using its live Shopify product data and image.", "artifact": {"type": "marketing", "product": product_full, "result": marketing_result.model_dump(), "model": model, "latency_ms": round(latency, 2)}, "data_used": {"shop": ctx["shop"], "catalog_scanned": len(products)}}
        except (ShopifyAPIError, OpenRouterError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    if any(k in q for k in ["low inventory", "low stock", "out of stock", "stock"]):
        rows = [p for p in products if p.get("inventory") is not None and int(p.get("inventory") or 0) <= 5]
        answer = f"I scanned all {len(products)} products in {ctx['shop']}. {len(rows)} are at or below 5 units. " + ("; ".join(f"{p['title']} ({p['inventory']})" for p in rows[:12]) if rows else "No products meet that threshold.")
    elif any(k in q for k in ["recent", "recently imported", "new products", "newly added"]):
        dated = [p for p in products if p.get("createdAt")]
        dated.sort(key=lambda p: p.get("createdAt") or "", reverse=True)
        answer = f"I scanned the full catalog. The newest products by Shopify created date are: " + "; ".join(f"{p['title']} ({p.get('createdAt','')[:10]})" for p in dated[:10])
    elif any(k in q for k in ["weak description", "poor description", "thin description", "product quality", "quality"]):
        weak = [p for p in products if len(p.get("description") or "") < 120]
        answer = f"I found {len(weak)} products with descriptions under 120 characters out of {len(products)} total. " + ("Examples: " + "; ".join(p["title"] for p in weak[:10]) + "." if weak else "The catalog descriptions clear that basic threshold.")
    elif any(k in q for k in ["advertise", "advertising", "marketing", "promote"]):
        candidates = [p for p in products if (p.get("status") == "ACTIVE" and (p.get("inventory") or 0) > 5)]
        candidates.sort(key=lambda p: len(p.get("description") or ""), reverse=True)
        answer = f"For advertising, I found {len(candidates)} active products with more than 5 units available. I can generate channel-specific Facebook, Instagram, TikTok, email, and paid-ad copy for any of them."
    elif any(k in q for k in ["product", "products", "inventory", "analyz"]):
        statuses: Dict[str, int] = {}
        for p in products: statuses[p.get("status") or "UNKNOWN"] = statuses.get(p.get("status") or "UNKNOWN", 0) + 1
        status_text = ", ".join(f"{k}: {v}" for k, v in sorted(statuses.items()))
        answer = f"I scanned the entire Shopify catalog: {len(products)} products and {ctx['counts']['inventory_units']} inventory units. Status mix: {status_text}. {len(ctx['low_stock'])} products are at or below 5 units. Ask me to find weak descriptions, recent products, advertising candidates, or analyze a specific product."
    elif any(k in q for k in ["customer", "customers", "client"]):
        customers = ctx["customers"]
        answer = f"I found {len(customers)} customer records in the current Shopify data window." if customers else "Shopify is currently withholding protected customer records from Mia."
    elif any(k in q for k in ["help", "what can you do", "capabilities"]):
        answer = "I’m Mia, your Shopify product copilot. I can scan the full catalog, analyze product quality, find low-stock or recent products, propose product enhancements, generate marketing, and apply merchant-approved product changes back to Shopify."
    else:
        try:
            ai = OpenRouterService()
            answer, model, latency = await ai.chat_text([
                {"role": "system", "content": "You are Mia, a Shopify marketing assistant. Use only the supplied live store context. Do not claim an action was applied unless the backend explicitly says so. For SEO requests, direct the user to the dedicated SEO generation and confirmation flow."},
                {"role": "user", "content": f"Store={ctx['shop']}\nCatalog={products[:25]}\nRequest={body.message}"},
            ])
            return {"answer": answer, "model": model, "latency_ms": round(latency, 2), "data_used": {"shop": ctx["shop"], "counts": ctx["counts"], "catalog_scanned": len(products), "warnings": ctx["warnings"]}}
        except OpenRouterError:
            answer = f"I’m connected to {ctx['shop']} and scanned {len(products)} products. Try: ‘show low inventory’, ‘find weak descriptions’, ‘find recently imported products’, ‘which products should I advertise?’, or ‘improve this product’."
    return {"answer": answer, "data_used": {"shop": ctx["shop"], "counts": ctx["counts"], "catalog_scanned": len(products), "warnings": ctx["warnings"]}}
