from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.db.models import AccountInvite
from tests.conftest import TEST_PASSWORD


def create_invite(client, role="viewer"):
    response = client.post("/auth/invites", json={"role": role})
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    return response.json()["token"]


def register(client, token, username="new-viewer"):
    return client.post("/auth/register", json={
        "username": username, "password": TEST_PASSWORD, "invite_token": token,
    })


def test_invited_user_can_register_once_and_sign_in(client):
    token = create_invite(client)
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/register").status_code == 200
    response = register(client, token)
    assert response.status_code == 201
    assert response.json() == {"username": "new-viewer", "role": "viewer"}
    assert register(client, token, username="another-user").status_code == 400

    login = client.post("/auth/login", json={
        "username": "new-viewer", "password": TEST_PASSWORD,
    })
    assert login.status_code == 200
    assert client.get("/api/v1/endpoints").status_code == 200
    assert client.post("/auth/invites", json={"role": "admin"}).status_code == 403


def test_duplicate_username_keeps_invitation_usable(client):
    token = create_invite(client, role="admin")
    assert register(client, token, username="admin").status_code == 409
    response = register(client, token, username="second-admin")
    assert response.json() == {"username": "second-admin", "role": "admin"}


def test_expired_and_invalid_tokens_cannot_register(client, session_factory):
    token = create_invite(client)
    with session_factory() as db:
        db.execute(update(AccountInvite).values(
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))
        db.commit()
    assert register(client, token).status_code == 400
    assert register(client, "a" * 43).status_code == 400
    assert client.post("/auth/register", json={
        "username": "no-invite", "password": TEST_PASSWORD,
    }).status_code == 422


def test_invitation_creation_requires_same_origin_admin(anonymous_client, accounts):
    assert anonymous_client.post("/auth/invites", json={}).status_code == 401
    assert anonymous_client.post("/auth/login", json={
        "username": "admin", "password": TEST_PASSWORD,
    }).status_code == 200
    assert anonymous_client.post(
        "/auth/invites", json={}, headers={"Origin": "https://unrelated.example"},
    ).status_code == 403
