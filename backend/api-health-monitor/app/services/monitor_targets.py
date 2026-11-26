import asyncio
import socket
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network

import httpx

MAX_REDIRECTS = 20
IPV6_GLOBAL_UNICAST = ip_network("2000::/3")
IPV6_EXCLUDED_NETWORKS = (
    ip_network("2001::/23"),  # Special-purpose assignments including Teredo
    ip_network("2002::/16"),  # 6to4
    ip_network("3fff::/20"),  # Documentation
)


class UnsafeMonitorURL(ValueError):
    """The check target is outside the public HTTP policy."""


def _require_public_address(address: IPv4Address | IPv6Address) -> None:
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


async def _get_at_address(
    client: httpx.AsyncClient, url: httpx.URL, address: str, timeout: float,
) -> httpx.Response:
    # Connect to the address we checked while keeping the original HTTP Host
    # and TLS certificate name. Connections are not reused across requests.
    request = httpx.Request(
        "GET",
        url.copy_with(host=address),
        headers={"Host": url.netloc.decode("ascii")},
        extensions={
            "sni_hostname": url.raw_host.decode("ascii").rstrip("."),
            "timeout": httpx.Timeout(timeout).as_dict(),
        },
    )
    return await client.send(request, follow_redirects=False)


async def fetch_public_url(
    client: httpx.AsyncClient, target: str, timeout: float,
) -> httpx.Response:
    async with asyncio.timeout(timeout):
        url = httpx.URL(target)
        for hop in range(MAX_REDIRECTS + 1):
            addresses = await resolve_public_addresses(url)
            for index, address in enumerate(addresses):
                try:
                    response = await _get_at_address(client, url, address, timeout)
                    break
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    if index == len(addresses) - 1:
                        raise
            if not response.has_redirect_location:
                return response
            if hop == MAX_REDIRECTS:
                break
            url = url.join(response.headers["location"])
    raise UnsafeMonitorURL("Too many redirects")
