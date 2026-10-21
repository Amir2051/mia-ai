"""
Analytics computation helpers.

Consume Shopify product, order, and customer GraphQL responses and
produce dashboard-level aggregates: totals, top products, recent sales,
average order value, and date-filtered analytics.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
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
    try:
        # Shopify timestamps can include offsets and fractional seconds.
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        pass
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            parsed = datetime.strptime(text, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            continue
    return None


PAID_FINANCIAL_STATUSES = {"PAID", "PARTIALLY_REFUNDED"}
UNPAID_FINANCIAL_STATUSES = {"AUTHORIZED", "EXPIRED", "PARTIALLY_PAID", "PENDING", "VOIDED"}


def _is_cancelled(order: Dict[str, Any]) -> bool:
    return bool(order.get("cancelledAt"))


def _financial_status(order: Dict[str, Any]) -> str:
    return str(order.get("displayFinancialStatus") or order.get("financialStatus") or "").upper()


def _is_paid_order(order: Dict[str, Any]) -> bool:
    if _is_cancelled(order):
        return False
    financial_status = _financial_status(order)
    # Older fixtures/imported responses may omit status. Real Shopify order
    # responses include it, so retain backwards compatibility for those inputs.
    return financial_status in PAID_FINANCIAL_STATUSES if financial_status else True


def _money_amount(order: Dict[str, Any], *field_names: str) -> Optional[Decimal]:
    for field_name in field_names:
        price_set = (order.get(field_name) or {}).get("shopMoney", {})
        amount = _to_float(price_set.get("amount"))
        if amount is not None:
            return Decimal(str(amount))
    return None


def _sum_revenue(orders: List[Dict[str, Any]]) -> Decimal:
    """Net sales proxy: paid, non-cancelled orders after returns/refunds."""
    total = Decimal("0")
    for order in orders:
        if not _is_paid_order(order):
            continue
        amount = _money_amount(order, "currentSubtotalPriceSet", "subtotalPriceSet", "totalPriceSet")
        if amount is not None:
            total += amount
    return total


def _sum_gross_order_value(orders: List[Dict[str, Any]]) -> Decimal:
    """Original order totals for non-cancelled orders, before returns/refunds."""
    total = Decimal("0")
    for order in orders:
        if _is_cancelled(order):
            continue
        amount = _money_amount(order, "totalPriceSet")
        if amount is not None:
            total += amount
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
    customers_response: Optional[Dict[str, Any]] = None,
    *,
    customers_count: Optional[int] = None,
) -> Dict[str, Any]:
    orders = _extract_orders(orders_response)
    customers = _extract_customers(customers_response or {})

    net_sales = _sum_revenue(orders)
    gross_order_value = _sum_gross_order_value(orders)
    order_count = len(orders)
    paid_orders = sum(1 for order in orders if _is_paid_order(order))
    cancelled_orders = sum(1 for order in orders if _is_cancelled(order))
    unpaid_orders = sum(
        1 for order in orders
        if not _is_cancelled(order)
        and _financial_status(order) in UNPAID_FINANCIAL_STATUSES
    )
    refunded_orders = sum(
        1 for order in orders
        if not _is_cancelled(order) and _financial_status(order) == "REFUNDED"
    )
    if customers_count is not None:
        customer_count = customers_count
    elif customers_response is not None:
        customer_count = len(customers)
    else:
        customer_count = None
    aov = float(net_sales / Decimal(str(paid_orders))) if paid_orders else 0.0

    return {
        "revenue": float(net_sales),
        "net_sales": float(net_sales),
        "gross_order_value": float(gross_order_value),
        "orders": order_count,
        "paid_orders": paid_orders,
        "unpaid_orders": unpaid_orders,
        "refunded_orders": refunded_orders,
        "cancelled_orders": cancelled_orders,
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
        if not _is_paid_order(order):
            continue
        for item in _line_items_from_order(order):
            title = item.get("title") or "Unknown"
            sku = item.get("sku") or "N/A"
            key = f"{title}|||{sku}"
            product_stats[key]["title"] = title
            product_stats[key]["sku"] = sku
            quantity = int(item.get("currentQuantity", item.get("quantity")) or 0)
            if quantity <= 0:
                continue
            product_stats[key]["units_sold"] += quantity
            price = _to_float(
                (item.get("discountedUnitPriceSet") or item.get("originalUnitPriceSet") or {})
                .get("shopMoney", {})
                .get("amount")
            )
            if price is not None:
                product_stats[key]["revenue"] += Decimal(str(price)) * Decimal(str(quantity))

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
        price_set = (
            order.get("currentTotalPriceSet")
            or order.get("totalPriceSet")
            or {}
        ).get("shopMoney", {})
        amount = _to_float(price_set.get("amount"))
        currency = price_set.get("currencyCode")
        enriched.append(
            {
                "id": order.get("id"),
                "name": order.get("name"),
                "created_at": created,
                "amount": amount,
                "currency": currency,
                "financial_status": _financial_status(order) or None,
                "cancelled_at": order.get("cancelledAt"),
                "is_cancelled": _is_cancelled(order),
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


def compute_period_comparison(
    orders_response: Dict[str, Any],
    *,
    days: int = 30,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Compare rolling current and previous periods using returned Shopify orders.

    Timestamps are compared in UTC. The caller should surface its order-history
    coverage warning alongside this result when Shopify limits older records.
    """
    period_days = max(1, min(int(days), 365))
    current_end = now or datetime.now(timezone.utc)
    if current_end.tzinfo is None:
        current_end = current_end.replace(tzinfo=timezone.utc)
    current_end = current_end.astimezone(timezone.utc)
    current_start = current_end - timedelta(days=period_days)
    previous_start = current_start - timedelta(days=period_days)

    current_orders: List[Dict[str, Any]] = []
    previous_orders: List[Dict[str, Any]] = []
    for order in _extract_orders(orders_response):
        created = _parse_created_at(order.get("createdAt"))
        if created is None:
            continue
        created = created.astimezone(timezone.utc)
        if current_start <= created < current_end:
            current_orders.append(order)
        elif previous_start <= created < current_start:
            previous_orders.append(order)

    def stats(orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        paid_orders = sum(1 for order in orders if _is_paid_order(order))
        net_sales = _sum_revenue(orders)
        return {
            "orders": len(orders),
            "paid_orders": paid_orders,
            "net_sales": float(net_sales),
            "gross_order_value": float(_sum_gross_order_value(orders)),
            "average_order_value": round(float(net_sales / Decimal(str(paid_orders))), 2) if paid_orders else 0.0,
        }

    current = stats(current_orders)
    previous = stats(previous_orders)

    def change(current_value: float, previous_value: float) -> Optional[float]:
        if previous_value == 0:
            return 0.0 if current_value == 0 else None
        return round(((current_value - previous_value) / abs(previous_value)) * 100, 1)

    return {
        "period_days": period_days,
        "timezone": "UTC",
        "current_period": {"start": current_start.isoformat(), "end": current_end.isoformat()},
        "previous_period": {"start": previous_start.isoformat(), "end": current_start.isoformat()},
        "current": current,
        "previous": previous,
        "change_percent": {
            "net_sales": change(current["net_sales"], previous["net_sales"]),
            "orders": change(float(current["orders"]), float(previous["orders"])),
            "average_order_value": change(current["average_order_value"], previous["average_order_value"]),
        },
    }


def filter_orders_by_date(
    orders_response: Dict[str, Any],
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Dict[str, Any]:
    orders = _extract_orders(orders_response)
    start_dt = _parse_created_at(start) if start else None
    end_dt = _parse_created_at(end) if end else None
    if end_dt is not None and end and len(str(end)) == 10:
        end_dt = end_dt + timedelta(days=1) - timedelta(microseconds=1)

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
        "net_sales": float(_sum_revenue(filtered)),
        "gross_order_value": float(_sum_gross_order_value(filtered)),
        "paid_orders": sum(1 for order in filtered if _is_paid_order(order)),
        "unpaid_orders": sum(
            1 for order in filtered
            if not _is_cancelled(order) and _financial_status(order) in UNPAID_FINANCIAL_STATUSES
        ),
        "refunded_orders": sum(
            1 for order in filtered
            if not _is_cancelled(order) and _financial_status(order) == "REFUNDED"
        ),
        "cancelled_orders": sum(1 for order in filtered if _is_cancelled(order)),
    }


def filter_orders_response(
    orders_response: Dict[str, Any],
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Dict[str, Any]:
    """Keep only orders created in [start, end], preserving the GraphQL shape.

    A date-only ``end`` (YYYY-MM-DD) includes that whole day.
    """
    if not start and not end:
        return orders_response
    start_dt = _parse_created_at(start) if start else None
    end_dt = _parse_created_at(end) if end else None
    if end_dt is not None and end and len(str(end)) == 10:
        end_dt = end_dt + timedelta(days=1) - timedelta(microseconds=1)

    edges = (((orders_response.get("orders") or {}).get("edges")) or [])
    kept = []
    for edge in edges:
        created = _parse_created_at((edge.get("node") or {}).get("createdAt"))
        if created is None:
            continue
        if start_dt and created < start_dt:
            continue
        if end_dt and created > end_dt:
            continue
        kept.append(edge)
    return {"orders": {"edges": kept, "pageInfo": {"hasNextPage": False, "endCursor": None}}}
