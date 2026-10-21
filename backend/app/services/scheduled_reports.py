"""Tenant-safe scheduled in-app sales reports."""
import asyncio
import calendar
import json
import logging
import re
from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, text

from app.models.database import AsyncSessionLocal
from app.models.schemas import ScheduledReport, Shop
from app.security.rls import set_shop_context
from app.security.tokens import decrypt_token
from app.services.analytics import compute_period_comparison
from app.services.audit import record_audit_event
from app.services.orders import OrderService
from app.shopify.client import ShopifyAPIClient, ShopifyAPIError

logger = logging.getLogger("mia_ai.scheduled_reports")

REPORT_PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
SCHEDULER_FLAG = "app.internal_scheduler"


def next_report_run_at(
    frequency: str,
    schedule_time: str,
    timezone_name: str,
    day_of_week: Optional[int] = None,
    day_of_month: Optional[int] = None,
    now: Optional[datetime] = None,
) -> datetime:
    """Return the next local wall-clock schedule as a naive UTC DB datetime."""
    if frequency not in REPORT_PERIOD_DAYS:
        raise ValueError("frequency must be daily, weekly, or monthly")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", schedule_time):
        raise ValueError("schedule_time must be HH:MM")
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("timezone must be a valid IANA timezone") from exc

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current_local = current.astimezone(zone)
    hour, minute = map(int, schedule_time.split(":"))
    local_time = time(hour=hour, minute=minute)

    if frequency == "daily":
        candidate_date = current_local.date()
        candidate = datetime.combine(candidate_date, local_time, tzinfo=zone)
        if candidate <= current_local:
            candidate = datetime.combine(candidate_date + timedelta(days=1), local_time, tzinfo=zone)
    elif frequency == "weekly":
        weekday = 0 if day_of_week is None else int(day_of_week)
        if not 0 <= weekday <= 6:
            raise ValueError("day_of_week must be between 0 (Monday) and 6 (Sunday)")
        days_ahead = (weekday - current_local.weekday()) % 7
        candidate_date = current_local.date() + timedelta(days=days_ahead)
        candidate = datetime.combine(candidate_date, local_time, tzinfo=zone)
        if candidate <= current_local:
            candidate = datetime.combine(candidate_date + timedelta(days=7), local_time, tzinfo=zone)
    else:
        day = 1 if day_of_month is None else int(day_of_month)
        if not 1 <= day <= 31:
            raise ValueError("day_of_month must be between 1 and 31")
        month_day = min(day, calendar.monthrange(current_local.year, current_local.month)[1])
        candidate_date = current_local.date().replace(day=month_day)
        candidate = datetime.combine(candidate_date, local_time, tzinfo=zone)
        if candidate <= current_local:
            if current_local.month == 12:
                year, month = current_local.year + 1, 1
            else:
                year, month = current_local.year, current_local.month + 1
            next_day = min(day, calendar.monthrange(year, month)[1])
            candidate = datetime.combine(current_local.date().replace(year=year, month=month, day=next_day), local_time, tzinfo=zone)

    return candidate.astimezone(timezone.utc).replace(tzinfo=None)


def _period_days(frequency: str) -> int:
    try:
        return REPORT_PERIOD_DAYS[frequency]
    except KeyError as exc:
        raise ValueError("Unsupported report frequency") from exc


async def _fetch_sales_report(shop: Shop, schedule: ScheduledReport) -> dict[str, Any]:
    if not shop.access_token_encrypted:
        raise RuntimeError("Shopify access token is not available for this store")
    access_token = decrypt_token(shop.access_token_encrypted)
    client = ShopifyAPIClient(shop_domain=shop.shop_domain, access_token=access_token)
    period_days = _period_days(schedule.frequency)
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=period_days * 2)
    start_text = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_text = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    order_query = f"created_at:>='{start_text}' created_at:<='{end_text}'"
    service = OrderService(client)
    orders: list[dict[str, Any]] = []
    warnings: list[str] = []
    after = None
    pagination_complete = True

    for page_number in range(100):
        page_data = await service.list_orders(query=order_query, first=250, after=after)
        connection = page_data.get("orders") or {}
        for edge in connection.get("edges") or []:
            order = edge.get("node") if isinstance(edge, dict) and "node" in edge else edge
            if isinstance(order, dict):
                orders.append(order)
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        next_cursor = page_info.get("endCursor")
        if not next_cursor or next_cursor == after:
            pagination_complete = False
            warnings.append("Order pagination stopped without a new cursor; this report may be incomplete.")
            break
        if page_number == 99:
            pagination_complete = False
            warnings.append("Order history reached the 25,000-order safety limit; this report may be incomplete.")
        after = next_cursor

    granted_scopes = {item.strip() for item in (shop.scope or "").split(",") if item.strip()}
    if "read_all_orders" not in granted_scopes and start.date() <= (now - timedelta(days=60)).date():
        warnings.append("Shopify may restrict order history to the most recent 60 days because read_all_orders is not granted.")

    comparison = compute_period_comparison(
        {"orders": {"edges": [{"node": order} for order in orders]}},
        days=period_days,
        now=now,
    )
    currency = next(
        (
            ((order.get("totalPriceSet") or {}).get("shopMoney") or {}).get("currencyCode")
            for order in orders
            if ((order.get("totalPriceSet") or {}).get("shopMoney") or {}).get("currencyCode")
        ),
        None,
    )
    if not pagination_complete:
        comparison["coverage_complete"] = False
    else:
        comparison["coverage_complete"] = True

    return {
        "report_type": schedule.report_type,
        "report_name": schedule.name,
        "frequency": schedule.frequency,
        "generated_at": now.isoformat(),
        "currency": currency,
        "comparison": comparison,
        "warnings": warnings,
    }


async def _load_schedule_and_shop(
    schedule_id: int,
    shop_id: int,
    shop_domain: Optional[str],
    *,
    internal_scheduler: bool,
) -> tuple[ScheduledReport, Shop]:
    async with AsyncSessionLocal() as db:
        if internal_scheduler:
            await db.execute(text("select set_config(:setting, 'true', true)"), {"setting": SCHEDULER_FLAG})
        elif shop_domain:
            await set_shop_context(db, shop_domain, shop_id)
        else:
            raise ValueError("shop context is required")
        schedule_result = await db.execute(
            select(ScheduledReport).where(
                ScheduledReport.id == schedule_id,
                ScheduledReport.shop_id == shop_id,
            )
        )
        schedule = schedule_result.scalar_one_or_none()
        if schedule is None:
            raise LookupError("Scheduled report not found")
        shop_result = await db.execute(select(Shop).where(Shop.id == shop_id))
        shop = shop_result.scalar_one_or_none()
        if shop is None or not shop.is_active:
            raise RuntimeError("Shopify store is inactive or unavailable")
        if not internal_scheduler:
            schedule.last_status = "running"
            schedule.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
            schedule.last_error = None
        await db.commit()
        return schedule, shop


async def _save_report_outcome(
    schedule_id: int,
    shop_id: int,
    shop_domain: str,
    *,
    status_value: str,
    result_payload: Optional[dict[str, Any]],
    error: Optional[str],
) -> None:
    async with AsyncSessionLocal() as db:
        await set_shop_context(db, shop_domain, shop_id)
        db_result = await db.execute(
            select(ScheduledReport).where(
                ScheduledReport.id == schedule_id,
                ScheduledReport.shop_id == shop_id,
            )
        )
        schedule = db_result.scalar_one_or_none()
        if schedule is None:
            return
        schedule.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
        schedule.last_status = status_value
        schedule.last_error = error
        schedule.last_result_json = json.dumps(result_payload, ensure_ascii=False) if result_payload is not None else None
        await db.commit()

    details: dict[str, Any] = {
        "status": status_value,
        "report_name": (result_payload or {}).get("report_name"),
        "frequency": (result_payload or {}).get("frequency"),
        "error": error,
    }
    comparison = (result_payload or {}).get("comparison") or {}
    current = comparison.get("current") or {}
    details.update({
        "period_days": comparison.get("period_days"),
        "net_sales": current.get("net_sales"),
        "orders": current.get("orders"),
        "average_order_value": current.get("average_order_value"),
        "currency": (result_payload or {}).get("currency"),
        "change_percent": (comparison.get("change_percent") or {}).get("net_sales"),
        "warnings": (result_payload or {}).get("warnings", []),
    })
    async with AsyncSessionLocal() as db:
        await record_audit_event(
            db,
            shop_id=shop_id,
            shop_domain=shop_domain,
            action="scheduled_report.completed" if status_value == "completed" else "scheduled_report.failed",
            entity_type="scheduled_report",
            entity_id=str(schedule_id),
            details=details,
        )


async def _mark_internal_load_failure(schedule_id: int, shop_id: int, error: str) -> None:
    """Mark a claimed job failed even when its shop configuration cannot be loaded."""
    async with AsyncSessionLocal() as db:
        await db.execute(text("select set_config(:setting, 'true', true)"), {"setting": SCHEDULER_FLAG})
        schedule_result = await db.execute(
            select(ScheduledReport).where(
                ScheduledReport.id == schedule_id,
                ScheduledReport.shop_id == shop_id,
            )
        )
        schedule = schedule_result.scalar_one_or_none()
        shop_result = await db.execute(select(Shop).where(Shop.id == shop_id))
        shop = shop_result.scalar_one_or_none()
        await db.commit()
    if schedule is None or shop is None:
        return
    payload = {"report_name": schedule.name, "frequency": schedule.frequency, "warnings": []}
    await _save_report_outcome(
        schedule_id,
        shop_id,
        shop.shop_domain,
        status_value="failed",
        result_payload=payload,
        error=error,
    )


async def execute_scheduled_report(
    schedule_id: int,
    shop_id: int,
    shop_domain: Optional[str] = None,
    *,
    internal_scheduler: bool = False,
) -> dict[str, Any]:
    try:
        schedule, shop = await _load_schedule_and_shop(
            schedule_id,
            shop_id,
            shop_domain,
            internal_scheduler=internal_scheduler,
        )
    except Exception:
        if internal_scheduler:
            safe_error = "The scheduled report could not load its Shopify store configuration."
            await _mark_internal_load_failure(schedule_id, shop_id, safe_error)
            return {"success": False, "schedule_id": schedule_id, "status": "failed", "error": safe_error}
        raise
    if not internal_scheduler and not shop_domain:
        raise ValueError("shop context is required")

    try:
        result = await _fetch_sales_report(shop, schedule)
        await _save_report_outcome(
            schedule_id,
            shop_id,
            shop.shop_domain,
            status_value="completed",
            result_payload=result,
            error=None,
        )
        return {"success": True, "schedule_id": schedule_id, "status": "completed", "result": result}
    except ShopifyAPIError as exc:
        safe_error = f"Shopify API request failed (HTTP {exc.status_code})."
    except Exception:
        logger.exception("Scheduled report failed schedule_id=%s shop_id=%s", schedule_id, shop_id)
        safe_error = "Report generation failed. Check the integration health panel and try again."

    await _save_report_outcome(
        schedule_id,
        shop_id,
        shop.shop_domain,
        status_value="failed",
        result_payload={"report_name": schedule.name, "frequency": schedule.frequency, "warnings": []},
        error=safe_error,
    )
    return {"success": False, "schedule_id": schedule_id, "status": "failed", "error": safe_error}


async def dispatch_due_reports(limit: int = 5) -> int:
    """Claim due jobs with PostgreSQL row locks so multiple web workers do not duplicate runs."""
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)
    claimed: list[tuple[int, int]] = []
    async with AsyncSessionLocal() as db:
        async with db.begin():
            await db.execute(text("select set_config(:setting, 'true', true)"), {"setting": SCHEDULER_FLAG})
            result = await db.execute(
                select(ScheduledReport)
                .where(ScheduledReport.enabled.is_(True), ScheduledReport.next_run_at <= now_naive)
                .order_by(ScheduledReport.next_run_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            for schedule in result.scalars().all():
                claimed.append((schedule.id, schedule.shop_id))
                schedule.next_run_at = next_report_run_at(
                    schedule.frequency,
                    schedule.schedule_time,
                    schedule.timezone,
                    schedule.day_of_week,
                    schedule.day_of_month,
                    now=now,
                )
                schedule.last_status = "running"
                schedule.last_run_at = now_naive
                schedule.last_error = None
    for schedule_id, shop_id in claimed:
        try:
            await execute_scheduled_report(schedule_id, shop_id, internal_scheduler=True)
        except Exception:
            logger.exception("Could not dispatch scheduled report id=%s shop_id=%s", schedule_id, shop_id)
    return len(claimed)


async def scheduled_report_loop(poll_seconds: int = 30) -> None:
    while True:
        try:
            await dispatch_due_reports()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled report dispatcher iteration failed")
        await asyncio.sleep(max(5, poll_seconds))
