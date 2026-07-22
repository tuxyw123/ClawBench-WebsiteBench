#!/usr/bin/env python3
"""Capture redacted, GET-only public Amazon source evidence.

Raw source HTML and screenshots remain in the caller-owned output directory.
The committed report contains hashes, dimensions, DOM/style summaries, and
redacted network/media metadata only.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import http.client
import json
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request as UrlRequest, urlopen

from playwright.sync_api import Browser, Page, Request, Response, sync_playwright


BEST_SELLERS_SOURCE_URL = (
    "https://us.amazon.com/Best-Sellers-External-Solid-State-Drives/zgbs/pc/3015429011"
)
CART_SOURCE_URL = "https://www.amazon.com/gp/cart/view.html"


@dataclass(frozen=True)
class PageSpec:
    name: str
    url: str
    state: str = "loaded"


PAGES = (
    PageSpec("storefront-home-live", "https://www.amazon.com/"),
    PageSpec("storefront-department-drawer-live", "https://www.amazon.com/", "menu"),
    PageSpec("storefront-search-autocomplete-live", "https://www.amazon.com/", "autocomplete"),
    PageSpec("all-departments-best-sellers-live", "https://www.amazon.com/Best-Sellers/zgbs"),
    PageSpec("best-sellers-external-ssd-live", BEST_SELLERS_SOURCE_URL),
    PageSpec("portable-ssd-search-live", "https://www.amazon.com/s?k=portable+ssd"),
    PageSpec(
        "portable-ssd-filtered-search-live",
        "https://www.amazon.com/s?k=portable+ssd&i=computers&rh=n%3A1292110011&s=review-rank",
    ),
    PageSpec(
        "catalog-no-results-live",
        "https://www.amazon.com/s?k=clawbench-impossible-product-9f3a8c",
    ),
    PageSpec(
        "computers-category-live",
        "https://www.amazon.com/computers-pc-hardware-accessories-add-ons/b/?node=541966",
    ),
    PageSpec(
        "electronics-category-live",
        "https://www.amazon.com/electronics-store/b/?node=172282",
    ),
    PageSpec(
        "home-kitchen-category-live",
        "https://www.amazon.com/home-garden-kitchen-furniture-bedding/b/?node=1055398",
    ),
    PageSpec(
        "books-category-live",
        "https://www.amazon.com/books-used-books-textbooks/b/?node=283155",
    ),
    PageSpec("todays-deals-live", "https://www.amazon.com/gp/goldbox/"),
    PageSpec(
        "samsung-t7-product-live-boundary",
        "https://us.amazon.com/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8",
    ),
    PageSpec("empty-cart-live", CART_SOURCE_URL),
    PageSpec("account-entry-live", "https://www.amazon.com/gp/css/homepage.html"),
    PageSpec("orders-entry-live", "https://www.amazon.com/gp/css/order-history"),
    PageSpec("lists-entry-live", "https://www.amazon.com/hz/wishlist/ls"),
    PageSpec(
        "not-found-live",
        "https://www.amazon.com/clawbench-local-replica-source-evidence-not-found",
    ),
)

VIEWPORTS = {
    "desktop": {
        "viewport": {"width": 1365, "height": 900},
        "user_agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
        "is_mobile": False,
        "has_touch": False,
    },
    "desktop-compact": {
        "viewport": {"width": 1024, "height": 768},
        "user_agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
        "is_mobile": False,
        "has_touch": False,
    },
    "tablet": {
        "viewport": {"width": 768, "height": 1024},
        "user_agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
        "is_mobile": False,
        "has_touch": True,
    },
    "mobile": {
        "viewport": {"width": 390, "height": 844},
        "user_agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 "
            "Mobile/15E148 Safari/604.1"
        ),
        "is_mobile": True,
        "has_touch": True,
    },
    "mobile-small": {
        "viewport": {"width": 320, "height": 568},
        "user_agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 "
            "Mobile/15E148 Safari/604.1"
        ),
        "is_mobile": True,
        "has_touch": True,
    },
}

PRODUCT_SOURCE_URL = (
    "https://us.amazon.com/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8"
)
PRODUCT_MOBILE_SOURCE_URL = "https://us.amazon.com/gp/aw/d/B0874XN4D8"

AMAZON_CONTROLLED_SUFFIXES = (
    ".amazon.com",
    ".media-amazon.com",
    ".ssl-images-amazon.com",
    ".amazon-adsystem.com",
)
SENSITIVE_QUERY = re.compile(
    r"token|session|cookie|auth|csrf|verify|signature|credential|key|id$",
    re.IGNORECASE,
)
SENSITIVE_TEXT = re.compile(
    r"(session-id|csrf|token|cookie|authorization|password)[\"'=:\s]+[^\s\"'&<]+",
    re.IGNORECASE,
)
LONG_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9])[A-Fa-f0-9]{32,}(?![A-Za-z0-9])")
CAPTURED_RESOURCE_TYPES = {
    "document",
    "stylesheet",
    "script",
    "image",
    "media",
    "font",
    "xhr",
    "fetch",
    "manifest",
}
MOBILE_VIEWPORTS = {"mobile", "mobile-small"}


class EvidenceStore:
    """Private, content-addressed storage for caller-owned source evidence."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.objects = root / "objects"
        self.pages = root / "pages"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.pages.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        os.chmod(self.objects, 0o700)
        os.chmod(self.pages, 0o700)
        existing = [path for path in self.objects.glob("*/*") if path.is_file()]
        self.unique_objects: set[str] = {path.name for path in existing}
        self.unique_bytes = sum(path.stat().st_size for path in existing)

    def put(self, body: bytes) -> dict[str, Any]:
        digest = sha256(body)
        relative = Path("objects") / digest[:2] / digest
        destination = self.root / relative
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(destination.parent, 0o700)
            destination.write_bytes(body)
            os.chmod(destination, 0o600)
            self.unique_bytes += len(body)
        self.unique_objects.add(digest)
        return {
            "sha256": digest,
            "bytes": len(body),
            "objectPath": relative.as_posix(),
        }

    def put_json(self, value: Any) -> dict[str, Any]:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        return self.put(body)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sanitize_url(raw: str, *, keep_public_query: bool = False) -> str:
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return "<invalid-url>"
    query: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if SENSITIVE_QUERY.search(key):
            query.append((key, "<redacted>"))
        elif keep_public_query and key in {"k", "node", "ref", "psc", "qid", "sort"}:
            query.append((key, value[:160]))
        else:
            query.append((key, "<present>"))
    path = LONG_IDENTIFIER.sub("<redacted-id>", parsed.path)
    return urlunsplit((parsed.scheme, parsed.netloc, path, urlencode(query), ""))


def amazon_controlled(raw: str) -> bool:
    host = (urlsplit(raw).hostname or "").lower()
    return host == "amazon.com" or any(host.endswith(suffix) for suffix in AMAZON_CONTROLLED_SUFFIXES)


def trim(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = SENSITIVE_TEXT.sub(r"\1=<redacted>", text)
    return text[:limit]


def fetch_public_html(url: str) -> tuple[bytes, dict[str, Any]]:
    best_partial: tuple[bytes, dict[str, Any]] | None = None
    last_error: BaseException | None = None
    for attempt in range(1, 4):
        request = UrlRequest(
            url,
            method="GET",
            headers={
                "User-Agent": VIEWPORTS["desktop"]["user_agent"],
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "identity",
            },
        )
        try:
            with urlopen(request, timeout=60) as response:
                incomplete = False
                try:
                    body = response.read()
                except http.client.IncompleteRead as error:
                    body = error.partial
                    incomplete = True
                    last_error = error
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    try:
                        body = gzip.decompress(body)
                    except (EOFError, OSError) as error:
                        incomplete = True
                        last_error = error
                metadata = {
                    "url": sanitize_url(response.url, keep_public_query=True),
                    "method": "GET",
                    "status": response.status,
                    "contentType": trim(response.headers.get("Content-Type", ""), 180),
                    "responseBytes": len(body),
                    "sha256": sha256(body),
                    "cacheControl": trim(response.headers.get("Cache-Control", ""), 180),
                    "etagPresent": bool(response.headers.get("ETag")),
                    "lastModified": trim(response.headers.get("Last-Modified", ""), 120),
                    "cookiesHeadersAndTokensOmitted": True,
                    "incompleteRead": incomplete,
                    "captureAttempt": attempt,
                }
                if not incomplete:
                    return body, metadata
                if best_partial is None or len(body) > len(best_partial[0]):
                    best_partial = (body, metadata)
        except Exception as error:  # pragma: no cover - source network is unstable
            last_error = error
        if attempt < 3:
            time.sleep(1)
    if best_partial is not None:
        body, metadata = best_partial
        metadata["fallbackToLargestPartialResponse"] = True
        metadata["terminalReadError"] = type(last_error).__name__ if last_error else None
        return body, metadata
    if last_error is not None:
        raise last_error
    raise RuntimeError("public HTML fetch ended without a response")


def load_caller_snapshot(
    path: Path,
    url: str,
    *,
    minimum_bytes: int,
    required_markers: tuple[str, ...],
) -> tuple[bytes, dict[str, Any]]:
    body = path.read_bytes()
    decoded = body.decode("utf-8", errors="replace")
    if len(body) < minimum_bytes or any(marker not in decoded for marker in required_markers):
        raise AssertionError(f"{path} is not the complete expected public response")
    return body, {
        "url": url,
        "method": "GET",
        "status": 200,
        "contentType": "text/html",
        "responseBytes": len(body),
        "sha256": sha256(body),
        "cacheControl": "not retained",
        "etagPresent": False,
        "lastModified": "",
        "cookiesHeadersAndTokensOmitted": True,
        "callerOwnedSnapshotCommitted": False,
    }


def inject_base(html: bytes, source_url: str) -> str:
    text = html.decode("utf-8", errors="replace")
    base = f'<base href="{source_url}">'
    if re.search(r"<head(?:\s[^>]*)?>", text, flags=re.IGNORECASE):
        return re.sub(
            r"(<head(?:\s[^>]*)?>)",
            lambda match: match.group(1) + base,
            text,
            count=1,
            flags=re.IGNORECASE,
        )
    return base + text


def dom_snapshot(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """
        () => {
          const root = document.documentElement;
          const body = document.body;
          const selectors = [
            '#navbar', '#nav-belt', '#nav-main', '#nav-search', 'main', 'h1',
            '#zg', '#gridItemRoot', '#productTitle', '#dp', '#leftCol',
            '#centerCol', '#rightCol', '#buybox', '#add-to-cart-button',
            '#quantity', '#sc-active-cart', '#sc-retail-cart-container',
            '[data-component-type="s-search-result"]', '#s-refinements',
            '.s-pagination-container', '.a-carousel', '#gw-layout',
            '#desktop-grid-1', '#nav-flyout-searchAjax', '#hmenu-container',
            '#your-orders-content', '#wishlist-page', 'footer'
          ];
          const styleKeys = [
            'display', 'position', 'fontFamily', 'fontSize', 'fontWeight',
            'lineHeight', 'color', 'backgroundColor', 'borderRadius',
            'padding', 'margin', 'gap', 'gridTemplateColumns', 'overflowX'
          ];
          const styleRecords = [];
          for (const selector of selectors) {
            const elements = [...document.querySelectorAll(selector)].slice(0, 8);
            for (const [index, element] of elements.entries()) {
              const rect = element.getBoundingClientRect();
              const style = getComputedStyle(element);
              const values = {};
              for (const key of styleKeys) values[key] = style[key];
              styleRecords.push({
                selector, index,
                tag: element.tagName.toLowerCase(),
                id: element.id || '',
                classes: [...element.classList].slice(0, 10),
                text: (element.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 180),
                rect: {
                  x: Math.round(rect.x * 100) / 100,
                  y: Math.round(rect.y * 100) / 100,
                  width: Math.round(rect.width * 100) / 100,
                  height: Math.round(rect.height * 100) / 100
                },
                visible: rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden',
                computed: values
              });
            }
          }
          const visible = element => {
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const labels = [...document.querySelectorAll('h1,h2,h3,button,[role="button"],input[type="submit"]')]
            .filter(visible).slice(0, 120).map(element => ({
              tag: element.tagName.toLowerCase(),
              id: element.id || '',
              text: (element.innerText || element.value || element.getAttribute('aria-label') || '')
                .replace(/\\s+/g, ' ').trim().slice(0, 220)
            })).filter(record => record.text);
          const forms = [...document.forms].slice(0, 80).map(form => ({
            id: form.id || '',
            method: (form.method || 'get').toUpperCase(),
            action: new URL(form.action || location.href, location.href).origin + new URL(form.action || location.href, location.href).pathname,
            fields: [...form.elements].map(field => field.name).filter(Boolean).slice(0, 80)
          }));
          const images = [...document.images].slice(0, 500).map(image => {
            const rect = image.getBoundingClientRect();
            return {
              src: image.currentSrc || image.src,
              alt: (image.alt || '').replace(/\\s+/g, ' ').trim().slice(0, 240),
              naturalWidth: image.naturalWidth,
              naturalHeight: image.naturalHeight,
              renderedWidth: Math.round(rect.width * 100) / 100,
              renderedHeight: Math.round(rect.height * 100) / 100,
              visible: visible(image),
              inViewport: rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth
            };
          });
          const bodyText = (body?.innerText || '').replace(/\\s+/g, ' ').trim();
          const main = document.querySelector('main, #pageContent, #dp, #zg, #sc-active-cart');
          const mainText = (main?.innerText || '').replace(/\\s+/g, ' ').trim();
          const deliveryText = [
            '#glow-ingress-line1', '#glow-ingress-line2', '#contextualIngressPtLabel_deliveryShortLine',
            '#contextualIngressPtLabel_deliveryShortLine span'
          ].map(selector => document.querySelector(selector)?.textContent || '')
            .join(' ').replace(/\\s+/g, ' ').trim();
          const currencySamples = [...new Set(
            [...bodyText.matchAll(/(?:USD|EUR|CAD|US\\$|CA\\$|\\$|€)\\s?\\d[\\d,.]*/g)]
              .slice(0, 20).map(match => match[0])
          )];
          const accessGatePatterns = [
            'robot check', 'enter the characters you see below',
            'sorry, we just need to make sure', 'automated access to amazon data'
          ];
          const loweredBody = bodyText.toLowerCase();
          return {
            title: document.title,
            lang: root?.lang || '',
            bodyClasses: body ? [...body.classList] : [],
            dimensions: {
              viewportWidth: innerWidth,
              viewportHeight: innerHeight,
              documentWidth: root?.scrollWidth || 0,
              documentHeight: root?.scrollHeight || 0
            },
            counts: {
              elements: document.querySelectorAll('*').length,
              links: document.links.length,
              forms: document.forms.length,
              buttons: document.querySelectorAll('button,[role="button"],input[type="submit"]').length,
              images: document.images.length
            },
            captureQuality: {
              bodyTextLength: bodyText.length,
              mainTextLength: mainText.length,
              mainPresent: Boolean(main),
              mainHydrated: mainText.length >= 100,
              accessGate: accessGatePatterns.find(pattern => loweredBody.includes(pattern)) || null
            },
            regionalSignals: {
              deliveryText: deliveryText.slice(0, 240),
              currencySamples
            },
            headingsAndControls: labels,
            forms,
            images,
            styles: styleRecords
          };
        }
        """
    )


def normalize_dom(snapshot: dict[str, Any]) -> dict[str, Any]:
    for image in snapshot["images"]:
        image["src"] = sanitize_url(image["src"], keep_public_query=True)
        image["alt"] = trim(image["alt"])
    for record in snapshot["headingsAndControls"]:
        record["text"] = trim(record["text"])
    for record in snapshot["styles"]:
        record["text"] = trim(record["text"])
    for form in snapshot["forms"]:
        form["action"] = sanitize_url(form["action"])
    snapshot["title"] = trim(snapshot["title"], 500)
    snapshot["regionalSignals"]["deliveryText"] = trim(
        snapshot["regionalSignals"]["deliveryText"], 240
    )
    snapshot["regionalSignals"]["currencySamples"] = [
        trim(value, 40) for value in snapshot["regionalSignals"]["currencySamples"]
    ]
    return snapshot


def apply_capture_state(page: Page, state: str) -> dict[str, Any]:
    if state == "loaded":
        return {"requested": state, "applied": True, "detail": "initial page state"}
    try:
        if state == "menu":
            trigger = page.locator(
                "#nav-hamburger-menu, [data-action='a-dropdown-button'], "
                "button[aria-label*='menu' i]"
            ).first
            trigger.wait_for(state="visible", timeout=4_000)
            trigger.click(timeout=4_000)
            page.wait_for_timeout(1_500)
            return {"requested": state, "applied": True, "detail": "menu trigger clicked"}
        if state == "autocomplete":
            search = page.locator("#twotabsearchtextbox, input[type='search']").first
            search.wait_for(state="visible", timeout=4_000)
            search.fill("portable ssd")
            page.wait_for_timeout(2_000)
            return {
                "requested": state,
                "applied": True,
                "detail": "public search input filled without submission",
            }
        return {"requested": state, "applied": False, "detail": "unknown state"}
    except Exception as error:  # pragma: no cover - source DOM is intentionally unstable
        return {
            "requested": state,
            "applied": False,
            "detail": f"{type(error).__name__}: {trim(error, 240)}",
        }


def accessibility_snapshot(context: Any, page: Page) -> dict[str, Any]:
    session = context.new_cdp_session(page)
    try:
        value = session.send("Accessibility.getFullAXTree")
    finally:
        session.detach()
    if not isinstance(value, dict):
        return {"nodes": []}
    return value


def store_response_bodies(
    handles: list[tuple[Response, dict[str, Any]]],
    store: EvidenceStore,
    *,
    max_response_bytes: int,
) -> dict[str, int]:
    attempted = 0
    stored = 0
    failed = 0
    skipped = 0
    incomplete = 0
    for response, row in handles:
        request_finished = bool(row.pop("_requestFinished", False))
        request_failure = row.pop("_requestFailure", None)
        if (
            row["method"] != "GET"
            or row["resourceType"] not in CAPTURED_RESOURCE_TYPES
        ):
            row["bodyStorage"] = "not-in-capture-scope"
            skipped += 1
            continue
        attempted += 1
        if not request_finished:
            row["bodyStorage"] = (
                "request-failed-before-body-complete"
                if request_failure
                else "not-finished-at-capture"
            )
            row["bodyHashError"] = request_failure or "response-stream-incomplete"
            incomplete += 1
            continue
        try:
            body = response.body()
            if max_response_bytes and len(body) > max_response_bytes:
                row["bodyStorage"] = "over-explicit-size-limit"
                row["bodyHashError"] = "response-over-explicit-size-limit"
                row["responseBytes"] = len(body)
                failed += 1
                continue
            reference = store.put(body)
            row["responseBytes"] = reference["bytes"]
            row["sha256"] = reference["sha256"]
            row["objectPath"] = reference["objectPath"]
            row["bodyHashed"] = True
            row["bodyStored"] = True
            row["bodyStorage"] = "private-content-addressed-object"
            stored += 1
        except Exception as error:  # pragma: no cover - response lifecycle is network-dependent
            row["bodyStorage"] = "unavailable"
            row["bodyHashError"] = type(error).__name__
            failed += 1
    return {
        "attempted": attempted,
        "stored": stored,
        "failed": failed,
        "skipped": skipped,
        "incomplete": incomplete,
    }


def finalize_response_rows(rows: list[dict[str, Any]]) -> None:
    """Close the capture boundary for responses observed during final callbacks."""

    for row in rows:
        request_finished = bool(row.pop("_requestFinished", False))
        request_failure = row.pop("_requestFailure", None)
        if row.get("bodyStorage"):
            continue
        if (
            row.get("method") != "GET"
            or row.get("resourceType") not in CAPTURED_RESOURCE_TYPES
        ):
            row["bodyStorage"] = "not-in-capture-scope"
            continue
        row["bodyStorage"] = (
            "request-failed-before-body-complete"
            if request_failure
            else "not-finished-at-capture"
        )
        row["bodyHashError"] = (
            request_failure
            or (
                "response-observed-after-body-capture"
                if request_finished
                else "response-stream-incomplete"
            )
        )


def capture_screenshot(
    page: Page,
    path: Path,
    *,
    full_page: bool,
) -> tuple[bytes | None, str | None]:
    errors: list[str] = []
    for animations, timeout in (("disabled", 20_000), ("allow", 20_000)):
        try:
            page.screenshot(
                path=str(path),
                full_page=full_page,
                animations=animations,
                caret="hide",
                timeout=timeout,
            )
            body = path.read_bytes()
            os.chmod(path, 0o600)
            return body, None
        except Exception as error:  # pragma: no cover - source rendering varies
            errors.append(f"{animations}:{type(error).__name__}:{trim(error, 180)}")
    return None, " | ".join(errors)


def capture_page(
    browser: Browser,
    store: EvidenceStore,
    output_dir: Path,
    viewport_name: str,
    page_name: str,
    source_url: str,
    capture_state: str = "loaded",
    source_html: bytes | None = None,
    source_response: dict[str, Any] | None = None,
    wait_ms: int = 7_000,
    max_response_bytes: int = 0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    viewport = VIEWPORTS[viewport_name]
    context = browser.new_context(
        viewport=viewport["viewport"],
        user_agent=viewport["user_agent"],
        is_mobile=viewport["is_mobile"],
        has_touch=viewport["has_touch"],
        locale="en-US",
        timezone_id="America/New_York",
        color_scheme="light",
        reduced_motion="reduce",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        java_script_enabled=source_html is None,
    )
    blocked: list[dict[str, Any]] = []

    def route_request(route: Any, request: Request) -> None:
        if request.method != "GET":
            blocked.append(
                {
                    "method": request.method,
                    "url": sanitize_url(request.url),
                    "resourceType": request.resource_type,
                }
            )
            route.abort("blockedbyclient")
        else:
            route.continue_()

    context.route("**/*", route_request)
    page = context.new_page()
    capture_open = True
    responses: list[dict[str, Any]] = []
    response_handles: list[tuple[Response, dict[str, Any]]] = []
    rows_by_request: dict[int, dict[str, Any]] = {}

    def response_seen(response: Response) -> None:
        if not capture_open:
            return
        request = response.request
        headers = response.headers
        try:
            reported_size = int(headers.get("content-length", "0") or 0)
        except ValueError:
            reported_size = 0
        row = {
            "url": sanitize_url(response.url, keep_public_query=True),
            "method": request.method,
            "status": response.status,
            "resourceType": request.resource_type,
            "contentType": trim(headers.get("content-type", ""), 180),
            "responseBytes": reported_size,
            "sha256": None,
            "bodyHashed": False,
            "bodyStored": False,
            "bodyHashError": None,
            "cacheControl": trim(headers.get("cache-control", ""), 180),
            "etagPresent": bool(headers.get("etag")),
            "lastModified": trim(headers.get("last-modified", ""), 120),
            "amazonControlledHost": amazon_controlled(response.url),
            "initiator": sanitize_url(request.frame.url) if request.frame else None,
            "_requestFinished": False,
            "_requestFailure": None,
        }
        responses.append(row)
        response_handles.append((response, row))
        rows_by_request[id(request)] = row

    def request_finished(request: Request) -> None:
        row = rows_by_request.get(id(request))
        if row is not None:
            row["_requestFinished"] = True

    def request_failed(request: Request) -> None:
        row = rows_by_request.get(id(request))
        if row is not None:
            row["_requestFailure"] = trim(request.failure, 180)

    page.on("response", response_seen)
    page.on("requestfinished", request_finished)
    page.on("requestfailed", request_failed)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(trim(error, 500)))
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(trim(message.text, 500))
        if message.type == "error"
        else None,
    )
    navigation_error: str | None = None
    if source_html is None:
        try:
            response = page.goto(source_url, wait_until="commit", timeout=30_000)
        except Exception as error:  # pragma: no cover - source availability varies
            response = None
            navigation_error = f"{type(error).__name__}: {trim(error, 500)}"
    else:
        response = None
        try:
            page.set_content(
                inject_base(source_html, source_url),
                wait_until="domcontentloaded",
                timeout=60_000,
            )
        except Exception as error:  # pragma: no cover - source assets may stall
            navigation_error = f"{type(error).__name__}: {trim(error, 500)}"
    page.wait_for_timeout(wait_ms if source_html is None else min(wait_ms, 3_000))
    state_result = apply_capture_state(page, capture_state)
    html = page.content().encode("utf-8")
    html_reference = store.put(html)
    screenshot_path = output_dir / f"source-{page_name}-{viewport_name}.png"
    full_screenshot_path = output_dir / f"source-{page_name}-{viewport_name}-full.png"
    screenshot, screenshot_error = capture_screenshot(
        page, screenshot_path, full_page=False
    )
    full_screenshot, full_screenshot_error = capture_screenshot(
        page, full_screenshot_path, full_page=True
    )
    snapshot = normalize_dom(dom_snapshot(page))
    ax_tree = accessibility_snapshot(context, page)
    ax_reference = store.put_json(ax_tree)
    body_capture = store_response_bodies(
        response_handles,
        store,
        max_response_bytes=max_response_bytes,
    )
    finalize_response_rows(responses)
    capture_open = False
    record = {
        "page": page_name,
        "viewport": viewport_name,
        "captureMode": "live-navigation" if source_html is None else "get-response-render",
        "requestedUrl": source_url,
        "finalUrl": sanitize_url(page.url, keep_public_query=True)
        if source_html is None
        else source_url,
        "navigationStatus": response.status
        if response
        else source_response.get("status") if source_response else None,
        "navigationError": navigation_error,
        "sourceResponse": source_response,
        "interactionState": state_result,
        "html": {
            "bytes": html_reference["bytes"],
            "sha256": html_reference["sha256"],
            "objectPath": html_reference["objectPath"],
            "committed": False,
        },
        "screenshot": {
            "available": screenshot is not None,
            "file": screenshot_path.name if screenshot is not None else None,
            "width": viewport["viewport"]["width"],
            "height": viewport["viewport"]["height"],
            "bytes": len(screenshot) if screenshot is not None else None,
            "sha256": sha256(screenshot) if screenshot is not None else None,
            "error": screenshot_error,
            "committed": False,
        },
        "fullPageScreenshot": {
            "available": full_screenshot is not None,
            "file": full_screenshot_path.name if full_screenshot is not None else None,
            "width": snapshot["dimensions"]["documentWidth"],
            "height": snapshot["dimensions"]["documentHeight"],
            "bytes": len(full_screenshot) if full_screenshot is not None else None,
            "sha256": sha256(full_screenshot) if full_screenshot is not None else None,
            "error": full_screenshot_error,
            "committed": False,
        },
        "accessibilityTree": {
            "nodeCount": len(ax_tree.get("nodes", [])),
            "bytes": ax_reference["bytes"],
            "sha256": ax_reference["sha256"],
            "objectPath": ax_reference["objectPath"],
            "committed": False,
        },
        "dom": snapshot,
        "blockedNonGetRequests": blocked,
        "pageErrors": errors,
        "consoleErrors": console_errors,
        "networkRequestCount": len(responses),
        "responseBodyCapture": body_capture,
    }
    context.close()
    return record, responses


def response_storage_summary(network: list[dict[str, Any]]) -> dict[str, int]:
    in_scope = [
        response
        for response in network
        if response["method"] == "GET"
        and response["resourceType"] in CAPTURED_RESOURCE_TYPES
    ]
    visual = [
        response
        for response in network
        if response["resourceType"] in {"image", "media", "font", "stylesheet"}
    ]
    return {
        "attempted": len(in_scope),
        "stored": sum(bool(response.get("bodyStored")) for response in in_scope),
        "failed": sum(
            response.get("bodyStorage") == "unavailable" for response in in_scope
        ),
        "overExplicitSizeLimit": sum(
            response.get("bodyStorage") == "over-explicit-size-limit"
            for response in in_scope
        ),
        "notFinishedAtCapture": sum(
            response.get("bodyStorage")
            in {"not-finished-at-capture", "request-failed-before-body-complete"}
            for response in in_scope
        ),
        "visualResponses": len(visual),
        "visualStored": sum(bool(response.get("bodyStored")) for response in visual),
    }


def build_media_inventory(
    pages: list[dict[str, Any]], network: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    usages: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        for image in page["dom"]["images"]:
            usages[image["src"]].append(
                {
                    "page": page["page"],
                    "viewport": page["viewport"],
                    "alt": image["alt"],
                    "naturalWidth": image["naturalWidth"],
                    "naturalHeight": image["naturalHeight"],
                    "visible": image["visible"],
                    "inViewport": image["inViewport"],
                }
            )
    inventory: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for response in network:
        if response["resourceType"] not in {"image", "media", "font"}:
            continue
        key = (response["url"], response["resourceType"])
        if key in seen:
            continue
        seen.add(key)
        use = usages.get(response["url"], [])
        inventory.append(
            {
                "publicUrl": response["url"],
                "resourceType": response["resourceType"],
                "contentType": response["contentType"],
                "responseBytes": response["responseBytes"],
                "sha256": response["sha256"],
                "objectPath": response.get("objectPath"),
                "bodyStored": bool(response.get("bodyStored")),
                "amazonControlledHost": response["amazonControlledHost"],
                "pageUsage": use,
                "taskRelated": any(item["visible"] for item in use),
                "localizationDecision": "authored-local-replacement",
                "reason": "Source media is inventoried for visual reference but is not redistributed.",
            }
        )
    return inventory


def assert_redacted(report: dict[str, Any]) -> None:
    encoded = json.dumps(report, ensure_ascii=True)
    forbidden = (
        "ak_bmsc=",
        '"cookie":',
        '"set-cookie":',
        '"authorization":',
    )
    for token in forbidden:
        if token.lower() in encoded.lower():
            raise AssertionError(f"sensitive source token leaked into report: {token}")


def checkpoint_path(store: EvidenceStore, viewport: str, page_name: str) -> Path:
    if not re.fullmatch(r"[a-z0-9-]+", viewport) or not re.fullmatch(
        r"[a-z0-9-]+", page_name
    ):
        raise AssertionError("checkpoint identifiers must be lowercase slugs")
    return store.pages / f"{viewport}--{page_name}.json"


def write_checkpoint(
    store: EvidenceStore,
    page_record: dict[str, Any],
    responses: list[dict[str, Any]],
) -> None:
    payload = {"pageRecord": page_record, "network": responses}
    assert_redacted(payload)
    path = checkpoint_path(store, page_record["viewport"], page_record["page"])
    path.write_text(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")
    os.chmod(path, 0o600)


def load_checkpoint(
    store: EvidenceStore,
    viewport: str,
    page_name: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    path = checkpoint_path(store, viewport, page_name)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert_redacted(payload)
    page_record = payload.get("pageRecord")
    responses = payload.get("network")
    if (
        not isinstance(page_record, dict)
        or page_record.get("page") != page_name
        or page_record.get("viewport") != viewport
        or not isinstance(responses, list)
    ):
        raise AssertionError(f"invalid capture checkpoint: {path.name}")
    return page_record, responses


def assert_evidence_consistent(report: dict[str, Any], root: Path) -> None:
    checked: set[str] = set()

    def check_object(reference: dict[str, Any]) -> None:
        relative = reference.get("objectPath")
        if not isinstance(relative, str) or not relative:
            raise AssertionError("stored evidence reference is missing objectPath")
        candidate = (root / relative).resolve()
        candidate.relative_to(root.resolve())
        if not candidate.is_file():
            raise AssertionError(f"stored evidence object is missing: {relative}")
        if relative in checked:
            return
        body = candidate.read_bytes()
        expected_bytes = reference.get("bytes", reference.get("responseBytes"))
        if len(body) != expected_bytes:
            raise AssertionError(f"stored evidence size mismatch: {relative}")
        if sha256(body) != reference.get("sha256"):
            raise AssertionError(f"stored evidence hash mismatch: {relative}")
        checked.add(relative)

    for page in report["pages"]:
        check_object(page["html"])
        check_object(page["accessibilityTree"])
        for key in ("screenshot", "fullPageScreenshot"):
            screenshot = page[key]
            if not screenshot.get("available", True):
                if not screenshot.get("error"):
                    raise AssertionError("unavailable screenshot is missing an error")
                continue
            path = root / screenshot["file"]
            if not path.is_file():
                raise AssertionError(f"source screenshot is missing: {path.name}")
            body = path.read_bytes()
            if len(body) != screenshot["bytes"] or sha256(body) != screenshot["sha256"]:
                raise AssertionError(f"source screenshot mismatch: {path.name}")
    for response in report["network"]:
        if response.get("bodyStored"):
            check_object(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--viewports",
        default=",".join(VIEWPORTS),
        help="Comma-separated viewport names; defaults to the five-viewport matrix.",
    )
    parser.add_argument(
        "--pages",
        default="all",
        help="Comma-separated PageSpec names or 'all'.",
    )
    parser.add_argument(
        "--wait-ms",
        type=int,
        default=5_000,
        help="Settling time after each live navigation.",
    )
    parser.add_argument(
        "--max-response-bytes",
        type=int,
        default=0,
        help="Explicit per-response storage cap; zero means unlimited.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse structurally valid per-page checkpoints in the output directory.",
    )
    parser.add_argument(
        "--product-html-snapshot",
        type=Path,
        help="Caller-owned raw HTML from an anonymous public GET; never committed.",
    )
    parser.add_argument(
        "--product-mobile-html-snapshot",
        type=Path,
        help="Caller-owned mobile /gp/aw/d public GET HTML; never committed.",
    )
    parser.add_argument("--best-sellers-html-snapshot", type=Path)
    parser.add_argument("--best-sellers-mobile-html-snapshot", type=Path)
    parser.add_argument("--cart-html-snapshot", type=Path)
    parser.add_argument("--cart-mobile-html-snapshot", type=Path)
    args = parser.parse_args()
    if args.wait_ms < 0 or args.max_response_bytes < 0:
        parser.error("--wait-ms and --max-response-bytes cannot be negative")
    selected_viewports = [value.strip() for value in args.viewports.split(",") if value.strip()]
    unknown_viewports = sorted(set(selected_viewports).difference(VIEWPORTS))
    if not selected_viewports or unknown_viewports:
        parser.error(f"unknown or empty viewports: {', '.join(unknown_viewports)}")
    pages_by_name = {page.name: page for page in PAGES}
    if args.pages == "all":
        selected_pages = list(PAGES)
    else:
        page_names = [value.strip() for value in args.pages.split(",") if value.strip()]
        unknown_pages = sorted(set(page_names).difference(pages_by_name))
        if not page_names or unknown_pages:
            parser.error(f"unknown or empty pages: {', '.join(unknown_pages)}")
        selected_pages = [pages_by_name[name] for name in page_names]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(args.output_dir, 0o700)
    store = EvidenceStore(args.output_dir)

    pages: list[dict[str, Any]] = []
    network: list[dict[str, Any]] = []
    os.environ.setdefault("PW_TEST_SCREENSHOT_NO_FONTS_READY", "1")
    if args.product_html_snapshot:
        product_html, product_response = load_caller_snapshot(
            args.product_html_snapshot,
            PRODUCT_SOURCE_URL,
            minimum_bytes=500_000,
            required_markers=("B0874XN4D8", "productTitle"),
        )
    else:
        product_html, product_response = fetch_public_html(PRODUCT_SOURCE_URL)
    mobile_product_html = product_html
    mobile_product_response = product_response
    if args.product_mobile_html_snapshot:
        mobile_product_html, mobile_product_response = load_caller_snapshot(
            args.product_mobile_html_snapshot,
            PRODUCT_MOBILE_SOURCE_URL,
            minimum_bytes=400_000,
            required_markers=("B0874XN4D8", "add-to-cart-button"),
        )
    snapshots: dict[str, list[tuple[str, str, bytes, dict[str, Any]]]] = {
        viewport_name: [] for viewport_name in selected_viewports
    }
    for viewport_name in selected_viewports:
        mobile = viewport_name in MOBILE_VIEWPORTS
        snapshots[viewport_name].append(
            (
                "samsung-t7-product-response-render",
                PRODUCT_MOBILE_SOURCE_URL if mobile else PRODUCT_SOURCE_URL,
                mobile_product_html if mobile else product_html,
                mobile_product_response if mobile else product_response,
            )
        )
    snapshot_specs = (
        (
            "best-sellers-external-ssd-response-render",
            BEST_SELLERS_SOURCE_URL,
            args.best_sellers_html_snapshot,
            args.best_sellers_mobile_html_snapshot,
            200_000,
            ("B0874XN4D8", "Amazon Best Sellers"),
        ),
        (
            "empty-cart-response-render",
            CART_SOURCE_URL,
            args.cart_html_snapshot,
            args.cart_mobile_html_snapshot,
            150_000,
            ("Your Amazon Cart is empty",),
        ),
    )
    for page_name, url, desktop_path, mobile_path, minimum, markers in snapshot_specs:
        desktop_snapshot: tuple[bytes, dict[str, Any]] | None = None
        mobile_snapshot: tuple[bytes, dict[str, Any]] | None = None
        if desktop_path:
            desktop_snapshot = load_caller_snapshot(
                desktop_path, url, minimum_bytes=minimum, required_markers=markers
            )
        if mobile_path:
            mobile_snapshot = load_caller_snapshot(
                mobile_path, url, minimum_bytes=minimum, required_markers=markers
            )
        for viewport_name in selected_viewports:
            selected = (
                mobile_snapshot
                if viewport_name in MOBILE_VIEWPORTS
                else desktop_snapshot
            )
            if selected:
                body, metadata = selected
                snapshots[viewport_name].append((page_name, url, body, metadata))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for viewport_name in selected_viewports:
                for page_spec in selected_pages:
                    checkpoint = (
                        load_checkpoint(store, viewport_name, page_spec.name)
                        if args.resume
                        else None
                    )
                    resumed = checkpoint is not None
                    if checkpoint:
                        page_record, responses = checkpoint
                    else:
                        page_record, responses = capture_page(
                            browser,
                            store,
                            args.output_dir,
                            viewport_name,
                            page_spec.name,
                            page_spec.url,
                            capture_state=page_spec.state,
                            wait_ms=args.wait_ms,
                            max_response_bytes=args.max_response_bytes,
                        )
                        for response in responses:
                            response["page"] = page_spec.name
                            response["viewport"] = viewport_name
                        write_checkpoint(store, page_record, responses)
                    pages.append(page_record)
                    network.extend(responses)
                    print(
                        json.dumps(
                            {
                                "progress": len(pages),
                                "page": page_spec.name,
                                "viewport": viewport_name,
                                "status": page_record["navigationStatus"],
                                "mainHydrated": page_record["dom"]["captureQuality"][
                                    "mainHydrated"
                                ],
                                "responsesStored": page_record["responseBodyCapture"][
                                    "stored"
                                ],
                                "responsesFailed": page_record["responseBodyCapture"][
                                    "failed"
                                ],
                                "responsesIncomplete": page_record[
                                    "responseBodyCapture"
                                ]["incomplete"],
                                "resumed": resumed,
                            },
                            ensure_ascii=True,
                        ),
                        flush=True,
                    )
                for page_name, snapshot_url, snapshot_html, snapshot_response in snapshots[viewport_name]:
                    checkpoint = (
                        load_checkpoint(store, viewport_name, page_name)
                        if args.resume
                        else None
                    )
                    resumed = checkpoint is not None
                    if checkpoint:
                        page_record, responses = checkpoint
                    else:
                        page_record, responses = capture_page(
                            browser,
                            store,
                            args.output_dir,
                            viewport_name,
                            page_name,
                            snapshot_url,
                            source_html=snapshot_html,
                            source_response=snapshot_response,
                            wait_ms=args.wait_ms,
                            max_response_bytes=args.max_response_bytes,
                        )
                        for response in responses:
                            response["page"] = page_name
                            response["viewport"] = viewport_name
                        write_checkpoint(store, page_record, responses)
                    pages.append(page_record)
                    network.extend(responses)
                    print(
                        json.dumps(
                            {
                                "progress": len(pages),
                                "page": page_name,
                                "viewport": viewport_name,
                                "status": page_record["navigationStatus"],
                                "mainHydrated": page_record["dom"]["captureQuality"][
                                    "mainHydrated"
                                ],
                                "responsesStored": page_record["responseBodyCapture"][
                                    "stored"
                                ],
                                "responsesFailed": page_record["responseBodyCapture"][
                                    "failed"
                                ],
                                "responsesIncomplete": page_record[
                                    "responseBodyCapture"
                                ]["incomplete"],
                                "resumed": resumed,
                            },
                            ensure_ascii=True,
                        ),
                        flush=True,
                    )
        finally:
            browser.close()

    # Old resumable checkpoints may contain a response delivered by Playwright's
    # final callback after body collection. Normalize those rows before totals.
    finalize_response_rows(network)
    storage_summary = response_storage_summary(network)
    captured_at = datetime.now(timezone.utc)
    snapshot_id = captured_at.strftime("amazon-en-us-new-york-%Y%m%dT%H%M%SZ")
    captured_at_text = captured_at.isoformat()
    if args.resume and args.report.is_file():
        try:
            previous_report = json.loads(args.report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous_report = {}
        if previous_report.get("format") == "clawbench-pro.public-source-observation.v2":
            snapshot_id = previous_report.get("snapshotId", snapshot_id)
            captured_at_text = previous_report.get("capturedAt", captured_at_text)
    regional_observations = [
        {
            "page": page["page"],
            "viewport": page["viewport"],
            **page["dom"]["regionalSignals"],
        }
        for page in pages
        if page["dom"]["regionalSignals"]["deliveryText"]
        or page["dom"]["regionalSignals"]["currencySamples"]
    ]
    report = {
        "format": "clawbench-pro.public-source-observation.v2",
        "snapshotId": snapshot_id,
        "capturedAt": captured_at_text,
        "accessBoundary": {
            "anonymousPublicGetOnly": True,
            "nonGetRequestsAbortedBeforeTransmission": True,
            "cookiesHeadersAndTokensOmitted": True,
            "rawResponseBodiesStoredPrivately": True,
            "rawResponseBodiesCommitted": False,
            "rawHtmlCommitted": False,
            "rawScreenshotsCommitted": False,
            "evidenceDirectoryMode": "0700",
            "evidenceObjectMode": "0600",
        },
        "source": {
            "platform": "amazon",
            "requestedRegionalBaseline": {
                "locale": "en-US",
                "currency": "USD",
                "deliveryRegion": "New York 10001",
            },
            "observedRegionalSignals": regional_observations,
            "scope": "Amazon public first-party daily retail surfaces with the External SSD task retained as a regression subset",
            "surfaceFamilies": [
                "storefront",
                "global-navigation",
                "search-and-refinements",
                "departments-and-best-sellers",
                "deals",
                "product-detail",
                "cart",
                "account-orders-and-lists-boundaries",
            ],
            "regressionTask": "Amazon External SSD Best Sellers to Samsung T7 quantity-two add-to-cart",
            "targetAsin": "B0874XN4D8",
            "targetRank": 2,
            "targetQuantity": 2,
        },
        "captureMatrix": {
            "viewports": {
                name: VIEWPORTS[name]["viewport"] for name in selected_viewports
            },
            "pages": [
                {"name": page.name, "url": page.url, "state": page.state}
                for page in selected_pages
            ],
            "getResponseRenders": sorted(
                {item[0] for values in snapshots.values() for item in values}
            ),
        },
        "pages": pages,
        "network": network,
        "mediaInventory": build_media_inventory(pages, network),
        "totals": {
            "pageViewportCaptures": len(pages),
            "networkResponses": len(network),
            "mediaResources": sum(
                1
                for response in network
                if response["resourceType"] in {"image", "media", "font"}
            ),
            "responseBodyStorage": storage_summary,
            "evidenceStore": {
                "uniqueObjects": len(store.unique_objects),
                "uniqueBytes": store.unique_bytes,
            },
            "blockedNonGetRequests": sum(
                len(page["blockedNonGetRequests"]) for page in pages
            ),
            "pageErrors": sum(len(page["pageErrors"]) for page in pages),
            "consoleErrors": sum(len(page["consoleErrors"]) for page in pages),
        },
    }
    assert_redacted(report)
    assert_evidence_consistent(report, args.output_dir)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
    os.chmod(args.report, 0o600)
    print(json.dumps(report["totals"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
