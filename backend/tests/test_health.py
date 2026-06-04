from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

EXPECTED_PAYLOAD = {
    "status": "ok",
    "service": "gtmflow-ai-backend",
    "version": "0.1.0",
}


def test_api_health_returns_payload() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == EXPECTED_PAYLOAD


def test_health_alias_returns_payload() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == EXPECTED_PAYLOAD
