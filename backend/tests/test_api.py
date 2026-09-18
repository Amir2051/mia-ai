from pathlib import Path

from fastapi.testclient import TestClient


def test_spa_routes_return_frontend_entrypoint_when_built(client: TestClient):
    response = client.get("/dashboard")
    frontend_index = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "index.html"

    if frontend_index.is_file():
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
    else:
        # Backend CI intentionally does not run the frontend build first.
        assert response.status_code == 404


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "Mia AI"
    assert body["database"] == "ok"
    assert body["build_id"]


def test_auth_session_without_authentication(client: TestClient):
    response = client.get("/api/auth/session")
    assert response.status_code == 200
    assert response.json()["connected"] is False
