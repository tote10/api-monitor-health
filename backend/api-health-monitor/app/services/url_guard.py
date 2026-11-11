"""Reject monitoring targets that could be used to probe internal networks."""

import ipaddress
import socket
from urllib.parse import urlsplit

from app.core.config import settings

ALLOWED_SCHEMES = ("http", "https")


class UnsafeTargetError(ValueError):
    """Raised when a URL cannot be monitored safely."""


def resolve_host(host: str) -> list[str]:
    """Resolve a hostname to every address it currently points at."""
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global


def validate_target_url(url: str) -> None:
    """Validate a monitor target or webhook URL before it is contacted.

    Scheme and host shape are always checked. When SSRF protection is enabled,
    every address the host resolves to must be publicly routable unless private
    targets are explicitly allowed.

    The address is validated, not pinned: the HTTP client resolves the hostname
    again when it connects, so a hostile DNS server could still answer
    differently at request time. Restrict outbound traffic at the network layer
    when that matters.
    """
    parsed = urlsplit(url)

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeTargetError(
            "Only http:// and https:// URLs can be monitored."
        )

    if parsed.username or parsed.password:
        raise UnsafeTargetError(
            "URLs with embedded credentials are not supported."
        )

    host = parsed.hostname
    if not host:
        raise UnsafeTargetError("The URL must include a hostname.")

    if not settings.SSRF_PROTECTION_ENABLED:
        return

    try:
        addresses = resolve_host(host)
    except (OSError, UnicodeError) as exc:
        # UnicodeError covers hostnames the IDNA codec cannot encode.
        raise UnsafeTargetError(
            f"Could not resolve the host '{host}'."
        ) from exc

    if not addresses:
        raise UnsafeTargetError(f"Could not resolve the host '{host}'.")

    if settings.ALLOW_PRIVATE_TARGETS:
        return

    for address in addresses:
        if not _is_public(address):
            raise UnsafeTargetError(
                f"'{host}' resolves to the private or reserved address "
                f"{address}, which is blocked to prevent SSRF."
            )
