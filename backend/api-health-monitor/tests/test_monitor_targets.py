import asyncio
import socket
from unittest.mock import AsyncMock

import httpx
import pytest

from app.db.models import Endpoint
from app.services import monitor_service, monitor_targets


@pytest.mark.parametrize("target", ["https://localhost/", "http://192.168.1.1/", "ftp://example.com/"])
def test_endpoint_create_and_update_require_public_http_urls(client, target):
    path = "/api/v1/endpoints"
    assert client.post(path, json={"name": "Test", "url": target}).status_code == 422

    response = client.post(path, json={"name": "Test", "url": "https://example.com/"})
    assert response.status_code == 201
    endpoint_id = response.json()["id"]
    assert client.patch(f"{path}/{endpoint_id}", json={"url": target}).status_code == 422


def test_previously_saved_local_url_can_be_viewed_but_not_checked(client, session_factory, monkeypatch):
    with session_factory() as db:
        endpoint = Endpoint(name="Legacy", url="http://127.0.0.1/")
        db.add(endpoint)
        db.commit()
        endpoint_id = endpoint.id

    def unexpected_request(request):
        pytest.fail("A disallowed target reached the transport")

    monkeypatch.setattr(
        monitor_service, "create_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(unexpected_request)),
    )
    path = f"/api/v1/endpoints/{endpoint_id}"
    assert client.get(path).status_code == 200
    response = client.post(f"{path}/check")
    assert response.status_code == 201
    assert response.json()["is_success"] is False
    assert "public IP" in response.json()["error"]


def test_all_dns_answers_must_be_public(monkeypatch):
    async def exercise():
        dns = AsyncMock(return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("1.1.1.1", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.1", 443)),
        ])
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
        with pytest.raises(monitor_targets.UnsafeMonitorURL, match="public IP"):
            await monitor_targets.resolve_public_addresses(httpx.URL("https://example.com"))

    asyncio.run(exercise())


def test_public_redirect_preserves_host_and_connects_to_checked_address(monkeypatch):
    async def exercise():
        dns = AsyncMock(side_effect=[
            [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("1.1.1.1", 8443))],
            [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2606:4700::1111", 8443, 0, 0))],
        ])
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path == "/start":
                return httpx.Response(302, headers={"Location": "/finish"})
            return httpx.Response(200)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            response = await monitor_targets.fetch_public_url(
                client, "https://example.com:8443/start", timeout=5,
            )
        assert response.status_code == 200
        assert [request.url.host for request in requests] == ["1.1.1.1", "2606:4700::1111"]
        assert [request.headers["host"] for request in requests] == ["example.com:8443"] * 2
        assert [request.extensions["sni_hostname"] for request in requests] == ["example.com"] * 2

    asyncio.run(exercise())


def test_redirect_to_local_url_stops_before_sending():
    async def exercise():
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(302, headers={"Location": "http://localhost/"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(monitor_targets.UnsafeMonitorURL, match="Local hostnames"):
                await monitor_targets.fetch_public_url(client, "https://1.1.1.1/", timeout=5)
        assert len(requests) == 1

    asyncio.run(exercise())
