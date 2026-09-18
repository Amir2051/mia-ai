"""
Analytics computation helpers.

Consume Shopify product, order, and customer GraphQL responses and
produce dashboard-level aggregates: totals, top products, recent sales,
average order value, and date-filtered analytics.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except Exception:
        return None


def _parse_created_at(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    text = str(raw)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _sum_revenue(orders: List[Dict[str, Any]]) -> Decimal:
    total = Decimal("0")
    for order in orders:
        price_set = (order.get("totalPriceSet") or {}).get("shopMoney", {})
        amount = _to_float(price_set.get("amount"))
        if amount is not None:
            total += Decimal(str(amount))
    return total


def _extract_orders(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    edges = (((data.get("orders") or {}).get("edges")) or [])
    return [edge.get("node", {}) for edge in edges]


def _extract_customers(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    edges = (((data.get("customers") or {}).get("edges")) or [])
    return [edge.get("node", {}) for edge in edges]


def _extract_products(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    edges = (((data.get("products") or {}).get("edges")) or [])
    return [edge.get("node", {}) for edge in edges]


def compute_dashboard(
    orders_response: Dict[str, Any],
    customers_response: Dict[str, Any],
) -> Dict[str, Any]:
    orders = _extract_orders(orders_response)
    customers = _extract_customers(customers_response)

    total_revenue = _sum_revenue(orders)
    order_count = len(orders)
    customer_count = len(customers)
    aov = (
        float(total_revenue / Decimal(str(order_count)))
        if order_count
        else 0.0
    )

    return {
        "revenue": float(total_revenue),
        "orders": order_count,
        "customers": customer_count,
        "average_order_value": round(aov, 2),
    }


def _line_items_from_order(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    edges = (((order.get("lineItems") or {}).get("edges")) or [])
    for edge in edges:
        node = edge.get("node", {})
        items.append(node)
    return items


def compute_top_products(
    orders_response: Dict[str, Any],
    limit: int = 10,
) -> List[Dict[str, Any]]:
    orders = _extract_orders(orders_response)
    product_stats: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "title": "Unknown",
            "sku": "N/A",
            "units_sold": 0,
            "revenue": Decimal("0"),
        }
    )

    for order in orders:
        for item in _line_items_from_order(order):
            title = item.get("title") or "Unknown"
            sku = item.get("sku") or "N/A"
            key = f"{title}|||{sku}"
            product_stats[key]["title"] = title
            product_stats[key]["sku"] = sku
            product_stats[key]["units_sold"] += int(item.get("quantity") or 0)
            price = _to_float(
                item.get("originalUnitPriceSet", {})
                .get("shopMoney", {})
                .get("amount")
            )
            if price is not None:
                product_stats[key]["revenue"] += Decimal(str(price)) * Decimal(str(item.get("quantity") or 0))

    ranked = sorted(
        product_stats.values(),
        key=lambda p: (p["units_sold"], p["revenue"]),
        reverse=True,
    )
    result = []
    for p in ranked[:limit]:
        result.append(
            {
                "title": p["title"],
                "sku": p["sku"],
                "units_sold": p["units_sold"],
                "revenue": float(p["revenue"]),
            }
        )
    return result


def compute_recent_sales(
    orders_response: Dict[str, Any],
    limit: int = 20,
) -> List[Dict[str, Any]]:
    orders = _extract_orders(orders_response)
    enriched = []
    for order in orders:
        created = order.get("createdAt")
        price_set = (order.get("totalPriceSet") or {}).get("shopMoney", {})
        amount = _to_float(price_set.get("amount"))
        currency = price_set.get("currencyCode")
        enriched.append(
            {
                "id": order.get("id"),
                "name": order.get("name"),
                "created_at": created,
                "amount": amount,
                "currency": currency,
                "customer": order.get("customer"),
            }
        )

    enriched.sort(key=lambda entry: entry.get("created_at") or "", reverse=True)
    return enriched[:limit]


def compute_aov(orders_response: Dict[str, Any]) -> Optional[float]:
    orders = _extract_orders(orders_response)
    if not orders:
        return None
    total = _sum_revenue(orders)
    return round(float(total / Decimal(str(len(orders)))), 2)


def filter_orders_by_date(
    orders_response: Dict[str, Any],
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Dict[str, Any]:
    orders = _extract_orders(orders_response)
    start_dt = _parse_created_at(start) if start else None
    end_dt = _parse_created_at(end) if end else None

    filtered = []
    for order in orders:
        created = _parse_created_at(order.get("createdAt"))
        if created is None:
            continue
        if start_dt and created < start_dt:
            continue
        if end_dt and created > end_dt:
            continue
        filtered.append(order)

    return {
        "orders": filtered,
        "count": len(filtered),
        "revenue": float(_sum_revenue(filtered)),
    }
