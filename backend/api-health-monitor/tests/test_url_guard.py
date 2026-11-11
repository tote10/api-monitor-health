import socket

import httpx
import pytest

from app.core.config import settings
from app.services import monitor_service, url_guard
from app.services.url_guard import UnsafeTargetError, validate_target_url


@pytest.fixture()
def ssrf_on(monkeypatch):
    monkeypatch.setattr(settings, "SSRF_PROTECTION_ENABLED", True)
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_TARGETS", False)


def _resolve_to(monkeypatch, mapping):
    def fake_resolve(host):
        addresses = mapping.get(host)
        if addresses is None:
            raise socket.gaierror(f"cannot resolve {host}")
        return addresses

    monkeypatch.setattr(url_guard, "resolve_host", fake_resolve)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "file:///etc/passwd",
        "https://user:secret@example.com/health",
        "https:///no-host",
    ],
)
def test_rejects_unsafe_url_shapes_even_without_ssrf_checks(url):
    with pytest.raises(UnsafeTargetError):
        validate_target_url(url)


def test_allows_public_addresses(ssrf_on, monkeypatch):
    _resolve_to(monkeypatch, {"api.example.com": ["93.184.216.34"]})

    validate_target_url("https://api.example.com/health")


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "192.168.0.10",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "fd00::1",
    ],
)
def test_rejects_private_and_reserved_addresses(ssrf_on, monkeypatch, address):
    _resolve_to(monkeypatch, {"internal.example.com": [address]})

    with pytest.raises(UnsafeTargetError, match="private or reserved"):
        validate_target_url("https://internal.example.com")


def test_rejects_when_any_resolved_address_is_private(ssrf_on, monkeypatch):
    _resolve_to(
        monkeypatch,
        {"mixed.example.com": ["93.184.216.34", "10.0.0.7"]},
    )

    with pytest.raises(UnsafeTargetError):
        validate_target_url("https://mixed.example.com")


def test_rejects_unresolvable_hosts(ssrf_on, monkeypatch):
    _resolve_to(monkeypatch, {})

    with pytest.raises(UnsafeTargetError, match="Could not resolve"):
        validate_target_url("https://missing.example.com")


def test_private_targets_can_be_allowed_explicitly(ssrf_on, monkeypatch):
    _resolve_to(monkeypatch, {"internal.example.com": ["10.0.0.7"]})
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_TARGETS", True)

    validate_target_url("https://internal.example.com")


def test_registration_rejects_private_target(client, ssrf_on, monkeypatch):
    _resolve_to(monkeypatch, {"localhost": ["127.0.0.1"]})

    response = client.post(
        "/api/v1/endpoints",
        json={"name": "Local", "url": "http://localhost:8000/health"},
    )

    assert response.status_code == 400
    assert "private or reserved" in response.json()["detail"]


def test_updates_and_webhooks_are_validated(client, ssrf_on, monkeypatch):
    _resolve_to(
        monkeypatch,
        {
            "example.com": ["93.184.216.34"],
            "metadata.internal": ["169.254.169.254"],
        },
    )

    created = client.post(
        "/api/v1/endpoints",
        json={"name": "Target", "url": "https://example.com"},
    )
    assert created.status_code == 201

    updated = client.patch(
        f"/api/v1/endpoints/{created.json()['id']}",
        json={"url": "http://metadata.internal/latest"},
    )
    assert updated.status_code == 400

    channel = client.post(
        "/api/v1/channels",
        json={
            "name": "Internal hook",
            "kind": "webhook",
            "target": "http://metadata.internal/hook",
        },
    )
    assert channel.status_code == 400


def test_checks_block_unsafe_targets_before_sending(
    client, ssrf_on, monkeypatch
):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200)

    def factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(monitor_service, "create_http_client", factory)
    monkeypatch.setattr(settings, "SSRF_PROTECTION_ENABLED", False)
    endpoint = client.post(
        "/api/v1/endpoints",
        json={"name": "Target", "url": "https://target.example.com/ok"},
    ).json()

    monkeypatch.setattr(settings, "SSRF_PROTECTION_ENABLED", True)
    _resolve_to(monkeypatch, {"target.example.com": ["10.0.0.5"]})

    result = client.post(
        f"/api/v1/endpoints/{endpoint['id']}/check"
    ).json()

    assert result["is_success"] is False
    assert result["status_code"] is None
    assert "UnsafeTargetError" in result["error"]
    assert requests == []


def test_redirects_to_private_addresses_are_blocked(
    client, ssrf_on, monkeypatch
):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if request.url.host == "target.example.com":
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest"}
            )
        return httpx.Response(200)

    def factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(monitor_service, "create_http_client", factory)
    monkeypatch.setattr(settings, "SSRF_PROTECTION_ENABLED", False)
    endpoint = client.post(
        "/api/v1/endpoints",
        json={"name": "Target", "url": "https://target.example.com/ok"},
    ).json()

    monkeypatch.setattr(settings, "SSRF_PROTECTION_ENABLED", True)
    _resolve_to(
        monkeypatch,
        {
            "target.example.com": ["93.184.216.34"],
            "169.254.169.254": ["169.254.169.254"],
        },
    )

    result = client.post(
        f"/api/v1/endpoints/{endpoint['id']}/check"
    ).json()

    assert result["is_success"] is False
    assert "private or reserved" in result["error"]
    assert requests == ["https://target.example.com/ok"]


@pytest.mark.parametrize(
    "url",
    [
        "http://a..b/health",
        "http://" + ("a" * 64) + ".com/health",
    ],
)
def test_rejects_hosts_the_idna_codec_cannot_encode(ssrf_on, url):
    with pytest.raises(UnsafeTargetError, match="Could not resolve"):
        validate_target_url(url)


def test_registration_rejects_unencodable_hosts(client, ssrf_on):
    response = client.post(
        "/api/v1/endpoints",
        json={"name": "Odd host", "url": "http://a..b/health"},
    )

    assert response.status_code == 400
    assert "Could not resolve" in response.json()["detail"]
