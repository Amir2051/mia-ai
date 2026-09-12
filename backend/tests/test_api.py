from fastapi.testclient import TestClient


def test_health(client: TestClient):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_auth_session_without_authentication(client: TestClient):
    response = client.get("/api/auth/session")

    assert response.status_code == 200

    data = response.json()

    assert data["connected"] is False
