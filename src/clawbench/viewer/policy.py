"""Pure request-policy helpers used by visual capture."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlparse


SAFE_METHODS = {"GET", "HEAD"}


@dataclass(frozen=True)
class RequestDecision:
    allow: bool
    reason: str


def host_matches(host: str, allowed_hosts: set[str]) -> bool:
    normalized = host.rstrip(".").lower()
    return any(
        normalized == allowed.rstrip(".").lower()
        or normalized.endswith(f".{allowed.rstrip('.').lower()}")
        for allowed in allowed_hosts
    )


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def request_decision(
    method: str,
    url: str,
    allowed_hosts: set[str],
    *,
    loopback_only: bool = False,
) -> RequestDecision:
    if method.upper() not in SAFE_METHODS:
        return RequestDecision(False, "blocked_mutating_method")
    parsed = urlparse(url)
    host = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not host:
        return RequestDecision(False, "blocked_non_http")
    if loopback_only:
        return RequestDecision(is_loopback(host), "allowed" if is_loopback(host) else "blocked_external_host")
    if not host_matches(host, allowed_hosts):
        return RequestDecision(False, "blocked_unlisted_host")
    return RequestDecision(True, "allowed")
