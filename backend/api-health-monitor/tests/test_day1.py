def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data
    assert data["environment"] == "development"


def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert data["docs"] == "/docs"


def test_register_endpoint(client):
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
    assert data["interval_seconds"] == 60
    assert data["timeout_seconds"] == 5
    assert data["expected_status_code"] == 200
    assert data["is_active"] is True
    assert "created_at" in data


def test_register_endpoint_uses_defaults(client):
    response = client.post(
        "/api/v1/endpoints",
        json={
            "name": "Billing API",
            "url": "https://billing.example.com/status",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["interval_seconds"] == 60
    assert data["timeout_seconds"] == 5
    assert data["expected_status_code"] == 200
    assert data["is_active"] is True


def test_duplicate_url_is_rejected(client):
    payload = {
        "name": "First",
        "url": "https://duplicate.example.com/health",
    }
    assert client.post("/api/v1/endpoints", json=payload).status_code == 201

    duplicate = {
        "name": "Second",
        "url": "https://duplicate.example.com/health/",
    }
    response = client.post("/api/v1/endpoints", json=duplicate)
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]

    assert len(client.get("/api/v1/endpoints").json()) == 1


def test_list_and_get_endpoint(client):
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


def test_get_missing_endpoint_returns_404(client):
    response = client.get("/api/v1/endpoints/999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_patch_and_delete_endpoint(client):
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


def test_patch_to_duplicate_url_is_rejected(client):
    first = client.post(
        "/api/v1/endpoints",
        json={"name": "First", "url": "https://one.example.com/health"},
    ).json()
    second = client.post(
        "/api/v1/endpoints",
        json={"name": "Second", "url": "https://two.example.com/health"},
    ).json()

    response = client.patch(
        f"/api/v1/endpoints/{second['id']}",
        json={"url": first["url"]},
    )
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]

    unchanged = client.get(f"/api/v1/endpoints/{second['id']}").json()
    assert unchanged["url"] == "https://two.example.com/health"


def test_patch_and_delete_missing_endpoint_return_404(client):
    assert client.patch(
        "/api/v1/endpoints/999", json={"name": "Missing"}
    ).status_code == 404
    assert client.delete("/api/v1/endpoints/999").status_code == 404


def test_invalid_interval_is_rejected(client):
    response = client.post(
        "/api/v1/endpoints",
        json={
            "name": "Too Frequent",
            "url": "https://frequent.example.com/health",
            "interval_seconds": 5,
        },
    )
    assert response.status_code == 422


def test_readiness_check(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["database"] == "ok"


def test_root_serves_dashboard_to_browsers(client):
    response = client.get("/", headers={"accept": "text/html"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "API Health Monitor" in response.text


def test_health_reports_monitoring_state(client):
    response = client.get("/health")
    assert response.json()["monitoring_enabled"] is False
