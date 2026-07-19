"""Capture-integrity rules shared by visual evidence producers.

These helpers deliberately classify evidence quality; they never calculate an
official WebsiteBench score. Keeping them pure also makes capture policy easy
to exercise without launching a browser.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


class CaptureError(RuntimeError):
    """Raised when a visual checkpoint cannot be captured safely."""


NONVISUAL_HOST_SUFFIXES = (
    "google-analytics.com",
    "googletagmanager.com",
    "clarity.ms",
    "hotjar.com",
    "mixpanel.com",
    "segment.com",
    "segment.io",
    "amplitude.com",
    "sentry.io",
    "datadoghq.com",
    "datadoghq-browser-agent.com",
    "newrelic.com",
    "nr-data.net",
    "bugsnag.com",
    "appsflyer.com",
    "profitwell.com",
    "marker.io",
)
NONVISUAL_URL_MARKERS = ("paypalobjects.com/en_us/i/scr/pixel.gif",)
VISUAL_RESOURCE_TYPES = {"document", "stylesheet", "image", "font", "media"}
BEHAVIORAL_RESOURCE_TYPES = {
    "script",
    "xhr",
    "fetch",
    "eventsource",
    "websocket",
    "manifest",
}


def _host_has_suffix(host: str, suffixes: tuple[str, ...]) -> bool:
    normalized = host.rstrip(".").lower()
    return any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in suffixes
    )


def _is_nonvisual_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    lowered_url = url.lower()
    return _host_has_suffix(host, NONVISUAL_HOST_SUFFIXES) or any(
        marker in lowered_url for marker in NONVISUAL_URL_MARKERS
    )


def classify_blocked_request(
    method: str,
    url: str,
    resource_type: str,
    reason: str,
) -> str:
    """Describe how a blocked request may affect screenshot fidelity."""

    normalized_type = resource_type.lower()
    if _is_nonvisual_url(url):
        return "nonvisual"
    if normalized_type in VISUAL_RESOURCE_TYPES:
        return "visual"
    if normalized_type in BEHAVIORAL_RESOURCE_TYPES:
        return "behavioral"
    if method.upper() not in {"GET", "HEAD"} or reason == "blocked_mutating_method":
        return "behavioral"
    return "unknown"


def classify_network_failure(url: str, resource_type: str) -> str:
    normalized_type = resource_type.lower()
    if _is_nonvisual_url(url):
        return "nonvisual"
    if normalized_type in VISUAL_RESOURCE_TYPES:
        return "visual"
    if normalized_type in BEHAVIORAL_RESOURCE_TYPES:
        return "behavioral"
    return "unknown"


def classify_page_error(
    message: str,
    blocked_requests: list[dict[str, str]],
) -> str:
    """Attribute a page error to the blocked request named in its message."""

    lowered = message.lower()
    for request in blocked_requests:
        request_url = request.get("url", "")
        if request_url and request_url.lower() in lowered:
            return request.get("impact", "unknown")
    return "behavioral"


def decide_capture_status(
    *,
    side: str,
    image_available: bool,
    soft_error: str | None,
    access_gate: str | None,
    blank_viewport: bool,
    screenshot_error: str | None,
    navigation_error: str | None,
    status_code: int | None,
    has_quality_issue: bool,
) -> str:
    """Map capture evidence to a state without discarding usable images."""

    if side not in {"source", "candidate"}:
        raise ValueError("side must be source or candidate")
    if not image_available:
        if side == "source" and navigation_error and not screenshot_error:
            return "blocked"
        return "failed"
    if soft_error or access_gate or blank_viewport:
        return "blocked" if side == "source" else "failed"
    if status_code is not None and status_code >= 400:
        return "blocked" if side == "source" else "failed"
    if navigation_error or has_quality_issue:
        return "degraded"
    return "captured"


def detect_soft_error(title: str | None, heading: str | None) -> str | None:
    """Detect a successful-HTTP error shell that must not be compared."""

    values = [value.strip().lower() for value in (title, heading) if value and value.strip()]
    markers = (
        "page not found",
        "404 not found",
        "404 error",
        "error 404",
        "this page doesn't exist",
        "this page does not exist",
    )
    for value in values:
        if value == "not found" or value == "404" or any(
            marker in value for marker in markers
        ):
            return next((marker for marker in markers if marker in value), value)
    return None


def detect_access_gate(
    title: str | None,
    heading: str | None,
    body_text: str | None,
) -> str | None:
    """Detect access-denied, bot-check, and browser-verification shells."""

    primary = " ".join(
        value.strip().lower()
        for value in (title, heading)
        if value and value.strip()
    )
    body = (body_text or "").strip().lower()
    evidence = f"{primary} {body[:5000] if len(body) <= 3000 else ''}"
    markers = (
        "access denied",
        "verify you are human",
        "checking your browser",
        "please verify you are a human",
        "unusual traffic",
        "enable javascript and cookies to continue",
        "security verification",
        "request blocked",
        "temporarily blocked",
    )
    if "just a moment" in evidence and (
        "cloudflare" in evidence or "performing security verification" in evidence
    ):
        return "cloudflare verification"
    return next((marker for marker in markers if marker in evidence), None)


def comparison_readiness(
    source: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[str, str | None]:
    """Decide whether two captures are safe to compare diagnostically."""

    statuses = {source.get("status"), candidate.get("status")}
    if "blocked" in statuses:
        blocked_side = "source" if source.get("status") == "blocked" else "candidate"
        blocked_capture = source if blocked_side == "source" else candidate
        detail = blocked_capture.get("message")
        if not detail and blocked_capture.get("http_status"):
            detail = f"HTTP {blocked_capture['http_status']}"
        suffix = f": {detail}" if detail else ""
        return "blocked", f"{blocked_side} capture blocked{suffix}"
    if "failed" in statuses:
        return (
            "failed",
            source.get("message") or candidate.get("message") or "capture failed",
        )
    if not source.get("image") or not candidate.get("image"):
        return "failed", "one or both screenshots are unavailable"
    if statuses == {"captured"}:
        return "captured", None
    return "degraded", "one or both screenshots contain degraded page evidence"


def reviewability_for_status(status: str) -> str:
    if status == "captured":
        return "reliable"
    if status == "degraded":
        return "caution"
    return "unavailable"
