import re
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from sqlalchemy import update

from app.core.auth import COOKIE
from app.db.models import LoginSession
from tests.conftest import TEST_PASSWORD

ENDPOINTS = "/api/v1/endpoints"


def test_dashboard_uses_content_versioned_assets(client):
    page = client.get("/", headers={"accept": "text/html"})
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assets = re.findall(r'(?:href|src)="(/static/[^"?]+)\?v=([a-f0-9]{16})"', page.text)
    assert len(assets) == page.text.count('="/static/')
    assert {"/static/styles.css", "/static/app.js"} <= {path for path, _ in assets}
    for path, version in assets:
        asset = client.get(f"{path}?v={version}")
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "no-cache"
        assert sha256(asset.content).hexdigest().startswith(version)
        conditional = client.get(path, headers={"If-None-Match": asset.headers["etag"]})
        assert conditional.status_code == 304
        assert conditional.headers["cache-control"] == "no-cache"


def test_anonymous_access(anonymous_client):
    response = anonymous_client.get(
        "/", headers={"accept": "text/html"}, follow_redirects=False
    )
    assert response.status_code == 307
    assert response.headers["location"] == "/login"
    assert anonymous_client.get("/login").status_code == 200
    assert anonymous_client.get("/static/index.html").status_code == 404
    for path in (ENDPOINTS, "/docs", "/redoc", "/openapi.json", "/auth/me"):
        assert anonymous_client.get(path).status_code == 401
    assert anonymous_client.post(ENDPOINTS, json={}).status_code == 401
    assert anonymous_client.get("/health").status_code == 200


def test_login_and_logout(anonymous_client, accounts):
    credentials = {"username": "admin", "password": TEST_PASSWORD}
    invalid = anonymous_client.post("/auth/login", json={**credentials, "password": "wrong"})
    assert invalid.status_code == 401

    response = anonymous_client.post("/auth/login", json=credentials)
    assert response.json() == {"username": "admin", "role": "admin"}
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    token = response.cookies[COOKIE]
    assert anonymous_client.get("/docs").status_code == 200
    assert anonymous_client.post("/auth/logout").status_code == 204

    replay = anonymous_client.get(ENDPOINTS, headers={"Cookie": f"{COOKIE}={token}"})
    assert replay.status_code == 401


def test_viewer_access(anonymous_client, accounts):
    response = anonymous_client.post(
        "/auth/login", json={"username": "viewer", "password": TEST_PASSWORD}
    )
    assert response.json()["role"] == "viewer"
    assert anonymous_client.get(ENDPOINTS).status_code == 200
    assert anonymous_client.post(ENDPOINTS, json={}).status_code == 403
    assert anonymous_client.patch(f"{ENDPOINTS}/1", json={}).status_code == 403
    assert anonymous_client.post(f"{ENDPOINTS}/1/check").status_code == 403
    assert anonymous_client.delete(f"{ENDPOINTS}/1").status_code == 403


def test_cross_origin_writes_are_rejected(client):
    headers = {"Origin": "http://other.example"}
    assert client.post(f"{ENDPOINTS}/1/check", headers=headers).status_code == 403
    assert client.post("/auth/logout", headers=headers).status_code == 403
    assert client.get("/auth/me").status_code == 200


def test_expired_session_is_rejected(client, session_factory):
    with session_factory() as db:
        db.execute(update(LoginSession).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        db.commit()
    assert client.get(ENDPOINTS).status_code == 401
