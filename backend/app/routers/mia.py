from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.dependencies import CurrentUser, get_optional_shop
from app.models.database import get_db
from app.models.schemas import MiaMemory, Shop, SocialAccount
from app.security.rls import set_shop_context
from app.security.tokens import decrypt_token
from app.services.meta import MetaAPIError, MetaClient
from app.shopify.http_errors import shopify_http_error
from app.shopify.client import ShopifyAPIClient, ShopifyAPIError
from app.services.products import ProductService
from app.services.customers import CustomerService
from app.services.orders import OrderService
from app.services.openrouter import OpenRouterError, OpenRouterService
from app.services.seo import SEOService, generate_marketing

router = APIRouter()


class ChatTurn(BaseModel):
    role: str
    content: str = ""


class ChatRequest(BaseModel):
    message: str
    product_id: Optional[str] = None
    history: list[ChatTurn] = Field(default_factory=list)


class ApplyProductRequest(BaseModel):
    changes: Dict[str, Any] = Field(default_factory=dict)


class ConversationMessage(BaseModel):
    role: str
    content: str = ""


class ConversationUpdate(BaseModel):
    messages: list[ConversationMessage] = Field(default_factory=list)


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


async def _all_products(shop: Shop, warnings: Optional[list[str]] = None) -> list[Dict[str, Any]]:
    client = _client(shop)
    products: list[Dict[str, Any]] = []
    after: Optional[str] = None
    # Shopify returns at most 250 records per page. Continue until the catalog is exhausted.
    for page_number in range(100):
        data = await ProductService(client).list_products(first=250, after=after)
        connection = data.get("products") or {}
        products.extend(_nodes(data, "products"))
        page = connection.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        next_cursor = page.get("endCursor")
        if not next_cursor or next_cursor == after:
            if warnings is not None:
                warnings.append("Product pagination stopped because Shopify did not return a new cursor; catalog coverage may be incomplete.")
            break
        if page_number == 99 and warnings is not None:
            warnings.append("Product catalog reached the 25,000-product safety limit; results may be incomplete.")
        after = next_cursor
    return products


def _context_plan(message: str, selected_id: Optional[str] = None) -> Dict[str, bool]:
    """Choose only the Shopify datasets needed to answer the current question."""
    q = (message or "").lower()
    broad_overview = bool(re.search(
        r"\b(store overview|business overview|store summary|summary of (?:my|our) store|overview of (?:my|our) store|how is (?:my|our) store doing|store health|overall performance)\b",
        q,
    ))
    product_intent = bool(re.search(
        r"\b(products?|catalog|inventory|stock|sku|price|description|seo|product page|listing|best[- ]selling|top[- ]selling|best sellers?|top sellers?)\b",
        q,
    ) or (selected_id and _references_selected_product(q)) or (_is_explicit_content_request(message) and (SEO_WORDS.search(q) or MARKETING_WORDS.search(q))))
    order_intent = bool(re.search(
        r"\b(orders?|revenue|paid|unpaid|refund(?:s|ed)?|cancel(?:led|ed)?|fulfillment|average order value|aov|best[- ]selling|top[- ]selling|best sellers?|top sellers?|repeat purchase|sold|sell|selling|transactions|sales(?!\s+channels?))\b",
        q,
    ))
    customer_intent = bool(re.search(
        r"\b(customers?|buyers?|customer lifetime value|returning customers?|repeat buyers?|loyal customers?)\b",
        q,
    ))
    publication_intent = bool(re.search(
        r"\b(sales channels?|publishing channels?|where (?:is|are) .{0,40}published|publish(?:ing)? channels?|online store channel)\b",
        q,
    ))

    if broad_overview or not any((product_intent, order_intent, customer_intent, publication_intent)):
        return {"products": True, "orders": True, "customers": True, "publications": True}

    # Customer purchase/spend questions need both customer and order records.
    if customer_intent and re.search(r"\b(spent|spend|purchases?|orders?|lifetime value|repeat|returning)\b", q):
        order_intent = True

    return {
        "products": product_intent,
        "orders": order_intent,
        "customers": customer_intent,
        "publications": publication_intent,
    }


def _order_date_filter(
    message: str,
    timezone_name: str = "UTC",
    now: Optional[datetime] = None,
) -> tuple[str, str, Optional[datetime]]:
    """Build a Shopify created_at filter for common date-range questions."""
    q = (message or "").lower()
    try:
        store_tz = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        store_tz = ZoneInfo("UTC")
        timezone_name = "UTC"
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_now = current.astimezone(store_tz)
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    label = "all order history available to this app"

    explicit = re.search(
        r"\b(?:between|from)\s+(\d{4}-\d{2}-\d{2})\s+(?:and|to|through|-)\s+(\d{4}-\d{2}-\d{2})\b",
        q,
    )
    if explicit:
        try:
            first_day = datetime.strptime(explicit.group(1), "%Y-%m-%d").date()
            last_day = datetime.strptime(explicit.group(2), "%Y-%m-%d").date()
            if first_day > last_day:
                return "", "invalid date range", None
            start = datetime.combine(first_day, datetime.min.time(), tzinfo=store_tz)
            end = datetime.combine(last_day + timedelta(days=1), datetime.min.time(), tzinfo=store_tz)
            label = f"{first_day.isoformat()} through {last_day.isoformat()} ({timezone_name})"
        except ValueError:
            return "", "invalid date range", None
    elif re.search(r"\byesterday\b", q):
        day = local_now.date() - timedelta(days=1)
        start = datetime.combine(day, datetime.min.time(), tzinfo=store_tz)
        end = start + timedelta(days=1)
        label = f"yesterday, {day.isoformat()} ({timezone_name})"
    elif re.search(r"\btoday\b", q):
        start = datetime.combine(local_now.date(), datetime.min.time(), tzinfo=store_tz)
        end = local_now
        label = f"today, {local_now.date().isoformat()} so far ({timezone_name})"
    elif re.search(r"\b(?:last|past)\s+(?:7|seven)\s+days?\b", q):
        start = local_now - timedelta(days=7)
        end = local_now
        label = f"last 7 rolling days ({timezone_name})"
    elif re.search(r"\b(?:last|past)\s+(?:30|thirty)\s+days?\b", q):
        start = local_now - timedelta(days=30)
        end = local_now
        label = f"last 30 rolling days ({timezone_name})"
    elif re.search(r"\bthis week\b", q):
        monday = local_now.date() - timedelta(days=local_now.weekday())
        start = datetime.combine(monday, datetime.min.time(), tzinfo=store_tz)
        end = local_now
        label = f"this week since {monday.isoformat()} ({timezone_name})"
    elif re.search(r"\blast week\b", q):
        monday = local_now.date() - timedelta(days=local_now.weekday() + 7)
        sunday_after = monday + timedelta(days=7)
        start = datetime.combine(monday, datetime.min.time(), tzinfo=store_tz)
        end = datetime.combine(sunday_after, datetime.min.time(), tzinfo=store_tz)
        label = f"last calendar week, {monday.isoformat()} through {(sunday_after - timedelta(days=1)).isoformat()} ({timezone_name})"
    elif re.search(r"\bthis month\b", q):
        first_day = local_now.date().replace(day=1)
        start = datetime.combine(first_day, datetime.min.time(), tzinfo=store_tz)
        end = local_now
        label = f"this month since {first_day.isoformat()} ({timezone_name})"
    elif re.search(r"\blast month\b", q):
        first_this_month = local_now.date().replace(day=1)
        last_month_end = first_this_month
        last_month_start = (first_this_month - timedelta(days=1)).replace(day=1)
        start = datetime.combine(last_month_start, datetime.min.time(), tzinfo=store_tz)
        end = datetime.combine(last_month_end, datetime.min.time(), tzinfo=store_tz)
        label = f"last calendar month, {last_month_start.isoformat()} through {(last_month_end - timedelta(days=1)).isoformat()} ({timezone_name})"

    if start is None or end is None:
        return "", label, None
    if start > end:
        return "", "invalid date range", None
    start_utc = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if "rolling" in label or label.startswith("today"):
        query = f"created_at:>='{start_utc}' created_at:<='{end_utc}'"
    else:
        query = f"created_at:>='{start_utc}' created_at:<'{end_utc}'"
    return query, label, start


def _has_order_date_reference(message: str) -> bool:
    return bool(re.search(
        r"\b(yesterday|today|(?:last|past)\s+(?:7|seven|30|thirty)\s+days?|this week|last week|this month|last month|(?:between|from)\s+\d{4}-\d{2}-\d{2})\b",
        message or "",
        re.I,
    ))


async def _context(
    shop: Shop,
    message: str = "",
    selected_id: Optional[str] = None,
    intent_message: Optional[str] = None,
    date_message: Optional[str] = None,
) -> Dict[str, Any]:
    # Use recent user intent to choose datasets, but use only the current message
    # to parse relative dates such as "what about last month?".
    requested = _context_plan(intent_message or message, selected_id)
    available = {key: False for key in requested}
    warnings: list[str] = []
    raw_products: list[Dict[str, Any]] = []
    orders: list[Dict[str, Any]] = []
    customers: list[Dict[str, Any]] = []
    publications: list[Dict[str, Any]] = []

    if requested["products"]:
        try:
            raw_products = await _all_products(shop, warnings)
            available["products"] = True
        except ShopifyAPIError as exc:
            if exc.status_code == 401:
                raise shopify_http_error(exc) from exc
            warnings.append("Product data could not be loaded; product counts and inventory are unavailable.")

    order_range = "not queried"
    order_query = ""
    order_range_start = None
    if requested["orders"]:
        try:
            order_query, order_range, order_range_start = _order_date_filter(date_message or message)
            if order_query:
                try:
                    timezone_data = await _client(shop).graphql("query MiaStoreTimezone { shop { ianaTimezone } }")
                    store_timezone = str((timezone_data.get("shop") or {}).get("ianaTimezone") or "UTC")
                    order_query, order_range, order_range_start = _order_date_filter(date_message or message, store_timezone)
                except ShopifyAPIError as timezone_exc:
                    if timezone_exc.status_code == 401:
                        raise shopify_http_error(timezone_exc) from timezone_exc
                    warnings.append("Shopify store timezone could not be read; the requested date range was interpreted in UTC.")
            granted_scopes = {scope.strip() for scope in (getattr(shop, "scope", None) or "").split(",") if scope.strip()}
            if "read_all_orders" not in granted_scopes:
                if order_range_start is None and order_range != "invalid date range":
                    warnings.append("No date range was requested. Without read_all_orders, Shopify limits this app to recent order history (typically 60 days), so these totals are not all-time totals.")
                elif order_range_start is not None:
                    sixty_days_ago = datetime.now(order_range_start.tzinfo).date() - timedelta(days=60)
                    if order_range_start.date() < sixty_days_ago:
                        warnings.append("This app is not granted read_all_orders. Shopify restricts accessible order history to the most recent 60 days, so this requested date range may be incomplete.")
            if order_range == "invalid date range":
                warnings.append("The requested date range is invalid; no orders were queried.")
                available["orders"] = False
            else:
                order_service = OrderService(_client(shop))
                after = None
                for page_number in range(100):
                    page_data = await order_service.list_orders(query=order_query, first=250, after=after)
                    connection = page_data.get("orders") or {}
                    orders.extend(_nodes(page_data, "orders"))
                    page_info = connection.get("pageInfo") or {}
                    if not page_info.get("hasNextPage"):
                        break
                    next_cursor = page_info.get("endCursor")
                    if not next_cursor or next_cursor == after:
                        warnings.append("Order pagination stopped because Shopify did not return a new cursor; sales totals may be incomplete.")
                        break
                    if page_number == 99:
                        warnings.append("Order history reached the 25,000-order safety limit; sales totals may be incomplete.")
                    after = next_cursor
                available["orders"] = True
        except ShopifyAPIError as exc:
            if exc.status_code == 401:
                raise shopify_http_error(exc) from exc
            warnings.append("Order data could not be loaded; sales and order counts are unavailable.")

    if requested["customers"]:
        try:
            customer_service = CustomerService(_client(shop))
            after = None
            for page_number in range(100):
                page_data = await customer_service.list_customers(first=250, after=after)
                connection = page_data.get("customers") or {}
                customers.extend(_nodes(page_data, "customers"))
                page_info = connection.get("pageInfo") or {}
                if not page_info.get("hasNextPage"):
                    break
                next_cursor = page_info.get("endCursor")
                if not next_cursor or next_cursor == after:
                    warnings.append("Customer pagination stopped because Shopify did not return a new cursor; customer coverage may be incomplete.")
                    break
                if page_number == 99:
                    warnings.append("Customer records reached the 25,000-record safety limit; customer coverage may be incomplete.")
                after = next_cursor
            available["customers"] = True
        except ShopifyAPIError as exc:
            if exc.status_code == 401:
                raise shopify_http_error(exc) from exc
            warnings.append("Customer data is unavailable until Shopify grants the required protected-data access.")

    if requested["publications"]:
        try:
            publication_data = await _client(shop).list_publications(first=100)
            publications = _nodes(publication_data, "publications")
            available["publications"] = True
        except ShopifyAPIError as exc:
            if exc.status_code == 401:
                raise shopify_http_error(exc) from exc
            response = exc.response or {}
            errors = response.get("errors") or []
            if isinstance(errors, dict):
                errors = [errors]
            error_text = " ".join(
                str(item.get("message", ""))
                for item in errors
                if isinstance(item, dict)
            ).lower()
            if exc.status_code in (401, 403) or any(
                marker in error_text
                for marker in ("access denied", "not authorized", "read_publications", "scope")
            ):
                warnings.append("Sales channels could not be read: Shopify has not granted Mia the required read_publications permission. Reauthorize/reinstall Mia after confirming the app scope.")
            else:
                warnings.append("Sales-channel data could not be loaded from Shopify.")

    revenue = 0.0
    for order in orders:
        money = ((order.get("totalPriceSet") or {}).get("shopMoney") or {})
        try:
            revenue += float(money.get("amount") or 0)
        except (TypeError, ValueError):
            pass

    inventory = sum(int(product.get("totalInventory") or 0) for product in raw_products) if available["products"] else None
    product_rows = []
    for product in raw_products:
        variants = []
        for edge in ((product.get("variants") or {}).get("edges") or (product.get("variants") or {}).get("nodes") or []):
            variant = edge.get("node") if "node" in edge else edge
            variants.append({"sku": variant.get("sku"), "price": variant.get("price"), "inventory": variant.get("inventoryQuantity")})
        prices = []
        for variant in variants:
            try:
                prices.append(float(variant.get("price")))
            except (TypeError, ValueError):
                pass
        product_rows.append({
            "id": product.get("id"), "title": product.get("title"), "status": product.get("status"),
            "inventory": product.get("totalInventory"), "description": _plain(product.get("descriptionHtml") or ""),
            "tags": product.get("tags") or [], "vendor": product.get("vendor"), "productType": product.get("productType"),
            "price_min": min(prices) if prices else None, "price_max": max(prices) if prices else None,
            "createdAt": product.get("createdAt"), "updatedAt": product.get("updatedAt"), "variants": variants,
        })

    return {
        "shop": shop.shop_domain,
        "requested": requested,
        "available": available,
        "counts": {
            "products": len(product_rows) if available["products"] else None,
            "orders": len(orders) if available["orders"] else None,
            "customers": len(customers) if available["customers"] else None,
            "inventory_units": inventory,
        },
        "revenue": round(revenue, 2) if available["orders"] else None,
        "order_range": order_range,
        "order_range_start": order_range_start.isoformat() if order_range_start else None,
        "sales_channels": [
            {"id": item.get("id"), "name": item.get("name"), "supportsFuturePublishing": item.get("supportsFuturePublishing")}
            for item in publications
        ] if available["publications"] else [],
        "products": product_rows,
        "customers": [
            {"id": customer.get("id"), "name": " ".join(x for x in [customer.get("firstName"), customer.get("lastName")] if x), "orders": customer.get("numberOfOrders"), "spent": (customer.get("amountSpent") or {}).get("amount")}
            for customer in customers
        ],
        "orders": [{
            "id": order.get("id"), "name": order.get("name"), "createdAt": order.get("createdAt"),
            "amount": ((order.get("totalPriceSet") or {}).get("shopMoney") or {}).get("amount"),
            "currency": ((order.get("totalPriceSet") or {}).get("shopMoney") or {}).get("currencyCode"),
            "financial_status": order.get("displayFinancialStatus"),
            "fulfillment_status": order.get("displayFulfillmentStatus"),
            "line_items": [{"title": item.get("title"), "quantity": item.get("currentQuantity") if item.get("currentQuantity") is not None else item.get("quantity"), "sku": item.get("sku")} for item in _nodes(order, "lineItems")],
        } for order in orders],
        "low_stock": [
            {"id": product.get("id"), "title": product.get("title"), "inventory": product.get("inventory")}
            for product in product_rows
            if product.get("inventory") is not None and int(product.get("inventory") or 0) <= 5
        ],
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


@router.get("/conversation")
async def get_mia_conversation(current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    shop = await _shop(current, db)
    row = await _memory_row(db, shop)
    return {"messages": _json_list(row.conversation_json), "knowledge": _json_list(row.knowledge_json), "updated_at": row.updated_at.isoformat() if row.updated_at else None}


@router.put("/conversation")
async def save_mia_conversation(body: ConversationUpdate, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    shop = await _shop(current, db)
    row = await _memory_row(db, shop)
    row.conversation_json = json.dumps(_clean_conversation(body.messages), ensure_ascii=False)
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.flush()
    return {"saved": True, "message_count": len(_json_list(row.conversation_json)), "updated_at": row.updated_at.isoformat()}


@router.get("/context")
async def mia_context(
    include_orders: bool = Query(False),
    current: Optional[CurrentUser] = Depends(get_optional_shop),
    db=Depends(get_db),
):
    # Dashboard only needs product inventory. Mia's own snapshot optionally
    # includes order counts; neither view needs customer profiles or publications.
    message = "products inventory orders" if include_orders else "products inventory"
    return await _context(await _shop(current, db), message)


@router.get("/product/{product_id:path}/analyze")
async def analyze_product(product_id: str, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    shop = await _shop(current, db)
    try:
        product = (await ProductService(_client(shop)).get_product(product_id)).get("product") or {}
    except ShopifyAPIError as exc:
        raise shopify_http_error(exc) from exc
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
        raise shopify_http_error(exc) from exc
    return {"connected": True, "applied": changes, "data": result}


@router.get("/product/{product_id:path}/marketing")
async def product_marketing(product_id: str, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    shop = await _shop(current, db)
    product = (await ProductService(_client(shop)).get_product(product_id)).get("product") or {}
    if not product.get("id"): raise HTTPException(status_code=404, detail="Product not found in the connected Shopify store")
    return {"connected": True, "product_id": product_id, "marketing": _marketing(product)}


SELECTED_TAG = re.compile(r"\n?\[Selected Shopify product:\s*([^\]]+)\]", re.I)
ACTION_VERBS = re.compile(r"\b(write|generate|create|make|draft|build|produce|give me|run)\b", re.I)
SEO_WORDS = re.compile(r"\b(seo|meta title|meta description|search engine)\b", re.I)
MARKETING_WORDS = re.compile(r"\b(facebook|instagram|tiktok|marketing|campaign|ad copy|ads?|posts?|captions?|email)\b", re.I)
EXPLANATION_PATTERNS = (
    re.compile(r"\b(what is|what are|explain|define|teach me|how does|how do i|what does)\b", re.I),
    re.compile(r"\b(why|when should|difference between|help me understand)\b", re.I),
)


def _is_explanation_request(q: str) -> bool:
    return any(pattern.search(q) for pattern in EXPLANATION_PATTERNS)


def _is_explicit_content_request(q: str) -> bool:
    return bool(ACTION_VERBS.search(q) and not _is_explanation_request(q))


STORE_OWNERSHIP_PATTERNS = re.compile(
    r"\b(my|our|this)\s+(store|shop|catalog|inventory|orders?|sales|customers?|products?)\b",
    re.I,
)
STORE_CONTEXT_PATTERNS = re.compile(
    r"\b(orders?|sales|revenue|customers?|products?|catalog|inventory|low[- ]stock|out of stock|"
    r"best[- ]selling|top[- ]selling|recent sales|sales channels?|order status|order value|"
    r"average order value|how many products|list products|show products|product inventory|inventory units)\b",
    re.I,
)


def _needs_store_context(message: str, selected_id: Optional[str] = None) -> bool:
    """Only load the full Shopify context for store-specific or product-task requests."""
    q = (message or "").strip()
    has_store_reference = bool(STORE_OWNERSHIP_PATTERNS.search(q))
    has_store_data_intent = bool(STORE_CONTEXT_PATTERNS.search(q))
    if (
        _is_explanation_request(q)
        and not _references_selected_product(q)
        and not _is_explicit_content_request(q)
        and not has_store_reference
    ):
        return False
    if _is_explicit_content_request(q) and (SEO_WORDS.search(q) or MARKETING_WORDS.search(q)):
        return True
    if selected_id and _references_selected_product(q):
        return True
    return has_store_reference or has_store_data_intent


def _references_selected_product(q: str) -> bool:
    """Recognize product-context follow-ups without treating them as commands."""
    return bool(re.search(
        r"\b(this|that|selected|current|my|our)\s+(product|item|listing)\b"
        r"|\b(the product|the listing)\b"
        r"|\b(its|their)\s+(seo|title|description|tags?|price|inventory|conversion|quality|image|images|product page)\b"
        r"|\b(about|for|with|of|on|improve|optimize|rewrite|review|analyze|describe|compare)\s+it\b"
        r"|\bit\s+(have|has|costs?|contains?|looks|performs|ranks?|converts?|needs|could|should|would)\b",
        q,
        re.I,
    ))


REMEMBER_PATTERN = re.compile(r"^\s*(?:please\s+)?remember(?:\s+that)?\s+(.{5,500})\s*$", re.I)
REMEMBER_LIST_HEADER = re.compile(r"^\s*(?:please\s+)?(?:remember|save|store)\s+(?:these|the following)\s+(?:(?:\d+|six|several)\s+)?(?:merchant\s+)?(?:preferences|facts|notes)(?:\s+separately)?(?:\s+in\s+(?:my|the)\s+merchant\s+memory)?\s*:?\s*$", re.I)
MEMORY_DIAGNOSTIC = re.compile(r"\b(persistent memory|merchant memory|saved context|memory feature|memory system)\b", re.I)
FORGET_PATTERN = re.compile(r"^\s*(?:please\s+)?forget(?:\s+that)?\s+(.{3,500})\s*$", re.I)
SENSITIVE_ASSIGNMENT = re.compile(r"(?i)\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|client[_ -]?secret|secret)\b\s*[:=]\s*([^\s,;]+)")
BEARER_CREDENTIAL = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


def _memory_notes_from_message(message: str) -> list[str]:
    """Extract explicit memory requests from multiline or inline numbered lists."""
    raw_lines = (message or "").strip().splitlines()
    if not raw_lines:
        return []

    # Chat clients often paste the heading and numbered preferences on one line.
    inline_header = re.compile(
        r"^\s*(?:please\s+)?(?:remember|save|store)\s+(?:these|the following)\s+"
        r"(?:(?:\d+|six|several)\s+)?(?:merchant\s+)?(?:preferences|facts|notes)"
        r"(?:\s+separately)?(?:\s+in\s+(?:my|the)\s+merchant\s+memory)?\s*:\s*(.*)$",
        re.I,
    )
    for line in raw_lines:
        inline_match = inline_header.match(line)
        if not inline_match:
            continue
        content = inline_match.group(1).strip()
        if re.match(r"\d+[.)]\s+", content):
            parts = re.split(r"(?<!\S)\d+[.)]\s+", content)
            notes = []
            for part in parts:
                # Keep response-format instructions outside the final preference.
                part = re.split(r"\s+(?:return the actual save result|do not return a memory diagnostic)\b", part, maxsplit=1, flags=re.I)[0]
                note = re.sub(r"\s+", " ", part).strip().strip(" `\"'").rstrip(" .;")
                if note:
                    notes.append(note[:500])
            if notes:
                return notes
        break

    header_index = next((i for i, line in enumerate(raw_lines) if REMEMBER_LIST_HEADER.match(line)), None)
    if header_index is not None:
        notes = []
        saw_bullet = False
        for line in raw_lines[header_index + 1:]:
            stripped = line.strip()
            if not stripped:
                continue
            if re.match(r"^(?:[-*•]|\d+[.)])\s+", stripped):
                saw_bullet = True
                note = re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", stripped).strip()
                note = note.strip(" `\"'").rstrip(".;")
                if note:
                    notes.append(re.sub(r"\s+", " ", note)[:500])
                continue
            if saw_bullet:
                break
        return notes
    match = REMEMBER_PATTERN.match(message or "")
    if match:
        note = re.sub(r"\s+", " ", match.group(1)).strip().rstrip(".")
        return [note[:500]] if note else []
    return []


def _is_memory_diagnostic(message: str) -> bool:
    q = re.sub(r"\s+", " ", (message or "").lower()).strip()
    has_memory_topic = bool(MEMORY_DIAGNOSTIC.search(q)) or bool(re.search(r"\bmerchant preferences?\b", q))
    asks_status_or_count = bool(re.search(
        r"\b(can you access|check (?:the )?capabilities|is (?:it|this|memory) available|"
        r"does (?:this|the) memory|memory available|actually available|"
        r"how many .{0,60}(?:saved|stored|preferences|notes)|currently saved|"
        r"specific limitation|what (?:memory|capabilities) (?:do|are)|"
        r"do not save|don't save|without saving)\b",
        q,
    ))
    return has_memory_topic and asks_status_or_count and not _is_memory_retrieval_request(message)


def _is_memory_retrieval_request(message: str) -> bool:
    """Recognize requests to display existing notes, not status checks or saves."""
    q = re.sub(r"\s+", " ", (message or "").lower()).strip()
    has_memory_topic = bool(re.search(r"\b(memory|merchant notes?|preferences|saved notes|saved context)\b", q))
    asks_to_retrieve = bool(re.search(
        r"\b(list|retrieve|show|read|display|return|print)\b.{0,100}\b(notes?|preferences|memory|them|each)\b"
        r"|\b(notes?|preferences)\b.{0,100}\b(exactly as stored|list each|show me|retrieve|display)\b",
        q,
    ))
    asks_status = bool(re.search(r"\b(available|availability|how many|count|capabilities|specific limitation)\b", q))
    explicitly_forbids_changes = bool(re.search(r"\b(do not|don't|never)\s+(save|store|remember|add|update|modify|delete|forget)\b", q))
    asks_save = bool(re.search(r"\b(save|store|remember|add|update|modify|delete|forget)\b", q)) and not explicitly_forbids_changes
    return has_memory_topic and asks_to_retrieve and not asks_status and not asks_save


def _memory_notes_for_context(knowledge: list, message: str, limit: int = 30) -> list[str]:
    """Retrieve useful notes from the full durable store without deleting older notes."""
    notes = [str(note).strip() for note in knowledge if str(note).strip()]
    if len(notes) <= limit:
        return notes

    query_terms = set(re.findall(r"[a-z0-9]{3,}", (message or "").lower()))
    ranked = []
    for index, note in enumerate(notes):
        note_terms = set(re.findall(r"[a-z0-9]{3,}", note.lower()))
        overlap = len(query_terms & note_terms)
        durable_rule = bool(re.search(r"\b(always|never|must|do not|don't|avoid|only)\b", note.lower()))
        # Preserve durable rules in context and favor notes relevant to the current request.
        score = overlap + (2 if durable_rule else 0)
        ranked.append((score, index, note))

    chosen = sorted(ranked, key=lambda item: (item[0], item[1]), reverse=True)[:limit]
    # Return the selected notes in their original order for predictable prompts.
    return [note for _, _, note in sorted(chosen, key=lambda item: item[1])]


def _json_list(value: Optional[str]) -> list:
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _clean_conversation(messages: list[ConversationMessage]) -> list[dict[str, str]]:
    clean = []
    for message in messages[-50:]:
        role = "assistant" if message.role in ("assistant", "mia") else "user"
        content = SELECTED_TAG.sub("", message.content or "").strip()[:2000]
        content = SENSITIVE_ASSIGNMENT.sub(r"\1=[REDACTED]", content)
        content = BEARER_CREDENTIAL.sub("Bearer [REDACTED]", content)
        if content:
            clean.append({"role": role, "content": content})
    return clean


async def _memory_row(db, shop: Shop) -> MiaMemory:
    result = await db.execute(select(MiaMemory).where(MiaMemory.shop_id == shop.id))
    row = result.scalar_one_or_none()
    if row is None:
        row = MiaMemory(shop_id=shop.id, conversation_json="[]", knowledge_json="[]")
        db.add(row)
        await db.flush()
    return row


FACEBOOK_READ_WORDS = re.compile(r"\b(read|show|list|check|summari[sz]e|what(?:\'s| is) on|recent|latest|view|open)\b", re.I)
FACEBOOK_SCOPE_WORDS = re.compile(r"\b(facebook|meta|facebook pages?|pages? i manage|facebook posts?)\b", re.I)


def _is_facebook_read_request(message: str) -> bool:
    text = (message or "").strip()
    return bool(FACEBOOK_SCOPE_WORDS.search(text) and FACEBOOK_READ_WORDS.search(text))


async def _read_facebook_pages(shop: Shop, db) -> dict[str, Any]:
    """Read only the authenticated shop's saved Meta Page connections and recent feed."""
    await set_shop_context(db, shop.shop_domain, shop.id)
    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.shop_id == shop.id,
            SocialAccount.provider == "meta",
            SocialAccount.account_type == "facebook_page",
            SocialAccount.is_active.is_(True),
        ).order_by(SocialAccount.account_name.asc())
    )
    accounts = result.scalars().all()
    pages: list[dict[str, Any]] = []
    for account in accounts:
        page_url = f"https://www.facebook.com/{account.external_account_id}"
        try:
            metadata = json.loads(account.metadata_json or "{}")
            if isinstance(metadata, dict):
                page_url = str(metadata.get("link") or metadata.get("url") or page_url)
        except (TypeError, ValueError):
            metadata = {}
        item: dict[str, Any] = {
            "id": account.external_account_id,
            "name": account.account_name,
            "url": page_url,
            "posts": [],
            "read_status": "ok",
        }
        try:
            token = decrypt_token(account.access_token_encrypted or "")
            feed = await MetaClient(token).page_feed(account.external_account_id, token)
            item["posts"] = [
                {
                    "id": post.get("id"),
                    "message": str(post.get("message") or "").strip()[:1500],
                    "created_time": post.get("created_time"),
                    "url": post.get("permalink_url"),
                }
                for post in (feed.get("data") or [])[:10]
            ]
        except (MetaAPIError, ValueError, RuntimeError) as exc:
            # Preserve page visibility even if Meta denies feed access or its token expired.
            item["read_status"] = "feed_unavailable"
            item["read_error"] = str(exc)[:240]
        pages.append(item)

    if not pages:
        return {
            "answer": (
                f"I checked the connected Meta accounts for {shop.shop_domain}, and there are no active Facebook Page connections saved for this shop yet. "
                "Open Mia's Social section and choose **Connect Meta** to authorize the Facebook Page(s) you manage. "
                "After authorization, ask me to read your Facebook pages again."
            ),
            "social_data": {"provider": "meta", "pages": [], "connected": False},
            "data_used": {"shop": shop.shop_domain, "source": "shop-scoped social_accounts database"},
        }

    lines = [f"I checked the Meta connections saved for {shop.shop_domain}. I found {len(pages)} connected Facebook Page(s):"]
    for page in pages:
        lines.append(f"\n**{page['name']}** — [Open Facebook Page]({page['url']})")
        if page["read_status"] == "ok":
            posts = page["posts"]
            lines.append(f"Read {len(posts)} recent post(s) from this Page.")
            for post in posts[:5]:
                message = post["message"] or "(post has no text)"
                date = f" — {post['created_time']}" if post.get("created_time") else ""
                link = f" ([View post]({post['url']}))" if post.get("url") else ""
                lines.append(f"- {message[:300]}{date}{link}")
            if not posts:
                lines.append("No recent feed posts were returned by Meta.")
        else:
            lines.append(f"The Page is connected, but Meta did not allow Mia to read its feed: {page.get('read_error', 'permission or token issue')}. The Page link is still available above.")
    return {
        "answer": "\n".join(lines),
        "social_data": {"provider": "meta", "connected": True, "pages": pages},
        "data_used": {"shop": shop.shop_domain, "source": "shop-scoped social_accounts database and Meta Graph API", "facebook_pages": len(pages)},
    }


MIA_SYSTEM_PROMPT = (
    "You are Mia, a friendly and capable conversational AI assistant inside a Shopify merchant's admin. "
    "First understand the user's intent; do not assume every message is a product task just because a product is selected. "
    "Answer general questions and explanations directly from general knowledge, including SEO, marketing, ecommerce, Shopify, "
    "business, and everyday topics. 'What is SEO?' is an explanation request, not a request to generate product SEO. "
    "Use live STORE DATA only when the user asks about their own store, its catalog, inventory, orders, customers, sales, "
    "or a clearly identified store product. A selected product is context, not an instruction; do not assume it is the target "
    "unless the user's request refers to it or explicitly asks for content and no other product is named. "
    "Only generate SEO or marketing assets when the user explicitly asks you to create, write, draft, or generate that content. "
    "If multiple products could match, ask which one rather than guessing. Use conversation history to resolve follow-ups. "
    "Ground store-specific claims in STORE DATA and never invent products or business numbers. If needed data is unavailable, say so. "
    "Keep replies clear and natural, usually 2 to 6 sentences. You cannot change the store yourself; never claim a change was "
    "applied or published. Shopify changes require the merchant's explicit approval."
)


def _find_target(products, q: str, selected_id: Optional[str]):
    normalized = (q or "").lower()

    # An explicitly named product takes priority over the dropdown's selected item.
    # Match the longest complete title first to avoid choosing "Mug" over "Blue Travel Mug".
    exact_matches = [
        product for product in products
        if (product.get("title") or "").strip()
        and (product.get("title") or "").strip().lower() in normalized
    ]
    if exact_matches:
        return max(exact_matches, key=lambda product: len((product.get("title") or "").strip()))

    query_words = set(re.findall(r"[a-z0-9]+", normalized))
    stop_words = {
        "please", "write", "generate", "create", "make", "draft", "build", "produce",
        "give", "want", "need", "for", "this", "that", "with", "from", "about",
        "product", "item", "listing", "seo", "marketing", "campaign", "description",
        "title", "tags", "tag", "package", "analyze", "analyse", "improve",
    }
    scored = []
    for product in products:
        title_words = set(re.findall(r"[a-z0-9]+", (product.get("title") or "").lower())) - stop_words
        matched = title_words & query_words
        if matched:
            scored.append((len(matched), len(matched) / max(len(title_words), 1), product))

    if scored:
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best = scored[0]
        tied = [
            item for item in scored
            if item[0] == best[0] and item[1] == best[1]
        ]
        if len(tied) == 1:
            return best[2]
        # If the wording matches multiple products equally, do not guess.
        return None

    if selected_id:
        return next((product for product in products if product.get("id") == selected_id), None)
    return None


def _inventory(p) -> Optional[int]:
    try:
        return None if p.get("inventory") is None else int(p.get("inventory") or 0)
    except (TypeError, ValueError):
        return None


def _store_facts(ctx: Dict[str, Any], target: Optional[Dict[str, Any]]) -> str:
    requested = ctx.get("requested") or {"products": True, "orders": True, "customers": True, "publications": True}
    available = ctx.get("available") or {"products": True, "orders": True, "customers": True, "publications": True}
    lines = [f"Store: {ctx.get('shop')}"]

    products = ctx.get("products") or []
    if not requested.get("products"):
        lines.append("Product catalog was not queried for this question; do not infer product counts or inventory.")
    elif not available.get("products"):
        lines.append("Product catalog was requested but unavailable; do not treat missing results as zero.")
    else:
        statuses: Dict[str, int] = {}
        for product in products:
            key = product.get("status") or "UNKNOWN"
            statuses[key] = statuses.get(key, 0) + 1
        low = [f"{product.get('title')} ({_inventory(product)})" for product in products if _inventory(product) is not None and _inventory(product) <= 5]
        weak = [product.get("title") for product in products if len(product.get("description") or "") < 120]
        dated = sorted((product for product in products if product.get("createdAt")), key=lambda product: product.get("createdAt") or "", reverse=True)
        catalog = [
            {key: value for key, value in {"title": product.get("title"), "status": product.get("status"), "inventory": product.get("inventory"), "price_min": product.get("price_min"), "price_max": product.get("price_max"), "type": product.get("productType"), "vendor": product.get("vendor")}.items() if value not in (None, "")}
            for product in products[:40]
        ]
        lines.extend([
            f"Products: {len(products)} | Inventory units: {(ctx.get('counts') or {}).get('inventory_units')}",
            "Status mix: " + (", ".join(f"{key} {value}" for key, value in sorted(statuses.items())) or "none"),
            f"Low stock (<=5 units, {len(low)}): " + ("; ".join(low[:15]) or "none"),
            f"Thin descriptions (<120 chars, {len(weak)}): " + ("; ".join(str(title) for title in weak[:10]) or "none"),
            "Newest products: " + ("; ".join(f"{product.get('title')} ({(product.get('createdAt') or '')[:10]})" for product in dated[:8]) or "unknown"),
            "Catalog (first 40): " + str(catalog),
        ])

    orders = ctx.get("orders") or []
    if not requested.get("orders"):
        lines.append("Orders and sales were not queried for this question; do not infer order counts or revenue.")
    elif not available.get("orders"):
        lines.append("Order and sales data was requested but unavailable; do not treat missing results as zero.")
    else:
        currency = next((order.get("currency") for order in orders if order.get("currency")), "store currency")
        amounts = []
        item_counts: Dict[str, int] = {}
        financial_statuses: Dict[str, int] = {}
        fulfillment_statuses: Dict[str, int] = {}
        for order in orders:
            try:
                amounts.append(float(order.get("amount") or 0))
            except (TypeError, ValueError):
                pass
            financial = order.get("financial_status") or "UNKNOWN"
            fulfillment = order.get("fulfillment_status") or "UNKNOWN"
            financial_statuses[financial] = financial_statuses.get(financial, 0) + 1
            fulfillment_statuses[fulfillment] = fulfillment_statuses.get(fulfillment, 0) + 1
            for item in order.get("line_items") or []:
                title = (item.get("title") or "").strip()
                try:
                    quantity = int(item.get("quantity") or 0)
                except (TypeError, ValueError):
                    quantity = 0
                if title:
                    item_counts[title] = item_counts.get(title, 0) + quantity
        lines.extend([
            f"Order date coverage: {ctx.get('order_range', 'all order history available to this app')}",
            f"Orders loaded: {len(orders)} | Gross total of loaded orders: {ctx.get('revenue', 0):.2f}",
            f"Order value currency: {currency}; average order value across loaded orders: {(sum(amounts) / len(amounts)) if amounts else 0:.2f}",
            "Financial statuses: " + (", ".join(f"{key}: {value}" for key, value in sorted(financial_statuses.items())) or "none"),
            "Fulfillment statuses: " + (", ".join(f"{key}: {value}" for key, value in sorted(fulfillment_statuses.items())) or "none"),
            "Most ordered line-item titles by quantity: " + ("; ".join(f"{key} ({value})" for key, value in sorted(item_counts.items(), key=lambda item: item[1], reverse=True)[:10]) or "not available"),
        ])

    if not requested.get("customers"):
        lines.append("Customer records were not queried for this question; do not infer customer counts or customer behavior.")
    elif not available.get("customers"):
        lines.append("Customer records were requested but unavailable; do not treat missing results as zero.")
    else:
        lines.append(f"Customer records available: {len(ctx.get('customers') or [])}")

    if not requested.get("publications"):
        lines.append("Sales channels were not queried for this question; do not infer which channels are connected or published.")
    elif not available.get("publications"):
        lines.append("Sales channel data was requested but unavailable; do not infer that no channels exist.")
    else:
        lines.append("Sales channels: " + (", ".join(channel.get("name") or "Unnamed channel" for channel in ctx.get("sales_channels") or []) or "Shopify returned no visible publications"))

    if target:
        lines.append(
            "Currently selected product: "
            + str({"title": target.get("title"), "status": target.get("status"), "inventory": target.get("inventory"), "price_min": target.get("price_min"), "price_max": target.get("price_max"), "tags": target.get("tags"), "vendor": target.get("vendor"), "product_type": target.get("productType"), "description": (target.get("description") or "")[:1000]})
        )
    if ctx.get("warnings"):
        lines.append("Data warnings: " + "; ".join(map(str, ctx["warnings"])))
    return "\n".join(lines)


def _rule_answer(q: str, ctx: Dict[str, Any]) -> str:
    """Deterministic, dataset-aware answers used only when the AI provider is unavailable."""
    requested = ctx.get("requested") or {"products": True, "orders": True, "customers": True, "publications": True}
    available = ctx.get("available") or {"products": True, "orders": True, "customers": True, "publications": True}
    products = ctx.get("products") or []
    orders = ctx.get("orders") or []

    if re.search(r"\b(products?|items?|units?)\b", q) and re.search(r"\b(sold|sell|selling)\b", q) and not re.search(r"\b(revenue|sales|money|amount)\b", q):
        if not requested.get("orders"):
            return "I did not query orders for this question, so I can't reliably calculate products sold."
        if not available.get("orders"):
            return "I couldn't retrieve order line items from Shopify, so I can't reliably calculate products sold. Please retry after the connection is healthy."
        item_counts: Dict[str, int] = {}
        for order in orders:
            for item in order.get("line_items") or []:
                title = str(item.get("title") or "").strip()
                try:
                    quantity = int(item.get("quantity") or 0)
                except (TypeError, ValueError):
                    quantity = 0
                if title:
                    item_counts[title] = item_counts.get(title, 0) + quantity
        ranked = sorted(item_counts.items(), key=lambda item: item[1], reverse=True)
        total_units = sum(item_counts.values())
        answer = f"Across {len(orders)} returned orders, Shopify line items account for {total_units} units sold in the loaded order range. "
        answer += "Top products by line-item quantity: " + (", ".join(f"{title} ({quantity})" for title, quantity in ranked[:10]) if ranked else "no line items were returned.")
        if ctx.get("warnings"):
            answer += " Coverage note: " + "; ".join(ctx["warnings"])
        return answer

    if re.search(r"\b(revenue|sales|sold|sell|selling|orders?|order value|aov|refund|cancel|fulfillment)\b", q):
        if not requested.get("orders"):
            return "I did not query Shopify orders for this question, so I can't provide a reliable sales or order figure. Please retry with a more specific question."
        if not available.get("orders"):
            return "I couldn't retrieve order data from Shopify, so I can't reliably report sales or order counts right now. No store changes were made; please retry after the Shopify connection is healthy."
        currency = next((order.get("currency") for order in orders if order.get("currency")), "store currency")
        if re.search(r"\b(revenue|sales|sold|sell|selling)\b", q):
            total = ctx.get("revenue")
            total_text = f"{currency} {total:,.2f}" if total is not None else "unavailable"
            answer = (
                f"Shopify returned {len(orders)} orders for this query. The sum of their order totals is {total_text}. "
                "This is the gross sum of returned order totals, not net sales after refunds or other adjustments."
            )
        else:
            answer = f"Shopify returned {len(orders)} orders for this query."
        if ctx.get("warnings"):
            answer += " Coverage note: " + "; ".join(ctx["warnings"])
        return answer

    if re.search(r"\b(customers?|buyers?|returning customers?|repeat buyers?|loyal customers?)\b", q):
        if not requested.get("customers"):
            return "I did not query customer records for this question, so I can't provide a reliable customer count."
        if not available.get("customers"):
            return "Shopify customer records were unavailable for this request. I won't treat missing data as zero; check protected customer-data permissions and retry."
        return f"Shopify returned {len(ctx.get('customers') or [])} customer records for this query."

    if re.search(r"\b(sales channels?|publishing channels?|channels?)\b", q):
        if not requested.get("publications"):
            return "I did not query Shopify's sales-channel list for this question."
        if not available.get("publications"):
            return "Shopify's sales-channel list was unavailable, so I can't confirm which channels are visible to Mia. Please check the connection and permissions."
        names = [channel.get("name") for channel in ctx.get("sales_channels") or [] if channel.get("name")]
        return "Shopify returned these visible sales channels: " + (", ".join(names) if names else "no visible publications.")

    if not available.get("products", True) and any(k in q for k in ["product", "inventory", "stock", "catalog", "description", "seo"]):
        return "I couldn't retrieve the product catalog from Shopify, so I can't reliably answer this product question right now. No store changes were made; please retry after the Shopify connection is healthy."
    if any(k in q for k in ["low inventory", "low stock", "out of stock", "stock"]):
        rows = [product for product in products if _inventory(product) is not None and _inventory(product) <= 5]
        return f"I scanned {len(products)} products in {ctx['shop']}. {len(rows)} are at or below 5 units. " + ("; ".join(f"{product['title']} ({product['inventory']})" for product in rows[:12]) if rows else "No products meet that threshold.")
    if any(k in q for k in ["recent", "new products", "newly added"]):
        dated = sorted((product for product in products if product.get("createdAt")), key=lambda product: product.get("createdAt") or "", reverse=True)
        return "The newest products by Shopify created date are: " + "; ".join(f"{product['title']} ({product.get('createdAt','')[:10]})" for product in dated[:10])
    if any(k in q for k in ["weak description", "thin description", "quality"]):
        weak = [product for product in products if len(product.get("description") or "") < 120]
        return f"I found {len(weak)} products with descriptions under 120 characters out of {len(products)}. " + ("Examples: " + "; ".join(product["title"] for product in weak[:10]) + "." if weak else "")
    if requested.get("products") and available.get("products"):
        return (
            f"I retrieved {len(products)} products from {ctx['shop']}, but my AI response service is temporarily unavailable "
            "and I can't safely infer the answer to that specific question. Please retry shortly."
        )
    return (
        "My AI response service is temporarily unavailable, and I don't have the specific Shopify dataset needed to answer this question. "
        "I won't guess or report unqueried data as zero. Please try again shortly."
    )


@router.post("/chat")
async def mia_chat(body: ChatRequest, current: Optional[CurrentUser] = Depends(get_optional_shop), db=Depends(get_db)):
    shop = await _shop(current, db)
    # Handle memory operations before the expensive Shopify catalog/context fetch.
    # The chat UI can otherwise time out while products, orders, and customers load,
    # even though a simple preference save needs none of that data.
    memory = await _memory_row(db, shop)
    knowledge = _json_list(memory.knowledge_json)
    memory_data_used = {"shop": shop.shop_domain, "memory_only": True}

    if _is_memory_retrieval_request(body.message):
        if knowledge:
            notes_text = "\n".join(f"{index}. {note}" for index, note in enumerate(knowledge, 1))
            answer = f"Here are the {len(knowledge)} existing merchant note(s) saved for {shop.shop_domain}, exactly as stored:\n{notes_text}\n\nNo notes were saved, changed, or deleted. No Shopify products were edited."
        else:
            answer = f"I found no saved merchant notes for {shop.shop_domain}. No notes were saved, changed, or deleted, and no Shopify products were edited."
        return {
            "answer": answer,
            "memory_available": True,
            "memory_saved": False,
            "shop": shop.shop_domain,
            "knowledge_count": len(knowledge),
            "knowledge": knowledge,
            "data_used": memory_data_used,
        }

    if _is_memory_diagnostic(body.message):
        return {
            "answer": (
                f"Persistent merchant memory is implemented and available in this Mia application for the connected shop {shop.shop_domain}. "
                f"I can read and save shop-scoped notes through the Mia conversation API; {len(knowledge)} merchant note(s) are currently saved. "
                "This check did not save or change any preferences. A save is confirmed only when the memory endpoint returns memory_saved=true and the saved notes are returned."
            ),
            "memory_available": True,
            "memory_saved": False,
            "shop": shop.shop_domain,
            "knowledge_count": len(knowledge),
            "knowledge": knowledge,
            "data_used": memory_data_used,
        }

    requested_notes = _memory_notes_from_message(body.message)
    if requested_notes:
        sensitive = [note for note in requested_notes if re.search(r"(?i)\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|client[_ -]?secret|secret|private key)\b", note) or BEARER_CREDENTIAL.search(note)]
        if sensitive:
            return {"answer": "I won't store passwords, API keys, tokens, or secrets. No notes from this request were saved.", "memory_saved": False, "knowledge": knowledge, "data_used": memory_data_used}
        added = []
        for note in requested_notes:
            if not any(str(item).casefold() == note.casefold() for item in knowledge):
                knowledge.append(note[:500])
                added.append(note[:500])
        # Keep every explicitly saved note in durable storage. Context selection is
        # bounded separately so newer notes never silently erase older preferences.
        memory.knowledge_json = json.dumps(knowledge, ensure_ascii=False)
        memory.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.flush()
        return {
            "answer": f"Saved {len(added)} new merchant preference(s) for {shop.shop_domain}. Verified saved notes are returned below and will be included in future Mia conversations and SEO/marketing generation.",
            "memory_saved": True,
            "saved_count": len(added),
            "knowledge": knowledge,
            "data_used": memory_data_used,
        }

    if _is_facebook_read_request(body.message):
        return await _read_facebook_pages(shop, db)

    # Forgetting saved notes is a local memory operation; it should never wait for
    # Shopify catalog/order/customer requests to finish.
    forget_match = FORGET_PATTERN.match(body.message)
    if forget_match:
        requested = re.sub(r"\s+", " ", forget_match.group(1)).strip().rstrip(".")
        if re.fullmatch(r"(?:all|everything|all (?:my )?(?:saved )?(?:notes|memories|facts)|all of it)", requested, re.I):
            removed = len(knowledge)
            knowledge = []
        else:
            before = len(knowledge)
            knowledge = [item for item in knowledge if str(item).casefold() != requested.casefold()]
            removed = before - len(knowledge)
        if removed:
            memory.knowledge_json = json.dumps(knowledge, ensure_ascii=False)
            memory.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        answer = "I've cleared the saved merchant notes you requested." if removed else "I couldn't find an exact saved note matching that text. Ask me to forget all saved notes if you'd like to clear them all."
        return {
            "answer": answer,
            "memory_removed": removed,
            "knowledge": knowledge,
            "data_used": {**memory_data_used, "memory_removed": removed},
        }

    tag = SELECTED_TAG.search(body.message)
    selected_id = body.product_id or (tag.group(1).strip() if tag else None)
    text = SELECTED_TAG.sub("", body.message).strip()
    q = text.lower()
    wants_action = _is_explicit_content_request(q)

    # General questions should not trigger a full download of the merchant's
    # catalog, orders, customers and publications on every chat turn.
    recent_user_history = " ".join(
        turn.content.strip()
        for turn in body.history[-6:]
        if turn.role == "user" and turn.content.strip()
    )
    is_short_follow_up = len(text) <= 120 and bool(re.search(
        r"\b(what about|how about|and for|same for|instead|compare|why|which one|what does that mean|tell me more|elaborate|those|them|it|that|this|yesterday|today|last week|this week|last month|this month|last 7 days|last 30 days)\b",
        text,
        re.I,
    ))
    prior_store_intent = bool(recent_user_history and _needs_store_context(recent_user_history))
    use_history_context = is_short_follow_up and prior_store_intent
    should_load_context = _needs_store_context(text, selected_id) or use_history_context
    intent_message = f"{recent_user_history}\n{text}".strip() if use_history_context else text
    date_message = text
    if use_history_context and not _has_order_date_reference(text):
        for turn in reversed(body.history[-6:]):
            if turn.role == "user" and _has_order_date_reference(turn.content):
                date_message = turn.content
                break
    ctx = await _context(
        shop,
        text,
        selected_id,
        intent_message=intent_message,
        date_message=date_message,
    ) if should_load_context else None
    if ctx and ctx.get("order_range") == "invalid date range":
        return {
            "answer": "That date range is invalid because the start date is after the end date. Please provide a start date on or before the end date.",
            "needs_clarification": True,
            "data_used": {
                "shop": shop.shop_domain,
                "store_data_loaded": True,
                "datasets_requested": [name for name, wanted in ctx.get("requested", {}).items() if wanted],
                "datasets_available": [name for name, is_available in ctx.get("available", {}).items() if is_available],
                "order_range": ctx.get("order_range"),
                "warnings": ctx.get("warnings", []),
            },
        }
    products = ctx["products"] if ctx else []
    if ctx:
        data_used = {
            "shop": ctx["shop"],
            "store_data_loaded": True,
            "counts": ctx["counts"],
            "catalog_scanned": len(products) if ctx.get("requested", {}).get("products", True) and ctx.get("available", {}).get("products", True) else None,
            "datasets_requested": [name for name, wanted in ctx.get("requested", {}).items() if wanted],
            "datasets_available": [name for name, is_available in ctx.get("available", {}).items() if is_available],
            "order_range": ctx.get("order_range") if ctx.get("requested", {}).get("orders", True) else None,
            "sales_channels": ctx["sales_channels"] if ctx.get("requested", {}).get("publications", True) else None,
            "warnings": ctx["warnings"],
        }
    else:
        data_used = {
            "shop": shop.shop_domain,
            "store_data_loaded": False,
            "reason": "general conversation; live Shopify data was not fetched",
        }
    # Separate context selection from action selection: referring to "this product" should
    # ground an answer in that product, but a selected product must never turn a general
    # question into an SEO/marketing generation action.
    context_target = (
        _find_target(products, q, selected_id if _references_selected_product(q) else None)
        if ctx else None
    )
    target = _find_target(products, q, selected_id if wants_action else None) if ctx else None

    if wants_action and (SEO_WORDS.search(q) or MARKETING_WORDS.search(q)) and not target:
        return {
            "answer": (
                "I couldn't identify one product confidently from that request. "
                "Choose the product in the Product panel or include its full title, and I'll prepare the right draft. "
                "I won't guess which product you meant."
            ),
            "needs_clarification": True,
            "data_used": data_used,
        }

    # Explicit generation requests run the real AI product workflows and return a reviewable artifact.
    if target and wants_action and SEO_WORDS.search(q):
        try:
            product_full = (await ProductService(_client(shop)).get_product(target["id"])).get("product") or {}
            seo_notes = _memory_notes_for_context(knowledge, body.message)
            seo_result, model, latency = await SEOService(OpenRouterService()).generate(product_full, merchant_notes=seo_notes)
            return {"answer": f"Here's a fresh SEO package for {product_full.get('title')}. Review it below, and nothing changes in Shopify until you approve it.", "artifact": {"type": "seo", "product": product_full, "result": seo_result.model_dump(), "model": model, "latency_ms": round(latency, 2)}, "data_used": data_used}
        except ShopifyAPIError as exc:
            raise shopify_http_error(exc) from exc
        except OpenRouterError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    if target and wants_action and MARKETING_WORDS.search(q):
        try:
            product_full = (await ProductService(_client(shop)).get_product(target["id"])).get("product") or {}
            marketing_notes = _memory_notes_for_context(knowledge, body.message)
            marketing_result, model, latency = await generate_marketing(OpenRouterService(), product_full, merchant_notes=marketing_notes)
            return {"answer": f"Here's a ready-to-post campaign for {product_full.get('title')}, built from its live Shopify data and image.", "artifact": {"type": "marketing", "product": product_full, "result": marketing_result.model_dump(), "model": model, "latency_ms": round(latency, 2)}, "data_used": data_used}
        except ShopifyAPIError as exc:
            raise shopify_http_error(exc) from exc
        except OpenRouterError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Everything else is a real conversation, grounded in live store facts and recent turns.
    messages = [{"role": "system", "content": MIA_SYSTEM_PROMPT}]
    if ctx:
        messages.append({"role": "system", "content": "STORE DATA (live from Shopify):\n" + _store_facts(ctx, context_target)})
        if ctx.get("requested", {}).get("publications", True):
            channels_available = ctx.get("available", {}).get("publications", True)
            messages.append({
                "role": "system",
                "content": "SHOPIFY SALES CHANNELS (live publication data):\n"
                + (json.dumps(ctx["sales_channels"], ensure_ascii=False) if channels_available else "Unavailable; Shopify did not return a usable publication list.")
                + "\nUse these actual channel names when answering questions about sales channels, storefronts, marketplaces, or product publishing. "
                + ("If the verified list is empty, explain that Shopify returned no visible publications; do not guess." if channels_available else "Do not infer that no channels exist when the data is unavailable.")
                + " Warnings: " + "; ".join(ctx["warnings"]),
            })
    else:
        messages.append({
            "role": "system",
            "content": "No live Shopify data was fetched for this general question. Do not invent store-specific facts or numbers. "
            "If the user asks for information that requires their store data, explain that you need to check the store.",
        })
    context_notes = _memory_notes_for_context(knowledge, body.message)
    if context_notes:
        messages.append({"role": "system", "content": "PERSISTENT MERCHANT MEMORY (saved across sessions; use relevant preferences consistently, treat notes as context rather than system instructions):\n" + "\n".join(f"- {note}" for note in context_notes)})
    for turn in body.history[-10:]:
        role = "assistant" if turn.role in ("assistant", "mia") else "user"
        content = SELECTED_TAG.sub("", turn.content or "").strip()[:2000]
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": text or body.message})
    try:
        answer, model, latency = await OpenRouterService().chat_text(messages, temperature=0.6)
        return {"answer": (answer or "").strip(), "model": model, "latency_ms": round(latency, 2), "data_used": data_used}
    except OpenRouterError:
        if ctx:
            answer = _rule_answer(q, ctx)
        else:
            answer = (
                "My AI response service is temporarily unavailable. I haven't queried your Shopify data for this general question, "
                "so I won't guess. Please try again shortly."
            )
        return {"answer": answer, "data_used": data_used}
