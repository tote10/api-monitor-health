import asyncio
import socket
import ssl
from datetime import datetime, timezone
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network

import httpx

from app.core.config import settings

MAX_REDIRECTS = 20
MAX_RESPONSE_BYTES = 128 * 1024
IPV6_GLOBAL_UNICAST = ip_network("2000::/3")
IPV6_EXCLUDED_NETWORKS = (
    ip_network("2001::/23"),  # Special-purpose assignments including Teredo
    ip_network("2002::/16"),  # 6to4
    ip_network("3fff::/20"),  # Documentation
)


class UnsafeMonitorURL(ValueError):
    """The check target is outside the public HTTP policy."""


def _require_public_address(address: IPv4Address | IPv6Address) -> None:
    if settings.ENVIRONMENT == "development" and settings.ALLOW_LOOPBACK_TARGETS and address.is_loopback:
        return
    if isinstance(address, IPv6Address):
        if address.ipv4_mapped is not None:
            _require_public_address(address.ipv4_mapped)
            return
        if (
            address not in IPV6_GLOBAL_UNICAST
            or any(address in network for network in IPV6_EXCLUDED_NETWORKS)
            or address.scope_id is not None
        ):
            raise UnsafeMonitorURL("Only public IP addresses can be monitored")
    if not address.is_global or address.is_multicast or address.is_reserved:
        raise UnsafeMonitorURL("Only public IP addresses can be monitored")


def validate_target(url: httpx.URL) -> IPv4Address | IPv6Address | None:
    if url.scheme not in {"http", "https"} or not url.host:
        raise UnsafeMonitorURL("Monitoring requires an HTTP or HTTPS URL")
    if url.userinfo:
        raise UnsafeMonitorURL("Monitoring URLs cannot contain credentials")
    host = url.host.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local")):
        raise UnsafeMonitorURL("Local hostnames cannot be monitored")
    try:
        address = ip_address(host)
    except ValueError:
        return None
    _require_public_address(address)
    return address


async def resolve_public_addresses(url: httpx.URL) -> list[str]:
    literal = validate_target(url)
    if literal is not None:
        return [str(literal)]

    host = url.raw_host.decode("ascii")
    records = await asyncio.get_running_loop().getaddrinfo(
        host, url.port or (443 if url.scheme == "https" else 80),
        type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
    )
    addresses = []
    for _, _, _, _, sockaddr in records:
        address = ip_address(sockaddr[0])
        _require_public_address(address)
        addresses.append(str(address))
    if not addresses:
        raise UnsafeMonitorURL("Target hostname did not resolve to an IP address")
    return list(dict.fromkeys(addresses))


def pinned_request(
    method: str, url: httpx.URL, address: str, timeout: float, *, headers=None, **kwargs,
) -> httpx.Request:
    # Connect to the address we checked while keeping the original HTTP Host
    # and TLS certificate name. Connections are not reused across requests.
    return httpx.Request(
        method,
        url.copy_with(host=address),
        headers={**(headers or {}), "Host": url.netloc.decode("ascii")},
        extensions={
            "sni_hostname": url.raw_host.decode("ascii").rstrip("."),
            "timeout": httpx.Timeout(timeout).as_dict(),
        },
        **kwargs,
    )


def peer_certificate_expiry(response: httpx.Response) -> datetime | None:
    stream = response.extensions.get("network_stream")
    if stream is None:
        return None
    tls = stream.get_extra_info("ssl_object")
    if tls is None:
        return None
    try:
        value = tls.getpeercert().get("notAfter")
        return datetime.fromtimestamp(ssl.cert_time_to_seconds(value), timezone.utc) if value else None
    except (ValueError, OSError, OverflowError):
        return None


class ResponseTooLarge(ValueError):
    """The requested assertion exceeds the bounded response reader."""


async def fetch_public_url(
    client: httpx.AsyncClient, target: str, timeout: float,
    *, method: str = "GET", headers: dict[str, str] | None = None,
    body: str | None = None, read_body: bool = False,
) -> httpx.Response:
    async with asyncio.timeout(timeout):
        url = httpx.URL(target)
        request_headers = dict(headers or {})
        if read_body:
            request_headers["Accept-Encoding"] = "identity"
        certificate_expires_at = None
        for hop in range(MAX_REDIRECTS + 1):
            addresses = await resolve_public_addresses(url)
            for index, address in enumerate(addresses):
                try:
                    request = pinned_request(
                        method, url, address, timeout, headers=request_headers, content=body,
                    )
                    response = await client.send(request, follow_redirects=False, stream=True)
                    break
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    if index == len(addresses) - 1:
                        raise
            try:
                if hop == 0:
                    certificate_expires_at = peer_certificate_expiry(response)
                if not response.has_redirect_location or method not in {"GET", "HEAD"}:
                    content = bytearray()
                    if read_body:
                        if response.headers.get("content-encoding", "identity").lower() != "identity":
                            raise ResponseTooLarge("Assertions require an uncompressed response")
                        async for part in response.aiter_bytes(chunk_size=8192):
                            if len(content) + len(part) > MAX_RESPONSE_BYTES:
                                raise ResponseTooLarge("Response exceeds the assertion size limit")
                            content.extend(part)
                    return httpx.Response(
                        response.status_code, headers=response.headers, content=bytes(content),
                        extensions={"certificate_expires_at": certificate_expires_at},
                    )
                if hop == MAX_REDIRECTS:
                    break
                destination = url.join(response.headers["location"])
                validate_target(destination)
                if (url.scheme, url.host, url.port) != (destination.scheme, destination.host, destination.port):
                    request_headers = {"Accept-Encoding": "identity"} if read_body else {}
                    body = None
                url = destination
            finally:
                await response.aclose()
    raise UnsafeMonitorURL("Too many redirects")
