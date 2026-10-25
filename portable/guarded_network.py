"""Socket-level HTTPS destination enforcement for configured network scopes.

The caller must supply an explicit host or CIDR scope. DNS answers are resolved
once, validated as a set, and pinned to an IP address for the TCP connection;
TLS still validates the original hostname. This primitive must be used by each
real network adapter. It does not transparently intercept arbitrary SDKs or
processes; OS egress policy remains the independent backstop.
"""
from __future__ import annotations

import http.client
import ipaddress
import math
import re
import socket
import ssl
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence
from urllib.parse import urlparse


class NetworkPolicyDenied(PermissionError):
    """A destination could not be proven to fit the configured network scope."""


@dataclass(frozen=True)
class PinnedHTTPSDestination:
    hostname: str
    port: int
    address: str


def _parse_networks(values: Sequence[str]) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    try:
        return tuple(ipaddress.ip_network(value, strict=False) for value in values)
    except (TypeError, ValueError) as exc:
        raise NetworkPolicyDenied("network policy contains an invalid CIDR") from exc


def resolve_https_destination(
    url: str,
    *,
    allowed_hosts: Sequence[str],
    allowed_cidrs: Sequence[str] = (),
    resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
) -> tuple[PinnedHTTPSDestination, ...]:
    """Resolve and authorize every DNS answer before any connection is attempted."""
    if not isinstance(url, str) or not url.strip():
        raise NetworkPolicyDenied("HTTPS destination URL is required")
    hosts = {host.strip().lower().rstrip(".") for host in allowed_hosts if isinstance(host, str) and host.strip()}
    networks = _parse_networks(allowed_cidrs)
    if not hosts and not networks:
        raise NetworkPolicyDenied("explicit allowed_hosts or allowed_cidrs scope is required")
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        port = parsed.port or 443
    except (ValueError, UnicodeError) as exc:
        raise NetworkPolicyDenied("invalid HTTPS destination") from exc
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not 1 <= port <= 65535
    ):
        raise NetworkPolicyDenied("only credential-free HTTPS destinations are allowed")
    if hosts and hostname not in hosts:
        raise NetworkPolicyDenied("destination hostname is outside the configured allowlist")

    try:
        if _is_ip_literal(hostname):
            addresses = [ipaddress.ip_address(hostname)]
        else:
            answers = resolver(hostname, port, type=socket.SOCK_STREAM)
            addresses = []
            for answer in answers:
                sockaddr = answer[4]
                addresses.append(ipaddress.ip_address(sockaddr[0]))
    except (OSError, ValueError, IndexError, TypeError) as exc:
        raise NetworkPolicyDenied("destination DNS resolution failed closed") from exc
    unique = tuple(dict.fromkeys(addresses))
    if not unique:
        raise NetworkPolicyDenied("destination resolved to no addresses")

    destinations: list[PinnedHTTPSDestination] = []
    for address in unique:
        in_scope = any(address.version == network.version and address in network for network in networks)
        if networks and not in_scope:
            raise NetworkPolicyDenied("a DNS answer is outside the configured CIDR scope")
        if not networks and not address.is_global:
            raise NetworkPolicyDenied("non-public network destinations require an explicit CIDR scope")
        destinations.append(PinnedHTTPSDestination(hostname, port, str(address)))
    return tuple(destinations)


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def connect_https_pinned(
    url: str,
    *,
    allowed_hosts: Sequence[str],
    allowed_cidrs: Sequence[str] = (),
    timeout: float = 10.0,
    resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
    connector: Callable[..., socket.socket] = socket.create_connection,
    tls_context: ssl.SSLContext | None = None,
) -> ssl.SSLSocket:
    """Connect to a validated IP without a second DNS lookup, then verify TLS SNI."""
    destinations = resolve_https_destination(
        url, allowed_hosts=allowed_hosts, allowed_cidrs=allowed_cidrs, resolver=resolver,
    )
    context = tls_context or ssl.create_default_context()
    errors: list[OSError] = []
    for destination in destinations:
        raw = None
        try:
            raw = connector((destination.address, destination.port), timeout=timeout)
            return context.wrap_socket(raw, server_hostname=destination.hostname)
        except OSError as exc:
            errors.append(exc)
            if raw is not None:
                try:
                    raw.close()
                except OSError:
                    pass
    raise NetworkPolicyDenied("all authorized destination addresses failed to connect") from (errors[-1] if errors else None)


def guarded_https_get(
    url: str,
    *,
    allowed_hosts: Sequence[str],
    allowed_cidrs: Sequence[str] = (),
    headers: dict[str, str] | None = None,
    method: str = "GET",
    body: bytes | None = None,
    timeout: float = 10.0,
    max_bytes: int = 2 * 1024 * 1024,
    resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
    connector: Callable[..., socket.socket] = socket.create_connection,
    tls_context: ssl.SSLContext | None = None,
) -> tuple[int, bytes]:
    """Perform one bounded HTTPS request through a validated, DNS-pinned socket.

    GET and HEAD requests cannot carry bodies. POST and PUT may carry a bytes
    body. Redirects are not followed; every destination is explicitly scoped.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise NetworkPolicyDenied("timeout must be finite and positive")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= 8 * 1024 * 1024:
        raise NetworkPolicyDenied("response limit is outside the supported range")
    method = str(method).upper()
    if method not in {"GET", "HEAD", "POST", "PUT"}:
        raise NetworkPolicyDenied("HTTP method is not supported by the guarded transport")
    if body is not None and not isinstance(body, bytes):
        raise NetworkPolicyDenied("HTTP body must be bytes")
    if body is not None and method in {"GET", "HEAD"}:
        raise NetworkPolicyDenied("GET and HEAD requests cannot carry a body")
    parsed = urlparse(url)
    request_target = parsed.path or "/"
    if parsed.query:
        request_target += "?" + parsed.query
    if "\r" in request_target or "\n" in request_target:
        raise NetworkPolicyDenied("invalid HTTP request target")
    safe_headers: list[tuple[str, str]] = []
    for name, value in (headers or {}).items():
        if (
            not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_~0-9A-Za-z-]+", name)
            or name.lower() in {"host", "connection", "content-length", "transfer-encoding"}
            or not isinstance(value, str) or "\r" in value or "\n" in value
        ):
            raise NetworkPolicyDenied("invalid or forbidden HTTP header")
        safe_headers.append((name, value))
    if method in {"POST", "PUT"}:
        safe_headers.append(("Content-Length", str(len(body or b""))))
    destinations = resolve_https_destination(
        url, allowed_hosts=allowed_hosts, allowed_cidrs=allowed_cidrs, resolver=resolver,
    )
    context = tls_context or ssl.create_default_context()
    errors: list[OSError] = []
    for item in destinations:
        raw = None
        try:
            raw = connector((item.address, item.port), timeout=float(timeout))
            with context.wrap_socket(raw, server_hostname=item.hostname) as secure:
                lines = [
                    f"{method} {request_target} HTTP/1.1",
                    f"Host: {item.hostname}" + (f":{item.port}" if item.port != 443 else ""),
                    "Connection: close",
                    *[f"{name}: {value}" for name, value in safe_headers],
                    "",
                    "",
                ]
                secure.sendall("\r\n".join(lines).encode("ascii", errors="strict") + (body or b""))
                response = http.client.HTTPResponse(secure)
                response.begin()
                return int(response.status), response.read(max_bytes + 1)
        except (OSError, ssl.SSLError, UnicodeError, http.client.HTTPException) as exc:
            errors.append(exc if isinstance(exc, OSError) else OSError(type(exc).__name__))
            if raw is not None:
                try:
                    raw.close()
                except OSError:
                    pass
    raise NetworkPolicyDenied("authorized HTTPS request failed") from (errors[-1] if errors else None)




guarded_https_request = guarded_https_get


__all__ = [
    "NetworkPolicyDenied",
    "PinnedHTTPSDestination",
    "connect_https_pinned",
    "guarded_https_get",
    "guarded_https_request",
    "resolve_https_destination",
]
