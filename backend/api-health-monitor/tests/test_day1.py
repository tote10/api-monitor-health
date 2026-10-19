import pytest
from fastapi.testclient import TestClient
from main import app
from app.services.endpoint_service import reset_mock_db

client = TestClient(app)


@pytest.fixture(autouse=True)
def run_around_tests():
    reset_mock_db()
    yield
    reset_mock_db()


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data
    assert data["environment"] == "development"


def test_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert data["docs"] == "/docs"


def test_register_endpoint():
    payload = {
        "name": "Production Auth API",
        "url": "https://auth.example.com/health",
        "interval_seconds": 60,
        "timeout_seconds": 5,
        "expected_status_code": 200,
        "is_active": True,
    }
    response = client.post("/api/v1/endpoints", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["id"] == 1
    assert data["name"] == payload["name"]
    assert data["url"] == "https://auth.example.com/health"
    assert data["is_active"] is True
    assert "created_at" in data


def test_list_and_get_endpoint():
    # Register endpoint
    payload = {
        "name": "Billing API",
        "url": "https://billing.example.com/status",
    }
    create_res = client.post("/api/v1/endpoints", json=payload)
    endpoint_id = create_res.json()["id"]

    # List endpoints
    list_res = client.get("/api/v1/endpoints")
    assert list_res.status_code == 200
    items = list_res.json()
    assert len(items) == 1
    assert items[0]["name"] == "Billing API"

    # Get single endpoint
    get_res = client.get(f"/api/v1/endpoints/{endpoint_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == endpoint_id


def test_patch_and_delete_endpoint():
    # Register endpoint
    create_res = client.post("/api/v1/endpoints", json={
        "name": "Old Name",
        "url": "https://service.example.com/health",
    })
    endpoint_id = create_res.json()["id"]

    # Patch endpoint
    patch_res = client.patch(f"/api/v1/endpoints/{endpoint_id}", json={
        "name": "Updated Name",
        "interval_seconds": 120,
    })
    assert patch_res.status_code == 200
    assert patch_res.json()["name"] == "Updated Name"
    assert patch_res.json()["interval_seconds"] == 120

    # Delete endpoint
    delete_res = client.delete(f"/api/v1/endpoints/{endpoint_id}")
    assert delete_res.status_code == 204

    # Verify not found
    get_after_delete = client.get(f"/api/v1/endpoints/{endpoint_id}")
    assert get_after_delete.status_code == 404
