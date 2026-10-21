import asyncio

from app.models.database import get_db


def test_liveness_and_readiness_endpoints(client):
    live = client.get("/health")
    ready = client.get("/ready")

    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["database"] == "ok"


def test_readiness_returns_503_when_database_probe_fails(client):
    async def broken_db():
        class BrokenSession:
            async def execute(self, statement):
                raise RuntimeError("database details must not be returned")

            async def rollback(self):
                return None

        yield BrokenSession()

    client.app.dependency_overrides[get_db] = broken_db
    try:
        response = client.get("/ready")
    finally:
        client.app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    assert response.json()["detail"] == "Database readiness check failed"
    assert "database details" not in response.text


def test_integration_health_without_shop_session_is_explicit(client):
    response = client.get("/api/settings/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is False
    assert payload["overall_status"] == "not_connected"
    assert payload["shopify"]["status"] == "not_connected"
