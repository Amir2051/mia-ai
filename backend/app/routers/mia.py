from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from app.auth.dependencies import CurrentUser, get_optional_shop
from app.models.database import get_db
from app.models.schemas import Shop
from app.security.tokens import decrypt_token
from app.shopify.client import ShopifyAPIClient, ShopifyAPIError
from app.services.products import ProductService
from app.services.customers import CustomerService
from app.services.orders import OrderService

router = APIRouter()

class ChatRequest(BaseModel):
    message: str

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

async def _context(shop: Shop) -> Dict[str, Any]:
    client = _client(shop)
    products_data: Dict[str, Any] = {}
    orders_data: Dict[str, Any] = {}
    customers_data: Dict[str, Any] = {}
    warnings = []
    try:
        products_data = await ProductService(client).list_products(first=250)
    except ShopifyAPIError as exc:
        warnings.append("Products could not be loaded")
    try:
        orders_data = await OrderService(client).list_orders(first=50)
    except ShopifyAPIError:
        warnings.append("Orders could not be loaded")
    try:
        customers_data = await CustomerService(client).list_customers(first=50)
    except ShopifyAPIError:
        warnings.append("Customer data is unavailable until Shopify grants the required protected-data access")
    products = _nodes(products_data, "products")
    orders = _nodes(orders_data, "orders")
    customers = _nodes(customers_data, "customers")
    revenue = 0.0
    for order in orders:
        money = ((order.get("totalPriceSet") or {}).get("shopMoney") or {})
        try: revenue += float(money.get("amount") or 0)
        except (TypeError, ValueError): pass
    inventory = sum(int(p.get("totalInventory") or 0) for p in products)
    low_stock = [p for p in products if p.get("totalInventory") is not None and int(p.get("totalInventory") or 0) <= 5]
    product_rows = []
    for p in products:
        variants = []
        for edge in ((p.get("variants") or {}).get("edges") or []):
            v = edge.get("node") or {}
            variants.append({
                "sku": v.get("sku"),
                "price": v.get("price"),
                "inventory": v.get("inventoryQuantity"),
            })
        product_rows.append({
            "id": p.get("id"), "title": p.get("title"),
            "status": p.get("status"), "inventory": p.get("totalInventory"),
            "variants": variants,
        })
    return {
        "shop": shop.shop_domain,
        "counts": {"products": len(products), "orders": len(orders), "customers": len(customers), "inventory_units": inventory},
        "revenue": round(revenue, 2),
        "products": product_rows,
        "customers": [{"id": c.get("id"), "name": " ".join(x for x in [c.get("firstName"), c.get("lastName")] if x), "orders": c.get("numberOfOrders"), "spent": (c.get("amountSpent") or {}).get("amount")} for c in customers],
        "orders": [{"id": o.get("id"), "name": o.get("name"), "createdAt": o.get("createdAt"), "amount": ((o.get("totalPriceSet") or {}).get("shopMoney") or {}).get("amount")} for o in orders],
        "low_stock": [{"id": p.get("id"), "title": p.get("title"), "inventory": p.get("totalInventory")} for p in low_stock],
        "warnings": warnings,
    }

@router.get("/context")
async def mia_context(current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    shop = await _shop(current, db)
    return await _context(shop)

@router.post("/chat")
async def mia_chat(body: ChatRequest, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    shop = await _shop(current, db)
    ctx = await _context(shop)
    q = body.message.lower().strip()
    counts = ctx["counts"]
    if any(k in q for k in ["customer", "customers", "client"]):
        customers = ctx["customers"]
        if not customers:
            answer = "I can connect to this store, but Shopify is currently withholding protected customer records from Mia. Once the required customer-data access is approved, I can search and analyze those records here."
        else:
            top = sorted(customers, key=lambda x: float(x.get("spent") or 0), reverse=True)[:5]
            names = ", ".join((c["name"] or "Unnamed customer") for c in top)
            answer = f"I found {len(customers)} customer records in the current Mia data window. Highest-spend customers in that window: {names}."
    elif any(k in q for k in ["product", "products", "inventory", "stock", "analyz"]):
        products = ctx["products"]
        status_counts = {}
        priced = []
        for p in products:
            status = p.get("status") or "UNKNOWN"
            status_counts[status] = status_counts.get(status, 0) + 1
            for v in p.get("variants") or []:
                try:
                    priced.append(float(v.get("price")))
                except (TypeError, ValueError):
                    pass
        status_text = ", ".join(f"{k}: {v}" for k, v in sorted(status_counts.items())) or "none"
        answer = f"Here’s a live product snapshot for {ctx['shop']}: {counts['products']} products with {counts['inventory_units']} total inventory units. Status mix: {status_text}."
        if priced:
            answer += f" Variant prices currently range from ${min(priced):,.2f} to ${max(priced):,.2f}."
        if ctx["low_stock"]:
            answer += " Attention needed: " + ", ".join(f"{p['title']} ({p['inventory']} units)" for p in ctx["low_stock"][:5]) + "."
        else:
            answer += " I did not find products at or below the current 5-unit low-stock threshold."
        answer += " I can next break this down by product, SKU, price, inventory risk, or sales performance."
    elif any(k in q for k in ["order", "orders", "sales", "revenue", "money"]):
        answer = f"The current Shopify data window contains {counts['orders']} orders and ${ctx['revenue']:,.2f} in order value."
    elif any(k in q for k in ["what can you do", "help", "capabilities"]):
        answer = "I can work from this organization's live Shopify data: products, inventory, orders, and—when Shopify permits access—customers. I can surface low stock, summarize sales, identify customer patterns, and help turn store data into actions."
    else:
        answer = f"I’m connected to {ctx['shop']} and can analyze {counts['products']} products, {counts['orders']} orders, and {counts['customers']} customer records in the current data window. Ask me about products, inventory, orders, sales, or customers."
    return {"answer": answer, "data_used": {"shop": ctx["shop"], "counts": counts, "warnings": ctx["warnings"]}}
