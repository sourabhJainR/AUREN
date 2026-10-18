"""Strict loopback-only HTTP primitives for local AUREN model endpoints.

Local inference endpoints are untrusted peers: validate their URL structurally and
never follow redirects, which could otherwise send prompts or probes off-device.
"""
from __future__ import annotations

from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def validate_loopback_base_url(value: str) -> str:
    """Return a canonical loopback HTTP origin or fail closed.

    Require an explicit valid port and forbid credentials, paths, query strings,
    fragments, non-loopback hosts, HTTPS ambiguity, and alternate host spellings.
    """
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("local model endpoint must be a valid loopback HTTP origin")
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise ValueError("local model endpoint is malformed") from exc
    if (
        parsed.scheme.lower() != "http"
        or hostname not in _ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1 <= port <= 65535
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("local model endpoint must use an explicit-port loopback HTTP origin")
    authority = f"[::1]:{port}" if hostname == "::1" else f"{hostname}:{port}"
    return f"http://{authority}"


class _NoRedirectHandler(HTTPRedirectHandler):
    """Refuse every redirect from a local model endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_loopback_request(request: Request, timeout: float):
    """Open a prevalidated loopback request without proxies or redirect following."""
    # Disable environment-configured proxies: local inference must remain local.
    opener = build_opener(ProxyHandler({}), _NoRedirectHandler())
    return opener.open(request, timeout=timeout)


__all__ = ["open_loopback_request", "validate_loopback_base_url"]
