from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.db.models import AccountInvite
from tests.conftest import TEST_PASSWORD


def create_invite(client):
    response = client.post("/auth/invites")
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
    assert response.json() == {"username": "new-viewer", "role": "member"}
    assert register(client, token, username="another-user").status_code == 400

    login = client.post("/auth/login", json={
        "username": "new-viewer", "password": TEST_PASSWORD,
    })
    assert login.status_code == 200
    assert client.get("/api/v1/endpoints").status_code == 200
    assert client.post("/auth/invites", json={"role": "admin"}).status_code == 403


def test_duplicate_username_keeps_invitation_usable(client):
    token = create_invite(client)
    assert register(client, token, username="admin").status_code == 409
    response = register(client, token, username="second-member")
    assert response.json() == {"username": "second-member", "role": "member"}


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


def test_invited_members_can_manage_only_their_own_endpoints(client):
    root = "/api/v1/endpoints"
    url = "https://example.com/"
    shared = client.post(root, json={"name": "Shared", "url": url}).json()
    for name in ("first-member", "second-member"):
        assert register(client, create_invite(client), username=name).status_code == 201

    def sign_in(username):
        login = client.post("/auth/login", json={"username": username, "password": TEST_PASSWORD})
        assert login.status_code == 200

    sign_in("first-member")
    assert client.get(root).json() == []
    first = client.post(root, json={"name": "First", "url": url, "is_active": False})
    assert first.status_code == 201
    first_path = f"{root}/{first.json()['id']}"
    assert client.patch(first_path, json={"name": "My renamed endpoint"}).status_code == 200
    assert client.get(first_path + "/status").status_code == 200
    assert client.post(root, json={"name": "Duplicate", "url": url}).status_code == 400

    sign_in("second-member")
    assert client.get(root).json() == []
    assert client.get(f"{root}/{shared['id']}").status_code == 404
    for suffix in ("", "/status", "/checks", "/incidents"):
        assert client.get(first_path + suffix).status_code == 404
    assert client.post(first_path + "/check").status_code == 404
    assert client.patch(first_path, json={"name": "Wrong workspace"}).status_code == 404
    assert client.delete(first_path).status_code == 404
    second = client.post(root, json={"name": "Second", "url": url, "is_active": False})
    assert second.status_code == 201
    assert client.delete(f"{root}/{second.json()['id']}").status_code == 204

    sign_in("admin")
    assert [item["id"] for item in client.get(root).json()] == [shared["id"]]
    assert client.get(first_path).status_code == 404
