import asyncio
import calendar
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


def test_next_report_run_at_daily_uses_store_timezone_and_rolls_forward():
    from app.services.scheduled_reports import next_report_run_at

    before_schedule = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    assert next_report_run_at(
        "daily", "09:00", "America/New_York", now=before_schedule
    ) == datetime(2026, 10, 20, 13, 0)

    after_schedule = datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)
    assert next_report_run_at(
        "daily", "09:00", "America/New_York", now=after_schedule
    ) == datetime(2026, 10, 21, 13, 0)


def test_next_report_run_at_weekly_and_monthly_clamp_day_to_month_end():
    from app.services.scheduled_reports import next_report_run_at

    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    assert next_report_run_at(
        "weekly", "09:00", "America/New_York", day_of_week=0, now=now
    ) == datetime(2026, 10, 26, 13, 0)

    assert next_report_run_at(
        "monthly", "09:00", "America/New_York", day_of_month=31, now=now
    ) == datetime(2026, 10, 31, 13, 0)

    after_october = datetime(2026, 10, 31, 14, 0, tzinfo=timezone.utc)
    assert next_report_run_at(
        "monthly", "09:00", "America/New_York", day_of_month=31, now=after_october
    ) == datetime(2026, 11, 30, 14, 0)


@pytest.mark.parametrize(
    "frequency,schedule_time,timezone_name",
    [
        ("yearly", "09:00", "UTC"),
        ("daily", "25:00", "UTC"),
        ("daily", "09:00", "Not/A_Timezone"),
    ],
)
def test_next_report_run_at_rejects_invalid_schedule_values(frequency, schedule_time, timezone_name):
    from app.services.scheduled_reports import next_report_run_at

    with pytest.raises(ValueError):
        next_report_run_at(frequency, schedule_time, timezone_name)


def test_fetch_sales_report_uses_period_filter_and_stores_summary_only(monkeypatch):
    from app.services import scheduled_reports

    now = datetime.now(timezone.utc)
    current_created = (now - timedelta(hours=2)).replace(microsecond=0).isoformat()
    previous_created = (now - timedelta(days=1, hours=2)).replace(microsecond=0).isoformat()
    captured = {}

    class FakeClient:
        def __init__(self, shop_domain, access_token):
            captured["shop_domain"] = shop_domain
            captured["access_token"] = access_token

    class FakeOrderService:
        def __init__(self, client):
            pass

        async def list_orders(self, query="", first=250, after=None):
            captured["query"] = query
            if after:
                return {"orders": {"edges": [], "pageInfo": {"hasNextPage": False}}}
            return {"orders": {"edges": [
                {"node": {
                    "id": "current", "createdAt": current_created, "displayFinancialStatus": "PAID",
                    "totalPriceSet": {"shopMoney": {"amount": "100.00", "currencyCode": "USD"}},
                    "currentSubtotalPriceSet": {"shopMoney": {"amount": "80.00", "currencyCode": "USD"}},
                }},
                {"node": {
                    "id": "previous", "createdAt": previous_created, "displayFinancialStatus": "PAID",
                    "totalPriceSet": {"shopMoney": {"amount": "50.00", "currencyCode": "USD"}},
                    "currentSubtotalPriceSet": {"shopMoney": {"amount": "45.00", "currencyCode": "USD"}},
                }},
            ], "pageInfo": {"hasNextPage": False, "endCursor": None}}}

    monkeypatch.setattr(scheduled_reports, "decrypt_token", lambda token: "decrypted-test-token")
    monkeypatch.setattr(scheduled_reports, "ShopifyAPIClient", FakeClient)
    monkeypatch.setattr(scheduled_reports, "OrderService", FakeOrderService)

    shop = SimpleNamespace(
        shop_domain="scheduled-report-test.myshopify.com",
        access_token_encrypted="encrypted",
        scope="read_orders,read_all_orders",
    )
    schedule = SimpleNamespace(name="Daily sales", report_type="sales_summary", frequency="daily")
    result = asyncio.run(scheduled_reports._fetch_sales_report(shop, schedule))

    assert captured["shop_domain"] == shop.shop_domain
    assert captured["access_token"] == "decrypted-test-token"
    assert "created_at:>='" in captured["query"]
    assert result["report_name"] == "Daily sales"
    assert result["comparison"]["period_days"] == 1
    assert result["comparison"]["current"]["orders"] == 1
    assert result["comparison"]["previous"]["orders"] == 1
    assert result["comparison"]["current"]["net_sales"] == 80.0
    assert result["comparison"]["previous"]["net_sales"] == 45.0
    assert "orders" not in result
    assert "customer" not in result


def test_scheduled_report_api_is_shop_scoped_and_supports_pause_and_delete(client, monkeypatch):
    from app.auth.dependencies import CurrentUser, get_current_shop
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import Shop
    from sqlalchemy import select

    shop_domain = "scheduled-report-api.myshopify.com"

    async def seed_shop():
        async with AsyncSessionLocal() as db:
            shop = (await db.execute(select(Shop).where(Shop.shop_domain == shop_domain))).scalar_one_or_none()
            if shop is None:
                db.add(Shop(
                    shop_domain=shop_domain,
                    access_token_encrypted="test-placeholder",
                    is_active=True,
                ))
                await db.commit()

    asyncio.run(seed_shop())
    client.app.dependency_overrides[get_current_shop] = lambda: CurrentUser(shop_domain)
    try:
        created = client.post("/api/reports/schedules", json={
            "name": "Weekly store report",
            "frequency": "weekly",
            "schedule_time": "09:00",
            "timezone": "America/New_York",
            "day_of_week": 0,
        })
        assert created.status_code == 201, created.text
        schedule = created.json()
        assert schedule["name"] == "Weekly store report"
        assert schedule["enabled"] is True
        assert schedule["last_status"] == "never"
        assert schedule["delivery"] == "in_app"

        listed = client.get("/api/reports/schedules")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [schedule["id"]]

        paused = client.patch(f"/api/reports/schedules/{schedule['id']}", json={"enabled": False})
        assert paused.status_code == 200, paused.text
        assert paused.json()["enabled"] is False

        from app.models.database import AsyncSessionLocal
        from app.models.schemas import ScheduledReport
        from sqlalchemy import select

        async def mark_schedule_due():
            async with AsyncSessionLocal() as db:
                row = (await db.execute(
                    select(ScheduledReport).where(ScheduledReport.id == schedule["id"])
                )).scalar_one()
                row.next_run_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
                await db.commit()

        asyncio.run(mark_schedule_due())

        run_call = {}
        async def fake_execute(schedule_id, shop_id, shop_domain, *, internal_scheduler=False):
            run_call.update({
                "schedule_id": schedule_id,
                "shop_id": shop_id,
                "shop_domain": shop_domain,
                "internal_scheduler": internal_scheduler,
            })
            return {"success": True, "schedule_id": schedule_id, "status": "completed", "result": {"report_name": "Weekly store report"}}

        monkeypatch.setattr("app.routers.reports.execute_scheduled_report", fake_execute)
        run_now = client.post(f"/api/reports/schedules/{schedule['id']}/run-now")
        assert run_now.status_code == 200, run_now.text
        refreshed = client.get("/api/reports/schedules").json()["items"][0]
        assert datetime.fromisoformat(refreshed["next_run_at"]) > datetime.now(timezone.utc).replace(tzinfo=None)
        assert run_call["schedule_id"] == schedule["id"]
        assert run_call["shop_domain"] == shop_domain
        assert run_call["internal_scheduler"] is False

        duplicate_run = client.post(f"/api/reports/schedules/{schedule['id']}/run-now")
        assert duplicate_run.status_code == 409, duplicate_run.text

        invalid_timezone = client.post("/api/reports/schedules", json={
            "name": "Bad timezone",
            "frequency": "daily",
            "schedule_time": "09:00",
            "timezone": "Not/A_Timezone",
        })
        assert invalid_timezone.status_code == 422

        deleted = client.delete(f"/api/reports/schedules/{schedule['id']}")
        assert deleted.status_code == 200
        assert deleted.json()["success"] is True
    finally:
        client.app.dependency_overrides.clear()



def test_scheduled_report_history_is_scoped_to_current_shop(client):
    import json
    from app.auth.dependencies import CurrentUser, get_current_shop
    from app.models.database import AsyncSessionLocal
    from app.models.schemas import AuditLog, Shop
    from sqlalchemy import select

    current_domain = "scheduled-history-current.myshopify.com"
    other_domain = "scheduled-history-other.myshopify.com"

    async def seed_history():
        async with AsyncSessionLocal() as db:
            shops = {}
            for domain in (current_domain, other_domain):
                shop = (await db.execute(select(Shop).where(Shop.shop_domain == domain))).scalar_one_or_none()
                if shop is None:
                    shop = Shop(shop_domain=domain, access_token_encrypted="test-placeholder", is_active=True)
                    db.add(shop)
                    await db.flush()
                shops[domain] = shop
            db.add(AuditLog(
                shop_id=shops[current_domain].id,
                action="scheduled_report.completed",
                entity_type="scheduled_report",
                entity_id="10",
                details_json=json.dumps({"report_name": "My report", "net_sales": 100}),
            ))
            db.add(AuditLog(
                shop_id=shops[other_domain].id,
                action="scheduled_report.completed",
                entity_type="scheduled_report",
                entity_id="20",
                details_json=json.dumps({"report_name": "Other shop report", "net_sales": 900}),
            ))
            await db.commit()

    asyncio.run(seed_history())
    client.app.dependency_overrides[get_current_shop] = lambda: CurrentUser(current_domain)
    try:
        response = client.get("/api/reports/history")
    finally:
        client.app.dependency_overrides.pop(get_current_shop, None)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["details"]["report_name"] == "My report"
    assert items[0]["details"]["net_sales"] == 100
