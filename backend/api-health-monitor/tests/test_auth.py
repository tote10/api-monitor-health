import pytest

from app.core.config import settings
from app.core.security import hash_password, verify_password


@pytest.fixture()
def auth_enabled(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "ALLOW_REGISTRATION", True)


def _register(client, email="owner@example.com", name="Owner"):
    return client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": name, "password": "correct horse"},
    )


def _login(client, email="owner@example.com", password="correct horse"):
    return client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )


def test_password_hash_round_trip():
    stored = hash_password("s3cret-passphrase")

    assert stored.startswith("scrypt$")
    assert "s3cret-passphrase" not in stored
    assert verify_password("s3cret-passphrase", stored) is True
    assert verify_password("wrong-password", stored) is False
    assert verify_password("s3cret-passphrase", "not-a-hash") is False


def test_session_endpoint_when_auth_is_disabled(client):
    response = client.get("/api/v1/auth/session")

    assert response.status_code == 200
    assert response.json() == {
        "auth_enabled": False,
        "authenticated": False,
        "registration_open": False,
        "has_users": False,
        "user": None,
    }


def test_api_requires_login_when_auth_is_enabled(client, auth_enabled):
    assert client.get("/api/v1/endpoints").status_code == 401
    assert client.post(
        "/api/v1/endpoints",
        json={"name": "Target", "url": "https://example.com"},
    ).status_code == 401


def test_first_user_becomes_admin_and_is_signed_in(client, auth_enabled):
    response = _register(client, email="Owner@Example.com")

    assert response.status_code == 201, response.text
    user = response.json()
    assert user["email"] == "owner@example.com"
    assert user["role"] == "admin"
    assert "password_hash" not in user

    session = client.get("/api/v1/auth/session").json()
    assert session["authenticated"] is True
    assert session["registration_open"] is True
    assert session["user"]["id"] == user["id"]


def test_second_user_is_viewer_and_cannot_write(client, auth_enabled):
    _register(client, email="owner@example.com", name="Owner")
    response = _register(client, email="viewer@example.com", name="Viewer")

    assert response.status_code == 201
    assert response.json()["role"] == "viewer"

    created = client.post(
        "/api/v1/endpoints",
        json={"name": "Target", "url": "https://example.com"},
    )
    assert created.status_code == 403
    assert created.json()["detail"] == "Administrator role required."

    assert client.get("/api/v1/endpoints").status_code == 200

    client.cookies.clear()
    assert _login(client, "owner@example.com").status_code == 200
    created = client.post(
        "/api/v1/endpoints",
        json={"name": "Target", "url": "https://example.com"},
    )
    assert created.status_code == 201


def test_login_rejects_bad_password_and_logout_revokes_session(
    client, auth_enabled
):
    _register(client)
    client.cookies.clear()

    assert _login(client, password="wrong password").status_code == 401
    assert _login(client).status_code == 200
    assert client.get("/api/v1/auth/session").json()["authenticated"] is True

    assert client.post("/api/v1/auth/logout").status_code == 204
    session = client.get("/api/v1/auth/session").json()
    assert session["authenticated"] is False
    assert client.get("/api/v1/endpoints").status_code == 401


def test_registration_can_be_closed(client, auth_enabled, monkeypatch):
    _register(client)

    monkeypatch.setattr(settings, "ALLOW_REGISTRATION", False)
    assert client.get("/api/v1/auth/session").json()["registration_open"] is (
        False
    )

    response = _register(client, email="late@example.com", name="Late")
    assert response.status_code == 403
    assert "disabled" in response.json()["detail"]


def test_only_admins_can_manage_accounts(client, auth_enabled):
    _register(client, email="owner@example.com", name="Owner")
    assert client.post(
        "/api/v1/auth/users",
        json={
            "email": "viewer@example.com",
            "name": "Viewer",
            "password": "correct horse",
            "role": "viewer",
        },
    ).status_code == 201

    client.cookies.clear()
    _login(client, "viewer@example.com")
    assert client.get("/api/v1/auth/users").status_code == 403
    assert client.post(
        "/api/v1/auth/users",
        json={
            "email": "other@example.com",
            "name": "Other",
            "password": "correct horse",
        },
    ).status_code == 403


def test_admin_can_promote_and_deactivate_accounts(client, auth_enabled):
    admin = _register(client).json()
    viewer = client.post(
        "/api/v1/auth/users",
        json={
            "email": "viewer@example.com",
            "name": "Viewer",
            "password": "correct horse",
        },
    ).json()

    promoted = client.patch(
        f"/api/v1/auth/users/{viewer['id']}", json={"role": "admin"}
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"

    deactivated = client.patch(
        f"/api/v1/auth/users/{viewer['id']}", json={"is_active": False}
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False

    client.cookies.clear()
    assert _login(client, "viewer@example.com").status_code == 401
    assert _login(client, "owner@example.com").status_code == 200
    assert client.get("/api/v1/auth/session").json()["user"]["id"] == admin["id"]


def test_last_admin_cannot_lose_admin_access(client, auth_enabled):
    admin = _register(client).json()

    demote = client.patch(
        f"/api/v1/auth/users/{admin['id']}", json={"role": "viewer"}
    )
    assert demote.status_code == 400

    deactivate = client.patch(
        f"/api/v1/auth/users/{admin['id']}", json={"is_active": False}
    )
    assert deactivate.status_code == 400


def test_password_reset_revokes_existing_sessions(client, auth_enabled):
    admin = _register(client).json()

    reset = client.patch(
        f"/api/v1/auth/users/{admin['id']}",
        json={"password": "a-brand-new-password"},
    )
    assert reset.status_code == 200

    assert client.get("/api/v1/auth/session").json()["authenticated"] is False
    assert _login(client, password="correct horse").status_code == 401
    assert _login(client, password="a-brand-new-password").status_code == 200
