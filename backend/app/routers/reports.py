"""Manage recurring in-app sales reports for the authenticated Shopify shop."""
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select

from app.auth.dependencies import CurrentUser, get_current_shop
from app.models.database import get_db
from app.models.schemas import AuditLog, ScheduledReport, Shop
from app.security.rls import set_shop_context
from app.services.scheduled_reports import execute_scheduled_report, next_report_run_at

router = APIRouter()


class ScheduledReportCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    report_type: Literal["sales_summary"] = "sales_summary"
    frequency: Literal["daily", "weekly", "monthly"] = "weekly"
    schedule_time: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = "UTC"
    day_of_week: Optional[int] = Field(default=0, ge=0, le=6)
    day_of_month: Optional[int] = Field(default=1, ge=1, le=31)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Report name cannot be blank")
        return normalized

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone, such as America/New_York") from exc
        return normalized


class ScheduledReportUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    frequency: Optional[Literal["daily", "weekly", "monthly"]] = None
    schedule_time: Optional[str] = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: Optional[str] = None
    day_of_week: Optional[int] = Field(default=None, ge=0, le=6)
    day_of_month: Optional[int] = Field(default=None, ge=1, le=31)
    enabled: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("Report name cannot be blank")
        return normalized

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone, such as America/New_York") from exc
        return normalized

    @model_validator(mode="after")
    def reject_null_required_updates(self):
        for field_name in ("name", "frequency", "schedule_time", "timezone", "enabled"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


async def _current_shop(current: CurrentUser, db) -> Shop:
    await set_shop_context(db, current.shop_domain)
    result = await db.execute(select(Shop).where(Shop.shop_domain == current.shop_domain))
    shop = result.scalar_one_or_none()
    if not shop or not shop.is_active or not shop.access_token_encrypted:
        raise HTTPException(status_code=404, detail="Active Shopify store with a saved access token not found")
    await set_shop_context(db, current.shop_domain, shop.id)
    return shop


def _schedule_dict(schedule: ScheduledReport) -> dict[str, Any]:
    try:
        last_result = json.loads(schedule.last_result_json) if schedule.last_result_json else None
    except (TypeError, ValueError):
        last_result = None
    return {
        "id": schedule.id,
        "shop_id": schedule.shop_id,
        "name": schedule.name,
        "report_type": schedule.report_type,
        "frequency": schedule.frequency,
        "schedule_time": schedule.schedule_time,
        "timezone": schedule.timezone,
        "day_of_week": schedule.day_of_week,
        "day_of_month": schedule.day_of_month,
        "enabled": schedule.enabled,
        "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else None,
        "last_run_at": schedule.last_run_at.isoformat() if schedule.last_run_at else None,
        "last_status": schedule.last_status,
        "last_error": schedule.last_error,
        "last_result": last_result,
        "created_at": schedule.created_at.isoformat() if schedule.created_at else None,
        "updated_at": schedule.updated_at.isoformat() if schedule.updated_at else None,
        "delivery": "in_app",
    }


@router.get("/schedules")
async def list_scheduled_reports(
    current: CurrentUser = Depends(get_current_shop),
    db=Depends(get_db),
):
    shop = await _current_shop(current, db)
    result = await db.execute(
        select(ScheduledReport)
        .where(ScheduledReport.shop_id == shop.id)
        .order_by(ScheduledReport.created_at.desc())
    )
    return {"items": [_schedule_dict(item) for item in result.scalars().all()]}


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
async def create_scheduled_report(
    body: ScheduledReportCreate,
    current: CurrentUser = Depends(get_current_shop),
    db=Depends(get_db),
):
    shop = await _current_shop(current, db)
    try:
        next_run = next_report_run_at(
            body.frequency,
            body.schedule_time,
            body.timezone,
            body.day_of_week,
            body.day_of_month,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    schedule = ScheduledReport(
        shop_id=shop.id,
        name=body.name,
        report_type=body.report_type,
        frequency=body.frequency,
        schedule_time=body.schedule_time,
        timezone=body.timezone,
        day_of_week=body.day_of_week if body.frequency == "weekly" else None,
        day_of_month=body.day_of_month if body.frequency == "monthly" else None,
        enabled=body.enabled,
        next_run_at=next_run,
        last_status="never",
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return _schedule_dict(schedule)


@router.patch("/schedules/{schedule_id}")
async def update_scheduled_report(
    schedule_id: int,
    body: ScheduledReportUpdate,
    current: CurrentUser = Depends(get_current_shop),
    db=Depends(get_db),
):
    shop = await _current_shop(current, db)
    result = await db.execute(
        select(ScheduledReport).where(
            ScheduledReport.id == schedule_id,
            ScheduledReport.shop_id == shop.id,
        )
    )
    schedule = result.scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status_code=404, detail="Scheduled report not found")

    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(schedule, key, value)
    try:
        schedule.next_run_at = next_report_run_at(
            schedule.frequency,
            schedule.schedule_time,
            schedule.timezone,
            schedule.day_of_week,
            schedule.day_of_month,
        )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(schedule)
    return _schedule_dict(schedule)


@router.delete("/schedules/{schedule_id}")
async def delete_scheduled_report(
    schedule_id: int,
    current: CurrentUser = Depends(get_current_shop),
    db=Depends(get_db),
):
    shop = await _current_shop(current, db)
    result = await db.execute(
        select(ScheduledReport).where(
            ScheduledReport.id == schedule_id,
            ScheduledReport.shop_id == shop.id,
        )
    )
    schedule = result.scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status_code=404, detail="Scheduled report not found")
    await db.delete(schedule)
    await db.commit()
    return {"success": True, "deleted_id": schedule_id}


@router.post("/schedules/{schedule_id}/run-now")
async def run_scheduled_report_now(
    schedule_id: int,
    current: CurrentUser = Depends(get_current_shop),
    db=Depends(get_db),
):
    shop = await _current_shop(current, db)
    result = await db.execute(
        select(ScheduledReport).where(
            ScheduledReport.id == schedule_id,
            ScheduledReport.shop_id == shop.id,
        )
    )
    schedule = result.scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status_code=404, detail="Scheduled report not found")
    await db.commit()
    outcome = await execute_scheduled_report(
        schedule.id,
        shop.id,
        shop.shop_domain,
        internal_scheduler=False,
    )
    if not outcome.get("success"):
        raise HTTPException(status_code=502, detail=outcome.get("error") or "Report generation failed")
    return outcome


@router.get("/history")
async def scheduled_report_history(
    limit: int = Query(default=20, ge=1, le=100),
    current: CurrentUser = Depends(get_current_shop),
    db=Depends(get_db),
):
    shop = await _current_shop(current, db)
    result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.shop_id == shop.id,
            AuditLog.action.in_(["scheduled_report.completed", "scheduled_report.failed"]),
        )
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    items = []
    for row in result.scalars().all():
        try:
            details = json.loads(row.details_json or "{}")
        except (TypeError, ValueError):
            details = {}
        items.append({
            "id": row.id,
            "action": row.action,
            "entity_id": row.entity_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "details": details,
        })
    return {"items": items}
