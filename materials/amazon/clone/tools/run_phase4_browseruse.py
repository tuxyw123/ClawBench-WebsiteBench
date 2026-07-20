#!/usr/bin/env python3
"""Run the Gate 4 BrowserUse original/clone trajectory comparison.

This runner intentionally does not construct a BrowserUse Agent and never
calls an LLM.  The current Codex session is the controller; BrowserUse 0.12.6
provides the Browser and Tools interaction layer.  Source browsing is guarded
at CDP Fetch.requestPaused so every non-GET request is failed before network
transmission.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from clawbench.amazon_contract import (  # noqa: E402
    amazon_runtime_fingerprint,
    load_amazon_runtime_contract,
)


RUNTIME_MANIFEST = load_amazon_runtime_contract(REPO_ROOT)
CONTRACT_PATH = ROOT / "phase4-browseruse.json"
REPORT_FORMAT = "clawbench.amazon.phase4-browseruse-report.v1"
CONTRACT_FORMAT = "clawbench.amazon.phase4-browseruse.v1"
EXPECTED_BROWSER_USE_VERSION = "0.12.6"
SOURCE_ORIGIN = "https://www.amazon.com"
BEST_ROOT = "/Best-Sellers/zgbs"
BEST_LEAF = "/Best-Sellers-External-Solid-State-Drives/zgbs/pc/3015429011"
PRODUCT = "/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8"
MOBILE_PRODUCT = "/gp/aw/d/B0874XN4D8"
CART = "/gp/cart/view.html"
TARGET_ASIN = "B0874XN4D8"
TERMINAL_PATHS = {
    "/gp/product/handle-buy-box/ref=dp_start-bbf_1_glance",
    "/cart/add-to-cart/ref=mw_dp_buy_crt",
}
SAFE_QUERY_KEYS = {"i", "k", "language", "page", "s"}
SOURCE_ACTIONS = {"navigate", "click", "find_elements", "search_page", "wait"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(stat.S_IRWXU)


def private_file(path: Path) -> None:
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def write_private_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    private_file(path)


def sanitize_url(value: str) -> str:
    """Keep public trajectory parameters and drop identifiers/tokens."""

    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<invalid-url>"
    safe_query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key in SAFE_QUERY_KEYS
    ]
    safe_path = re.sub(
        r"(?<=/)\d{3}-\d{7}-\d{7}(?=/|$)",
        "<redacted-session>",
        parsed.path,
    )
    safe_path = "/".join(
        "<redacted-opaque-path>" if len(segment) > 64 else segment
        for segment in safe_path.split("/")
    )
    return urlunsplit(
        (parsed.scheme, parsed.netloc, safe_path, urlencode(safe_query), "")
    )


def sanitize_result(value: str) -> str:
    """Remove opaque page configuration values from retained action summaries."""

    return re.sub(r"[A-Za-z0-9_./+=-]{40,}", "<redacted-opaque-value>", value)


def no_elements_found(value: str) -> bool:
    normalized = value.strip().casefold()
    return normalized.startswith("no elements found") or normalized.startswith(
        "found 0 element"
    )


def source_url(origin: str, path: str) -> str:
    separator = "&" if "?" in path else "?"
    return urljoin(origin, f"{path}{separator}language=en_US")


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("format") != CONTRACT_FORMAT:
        raise ValueError("unsupported Phase 4 contract format")
    browser_use = contract.get("browserUse", {})
    if browser_use.get("version") != EXPECTED_BROWSER_USE_VERSION:
        raise ValueError("Phase 4 must pin browser-use 0.12.6")
    if browser_use.get("additionalLlmCalls") != 0:
        raise ValueError("Phase 4 cannot make an additional LLM call")
    source_safety = contract.get("sourceSafety", {})
    if source_safety.get("allowedRequestMethods") != ["GET"]:
        raise ValueError("source safety must allow GET only")
    trajectories = contract.get("trajectories", [])
    required = contract.get("gateCriteria", {}).get("requiredTrajectoryCount")
    if len(trajectories) != required or len({item["id"] for item in trajectories}) != required:
        raise ValueError("Phase 4 trajectory matrix is incomplete or duplicated")
    return contract


def node_text(node: Any) -> str:
    for name in (
        "get_all_text_till_next_clickable_element",
        "get_meaningful_text_for_llm",
        "get_all_children_text",
    ):
        method = getattr(node, name, None)
        if callable(method):
            try:
                return str(method())
            except Exception:
                continue
    return ""


def user_agent_for(viewport: dict[str, int]) -> str:
    if viewport["width"] <= 480:
        return (
            "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/138.0.0.0 Mobile Safari/537.36"
        )
    return (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    )


def event_value(event: Any, *names: str) -> Any:
    for name in names:
        if isinstance(event, dict) and name in event:
            return event[name]
        value = getattr(event, name, None)
        if value is not None:
            return value
    return None


class ReadOnlyFetchGuard:
    """Fail non-GET requests before transmission on every attached CDP target."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self._client: Any = None
        self._current_session_id: str | None = None
        self._pending: set[asyncio.Task[Any]] = set()
        self._session_ids: set[str] = set()
        self._active = False
        self._installed = False

    def _track(self, coroutine: Any, name: str) -> None:
        async def wrapped() -> None:
            try:
                await coroutine
            except Exception as error:  # pragma: no cover - depends on CDP disconnects
                if not self._active and "Client is stopping" in str(error):
                    return
                self.errors.append(f"{name}: {type(error).__name__}: {error}")

        task = asyncio.create_task(wrapped(), name=name)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _enable(self, session_id: str) -> None:
        await self._client.send.Fetch.enable(
            params={"patterns": [{"urlPattern": "*", "requestStage": "Request"}]},
            session_id=session_id,
        )

    async def install(self, browser: Any) -> None:
        cdp_session = await browser.get_or_create_cdp_session()
        self._client = cdp_session.cdp_client
        self._current_session_id = cdp_session.session_id
        self._session_ids.add(cdp_session.session_id)
        self._active = True

        def on_request_paused(event: Any, session_id: str | None = None) -> None:
            if not self._active:
                return
            request = event_value(event, "request") or {}
            method = str(event_value(request, "method") or "").upper()
            url = str(event_value(request, "url") or "")
            request_id = event_value(event, "requestId", "request_id")
            record = {
                "method": method,
                "url": sanitize_url(url),
                "decision": "pending",
            }
            self.requests.append(record)

            async def decide() -> None:
                target_session = session_id or self._current_session_id
                try:
                    if method == "GET":
                        await self._client.send.Fetch.continueRequest(
                            params={"requestId": request_id}, session_id=target_session
                        )
                        record["decision"] = "continue"
                    else:
                        await self._client.send.Fetch.failRequest(
                            params={
                                "requestId": request_id,
                                "errorReason": "BlockedByClient",
                            },
                            session_id=target_session,
                        )
                        record["decision"] = "blocked-before-send"
                except RuntimeError as error:
                    if "Invalid InterceptionId" in str(error):
                        # A cross-document navigation can dispose a paused request
                        # before our command arrives. It is canceled with the old
                        # document and cannot have crossed the network boundary.
                        record["decision"] = "canceled-before-decision"
                        return
                    raise

            self._track(decide(), "phase4-source-request-decision")

        def on_attached(event: Any, _session_id: str | None = None) -> None:
            child_session = event_value(event, "sessionId", "session_id")
            target_info = event_value(event, "targetInfo", "target_info") or {}
            target_type = str(event_value(target_info, "type") or "")
            if child_session and target_type in {
                "page",
                "iframe",
                "worker",
                "service_worker",
                "shared_worker",
            }:
                self._session_ids.add(str(child_session))
                self._track(self._enable(str(child_session)), "phase4-source-target-guard")

        self._client.register.Fetch.requestPaused(on_request_paused)
        self._client.register.Target.attachedToTarget(on_attached)
        await self._enable(cdp_session.session_id)
        self._installed = True

    async def close(self) -> None:
        """Disable Fetch before BrowserUse tears down its CDP client."""

        await self.settle()
        for session_id in tuple(self._session_ids):
            try:
                await self._client.send.Fetch.disable(session_id=session_id)
            except Exception:
                # Detached iframe/worker sessions are already unable to transmit.
                pass
        self._active = False
        await self.settle()

    async def settle(self) -> None:
        for _ in range(20):
            if not self._pending:
                break
            await asyncio.sleep(0.05)
        if self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)

    @property
    def blocked_non_get(self) -> int:
        return sum(item["decision"] == "blocked-before-send" for item in self.requests)


class NetworkObserver:
    def __init__(self) -> None:
        self.requests: list[dict[str, str]] = []
        self.duplicate_events_suppressed = 0
        self._seen: set[tuple[str, str, str]] = set()

    def record(self, event: Any, session_id: str | None = None) -> None:
        request = event_value(event, "request") or {}
        method = str(event_value(request, "method") or "").upper()
        url = sanitize_url(str(event_value(request, "url") or ""))
        request_id = str(event_value(event, "requestId", "request_id") or "")
        # BrowserUse subscribes at both the root and target CDP sessions.  The
        # same physical request can therefore arrive twice with different
        # session IDs, while Chromium's request ID remains stable.
        key = (request_id, method, url)
        if key in self._seen:
            self.duplicate_events_suppressed += 1
            return
        self._seen.add(key)
        self.requests.append({"method": method, "url": url})

    async def install(self, browser: Any) -> None:
        cdp_session = await browser.get_or_create_cdp_session()
        client = cdp_session.cdp_client

        def on_request(event: Any, _session_id: str | None = None) -> None:
            self.record(event, _session_id)

        client.register.Network.requestWillBeSent(on_request)
        await client.send.Network.enable(session_id=cdp_session.session_id)


@dataclass
class Trace:
    name: str
    side: str
    browser: Any
    tools: Any
    action_model: Any
    output_dir: Path
    expected_origin: str
    actions: list[dict[str, Any]] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)

    async def current_url(self) -> str:
        return sanitize_url(await self.browser.get_current_page_url())

    def require(self, name: str, passed: bool, detail: str = "") -> None:
        self.assertions.append({"name": name, "passed": bool(passed), "detail": detail})
        if not passed:
            raise AssertionError(f"{self.name}: {name}: {detail}")

    async def act(self, label: str, action: str, params: dict[str, Any]) -> Any:
        if self.side == "source" and action not in SOURCE_ACTIONS:
            raise AssertionError(f"source action {action!r} is forbidden")
        payload = {action: params}
        started = utc_now()
        result = await self.tools.act(
            self.action_model.model_validate(payload), browser_session=self.browser
        )
        error = getattr(result, "error", None)
        extracted = sanitize_result(
            str(getattr(result, "extracted_content", "") or "")[:3000]
        )
        record = {
            "label": label,
            "action": action,
            "params": params,
            "startedAt": started,
            "completedAt": utc_now(),
            "url": await self.current_url(),
            "error": str(error) if error else None,
            "result": extracted,
        }
        self.actions.append(record)
        if error:
            raise AssertionError(f"{self.name}: {label}: {error}")
        return result

    async def navigate(self, label: str, url: str) -> None:
        await self.act(label, "navigate", {"url": url})

    async def find(self, label: str, selector: str, required: bool = False) -> str:
        result = await self.act(label, "find_elements", {"selector": selector})
        text = str(getattr(result, "extracted_content", "") or "")
        if required:
            self.require(label, "Found 0 element" not in text, text[:500])
        return text

    async def search_text(self, label: str, pattern: str) -> str:
        result = await self.act(label, "search_page", {"pattern": pattern})
        return str(getattr(result, "extracted_content", "") or "")

    async def _selector_map(self) -> dict[int, Any]:
        await self.browser.get_browser_state_summary(include_screenshot=False)
        return await self.browser.get_selector_map()

    async def index_for(
        self,
        *,
        tag: str | None = None,
        attributes: dict[str, str] | None = None,
        href_contains: str | None = None,
        text_contains: str | None = None,
    ) -> int | None:
        selector_map = await self._selector_map()
        for index, node in selector_map.items():
            node_tag = str(getattr(node, "tag_name", "")).casefold()
            attrs = dict(getattr(node, "attributes", {}) or {})
            text = node_text(node)
            if tag and node_tag != tag.casefold():
                continue
            if attributes and any(attrs.get(key) != value for key, value in attributes.items()):
                continue
            if href_contains and href_contains not in str(attrs.get("href", "")):
                continue
            if text_contains and text_contains.casefold() not in text.casefold():
                continue
            return int(index)
        return None

    async def click_link(
        self,
        label: str,
        *,
        href_contains: str | None = None,
        text_contains: str | None = None,
        required: bool = True,
    ) -> bool:
        index = await self.index_for(
            tag="a", href_contains=href_contains, text_contains=text_contains
        )
        if index is None:
            if required:
                self.require(label, False, "matching link is absent")
            return False
        await self.act(label, "click", {"index": index})
        return True

    async def click_control(
        self,
        label: str,
        *,
        tag: str,
        attributes: dict[str, str] | None = None,
        text_contains: str | None = None,
        required: bool = True,
    ) -> bool:
        index = await self.index_for(
            tag=tag, attributes=attributes, text_contains=text_contains
        )
        if index is None:
            if required:
                self.require(label, False, "matching control is absent")
            return False
        await self.act(label, "click", {"index": index})
        return True

    async def input_text(
        self, label: str, text: str, *, attributes: dict[str, str]
    ) -> None:
        index = await self.index_for(tag="input", attributes=attributes)
        self.require(label, index is not None, "" if index is not None else "matching input is absent")
        await self.act(label, "input", {"index": index, "text": text})

    async def select_text(
        self, label: str, text: str, *, attributes: dict[str, str]
    ) -> None:
        index = await self.index_for(tag="select", attributes=attributes)
        self.require(label, index is not None, "" if index is not None else "matching select is absent")
        await self.act(label, "select_dropdown", {"index": index, "text": text})

    async def screenshot(self, label: str) -> None:
        path = self.output_dir / f"{self.name}-{label}.png"
        await self.browser.take_screenshot(path=str(path), full_page=False)
        private_file(path)
        self.screenshots.append(path.name)

    def result(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "side": self.side,
            "actions": self.actions,
            "assertions": self.assertions,
            "screenshots": self.screenshots,
            "passed": all(item["passed"] for item in self.assertions),
        }


async def new_browser(
    Browser: Any,
    executable: Path,
    viewport: dict[str, int],
    allowed_domains: list[str],
) -> Any:
    browser = Browser(
        headless=True,
        executable_path=executable,
        viewport=viewport,
        screen=viewport,
        allowed_domains=allowed_domains,
        enable_default_extensions=False,
        user_agent=user_agent_for(viewport),
        headers={"Accept-Language": "en-US,en;q=0.9"},
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-features=ServiceWorker",
            "--disable-sync",
            "--lang=en-US",
            "--no-first-run",
        ],
    )
    await browser.start()
    return browser


async def run_source_desktop(
    Browser: Any, Tools: Any, executable: Path, output_dir: Path, source_origin: str
) -> tuple[dict[str, Any], ReadOnlyFetchGuard]:
    browser = await new_browser(
        Browser,
        executable,
        {"width": 1365, "height": 900},
        ["amazon.com", "*.amazon.com"],
    )
    tools = Tools()
    trace = Trace(
        "source-desktop",
        "source",
        browser,
        tools,
        tools.registry.create_action_model(),
        output_dir,
        source_origin,
    )
    guard = ReadOnlyFetchGuard()
    try:
        await guard.install(browser)
        await trace.navigate("B01 root GET", source_url(source_origin, BEST_ROOT))
        await trace.find("B01 root headings", "h1, h2")
        await trace.screenshot("b01-root")
        followed = await trace.click_link(
            "B01 leaf link click", href_contains="3015429011", required=False
        )
        if not followed:
            await trace.navigate("B01 leaf GET fallback", source_url(source_origin, BEST_LEAF))
        await trace.find("B01 ranked products", "[data-asin], .zg-grid-general-faceout")
        await trace.screenshot("b01-leaf")
        followed = await trace.click_link(
            "B01 target link click", href_contains=TARGET_ASIN, required=False
        )
        if not followed:
            await trace.navigate("B01 product GET fallback", source_url(source_origin, PRODUCT))
        affordances = await trace.find(
            "B01 product affordances",
            "#productTitle, #quantity, #add-to-cart-button, input[name='submit.add-to-cart']",
        )
        if no_elements_found(affordances):
            await trace.navigate(
                "B01 product GET retry after transient click",
                source_url(source_origin, PRODUCT),
            )
            await trace.find(
                "B01 product affordances retry",
                "#productTitle, #quantity, #add-to-cart-button, input[name='submit.add-to-cart']",
            )
        await trace.screenshot("b01-product")

        await trace.navigate(
            "B02 search GET", source_url(source_origin, "/s?k=portable+ssd")
        )
        await trace.find(
            "B02 result and refinements",
            "[data-component-type=s-search-result], #s-refinements, select[name=s]",
        )
        await trace.screenshot("b02-search")

        await trace.navigate("B03 account GET", source_url(source_origin, "/account"))
        await trace.find("B03 anonymous account", "h1, form, a")
        await trace.screenshot("b03-account")

        await trace.navigate("B04 cart GET", source_url(source_origin, CART))
        await trace.find("B04 cart semantics", "h1, #sc-active-cart, #sc-retail-cart-container")
        await trace.screenshot("b04-cart")
        trace.require("source guard installed", guard._installed)
    finally:
        await guard.close()
        await browser.stop()
    return trace.result(), guard


async def run_source_mobile(
    Browser: Any, Tools: Any, executable: Path, output_dir: Path, source_origin: str
) -> tuple[dict[str, Any], ReadOnlyFetchGuard]:
    browser = await new_browser(
        Browser,
        executable,
        {"width": 390, "height": 844},
        ["amazon.com", "*.amazon.com"],
    )
    tools = Tools()
    trace = Trace(
        "source-mobile",
        "source",
        browser,
        tools,
        tools.registry.create_action_model(),
        output_dir,
        source_origin,
    )
    guard = ReadOnlyFetchGuard()
    try:
        await guard.install(browser)
        await trace.navigate("B05 mobile ranking GET", source_url(source_origin, BEST_LEAF))
        await trace.find("B05 mobile ranking semantics", "h1, h2, [data-asin]")
        await trace.screenshot("b05-ranking")
        await trace.navigate(
            "B05 mobile product GET", source_url(source_origin, MOBILE_PRODUCT)
        )
        await trace.find(
            "B05 mobile product affordances",
            "#title, #productTitle, #quantity, #add-to-cart-button",
        )
        await trace.screenshot("b05-product")
        trace.require("source mobile guard installed", guard._installed)
    finally:
        await guard.close()
        await browser.stop()
    return trace.result(), guard


async def run_clone_task(
    Browser: Any, Tools: Any, executable: Path, output_dir: Path, clone_origin: str
) -> tuple[dict[str, Any], NetworkObserver]:
    browser = await new_browser(
        Browser,
        executable,
        {"width": 1365, "height": 900},
        ["127.0.0.1", "localhost"],
    )
    tools = Tools()
    trace = Trace(
        "clone-task",
        "clone",
        browser,
        tools,
        tools.registry.create_action_model(),
        output_dir,
        clone_origin,
    )
    network = NetworkObserver()
    try:
        await network.install(browser)
        await trace.navigate("B01 clone root", urljoin(clone_origin, BEST_ROOT))
        await trace.click_link("B01 click External SSD", href_contains="3015429011")
        trace.require("B01 leaf route", BEST_LEAF in await trace.current_url())
        await trace.screenshot("b01-leaf")
        await trace.click_link("B01 click rank 2", href_contains=TARGET_ASIN)
        trace.require("B01 exact product route", TARGET_ASIN in await trace.current_url())
        await trace.click_control(
            "B01 gallery detail",
            tag="button",
            attributes={"data-gallery-state": "detail"},
        )
        await trace.click_control(
            "B01 capacity 1 TB",
            tag="button",
            attributes={"data-variant": "1 TB"},
        )
        await trace.select_text("B01 quantity 2", "2", attributes={"name": "quantity"})
        await trace.screenshot("b01-ready-to-add")
        await trace.click_control(
            "B01 Add to Cart", tag="button", text_contains="Add to cart"
        )
        await trace.find("B01 populated cart", ".cart-item, .cart-subtotal-line", required=True)
        cart_text = await trace.search_text("B01 exact subtotal", "$439.98")
        trace.require("B01 subtotal $439.98", "$439.98" in cart_text, cart_text[:500])
        quantity_text = await trace.search_text("B01 exact quantity", "Qty: 2")
        trace.require("B01 quantity two", "Qty: 2" in quantity_text, quantity_text[:500])
        trace.require("B01 cart route", CART in await trace.current_url())
        await trace.screenshot("b01-cart")
    finally:
        await browser.stop()
    return trace.result(), network


async def run_clone_search_account(
    Browser: Any, Tools: Any, executable: Path, output_dir: Path, clone_origin: str
) -> tuple[dict[str, Any], NetworkObserver]:
    browser = await new_browser(
        Browser,
        executable,
        {"width": 1365, "height": 900},
        ["127.0.0.1", "localhost"],
    )
    tools = Tools()
    trace = Trace(
        "clone-desktop-secondary",
        "clone",
        browser,
        tools,
        tools.registry.create_action_model(),
        output_dir,
        clone_origin,
    )
    network = NetworkObserver()
    try:
        await network.install(browser)
        await trace.navigate("B02 clone home", clone_origin + "/")
        await trace.input_text(
            "B02 type portable ssd", "portable ssd", attributes={"name": "k"}
        )
        await trace.act("B02 submit search", "send_keys", {"keys": "ENTER"})
        await trace.find("B02 populated results", ".search-result", required=True)
        await trace.click_control(
            "B02 Computers & Accessories filter",
            tag="input",
            attributes={"data-search-filter": "department", "value": "computers"},
        )
        await trace.select_text(
            "B02 sort low to high",
            "Price: Low to High",
            attributes={"data-search-sort": ""},
        )
        await trace.find("B02 refined result", ".search-result", required=True)
        await trace.screenshot("b02-refined-search")

        await trace.navigate("B03 clone account", urljoin(clone_origin, "/account"))
        await trace.find(
            "B03 local account entry", ".commerce-auth-card", required=True
        )
        await trace.find("B03 sign-in affordance", "a[href='/login']", required=True)
        await trace.find(
            "B03 account creation affordance", "a[href='/register']", required=True
        )
        await trace.click_link("B03 Your Orders", href_contains="/account/orders")
        await trace.find("B03 local orders boundary", ".safe-page", required=True)
        trace.require(
            "B03 remains same origin",
            urlsplit(await trace.current_url()).netloc == urlsplit(clone_origin).netloc,
        )
        await trace.screenshot("b03-orders-boundary")
    finally:
        await browser.stop()
    return trace.result(), network


async def run_clone_empty_cart(
    Browser: Any, Tools: Any, executable: Path, output_dir: Path, clone_origin: str
) -> tuple[dict[str, Any], NetworkObserver]:
    browser = await new_browser(
        Browser,
        executable,
        {"width": 1365, "height": 900},
        ["127.0.0.1", "localhost"],
    )
    tools = Tools()
    trace = Trace(
        "clone-empty-cart",
        "clone",
        browser,
        tools,
        tools.registry.create_action_model(),
        output_dir,
        clone_origin,
    )
    network = NetworkObserver()
    try:
        await network.install(browser)
        await trace.navigate("B04 clone cart", urljoin(clone_origin, CART))
        empty = await trace.search_text("B04 empty cart", "Your Amazon Cart is empty")
        trace.require("B04 empty semantics", "Your Amazon Cart is empty" in empty, empty[:500])
        checkout = await trace.find("B04 checkout summary absent", ".cart-summary")
        trace.require(
            "B04 no checkout affordance",
            no_elements_found(checkout),
            checkout[:500],
        )
        await trace.screenshot("b04-empty-cart")
    finally:
        await browser.stop()
    return trace.result(), network


async def run_clone_mobile(
    Browser: Any, Tools: Any, executable: Path, output_dir: Path, clone_origin: str
) -> tuple[dict[str, Any], NetworkObserver]:
    browser = await new_browser(
        Browser,
        executable,
        {"width": 390, "height": 844},
        ["127.0.0.1", "localhost"],
    )
    tools = Tools()
    trace = Trace(
        "clone-mobile",
        "clone",
        browser,
        tools,
        tools.registry.create_action_model(),
        output_dir,
        clone_origin,
    )
    network = NetworkObserver()
    try:
        await network.install(browser)
        await trace.navigate("B05 clone mobile ranking", urljoin(clone_origin, BEST_LEAF))
        await trace.click_link("B05 click rank 2", href_contains=TARGET_ASIN)
        trace.require("B05 mobile product route", TARGET_ASIN in await trace.current_url())
        await trace.find(
            "B05 mobile controls", "select[name=quantity], [data-add-form] button", required=True
        )
        await trace.screenshot("b05-product")
    finally:
        await browser.stop()
    return trace.result(), network


def external_clone_requests(requests: list[dict[str, str]], clone_origin: str) -> list[dict[str, str]]:
    expected = urlsplit(clone_origin).netloc
    return [
        item
        for item in requests
        if urlsplit(item["url"]).scheme in {"http", "https"}
        and urlsplit(item["url"]).netloc != expected
    ]


def clone_terminal_posts(requests: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        item
        for item in requests
        if item["method"] == "POST" and urlsplit(item["url"]).path in TERMINAL_PATHS
    ]


def action_found(trace: dict[str, Any], *labels: str) -> bool:
    for action in trace["actions"]:
        if action["label"] in labels:
            result = action.get("result", "")
            if result.startswith("Found ") and not result.startswith("Found 0 "):
                return True
    return False


def build_comparisons(
    source_desktop: dict[str, Any],
    source_mobile: dict[str, Any],
    clone_task: dict[str, Any],
    clone_secondary: dict[str, Any],
    clone_empty: dict[str, Any],
    clone_mobile: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = [
        (
            "B01",
            action_found(
                source_desktop,
                "B01 product affordances",
                "B01 product affordances retry",
            ),
            clone_task["passed"],
        ),
        (
            "B02",
            action_found(source_desktop, "B02 result and refinements"),
            clone_secondary["passed"],
        ),
        (
            "B03",
            action_found(source_desktop, "B03 anonymous account"),
            clone_secondary["passed"],
        ),
        (
            "B04",
            action_found(source_desktop, "B04 cart semantics"),
            clone_empty["passed"],
        ),
        (
            "B05",
            action_found(source_mobile, "B05 mobile product affordances"),
            clone_mobile["passed"],
        ),
    ]
    return [
        {
            "trajectory": identifier,
            "sourceSemanticAvailable": source_available,
            "clonePassed": clone_passed,
            "status": (
                "passed"
                if source_available and clone_passed
                else "source-unavailable-clone-passed"
                if clone_passed
                else "failed"
            ),
        }
        for identifier, source_available, clone_passed in rows
    ]


def build_review(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Gate 4 BrowserUse Review",
        "",
        f"- BrowserUse: `{report['browserUse']['version']}` (`Browser + Tools`)",
        "- Controller: current Codex session; additional LLM calls: `0`",
        f"- Trajectories: `{summary['trajectoriesPassed']}/{summary['trajectoryCount']}` passed",
        f"- Source-unavailable / clone-passed trajectories: `{summary['sourceUnavailableClonePassed']}`",
        f"- Source requests observed: `{summary['sourceRequests']}`",
        f"- Source non-GET blocked before send: `{summary['sourceNonGetBlocked']}`",
        f"- Source requests canceled with an old document: `{summary['sourceRequestsCanceled']}`",
        "- Source non-GET transmitted: `0`",
        f"- Clone external requests: `{summary['cloneExternalRequests']}`",
        f"- Duplicate clone CDP events suppressed: `{summary['cloneDuplicateNetworkEventsSuppressed']}`",
        f"- Unique clone POSTs: `{summary['clonePostCount']}`",
        f"- Unique clone terminal POSTs: `{summary['cloneTerminalPostCount']}`",
        f"- Clone exact terminal: `{summary['cloneTaskTerminalReached']}`",
        f"- Clone empty-cart checkout affordance: `{summary['cloneEmptyCartCheckoutAffordance']}`",
        f"- Screenshots: `{summary['screenshotCount']}`",
        "",
        "The source trace is anonymous and read-only. A missing live semantic is",
        "recorded as a protection/unavailable observation rather than bypassed. The",
        "clone-only suffix selects quantity 2 and reaches the local cart subtotal",
        "of $439.98. Inspect every PNG in this private directory before approving",
        "Gate 4.",
        "",
        "## Screenshot manifest",
        "",
    ]
    for item in report["screenshots"]:
        lines.append(f"- `{item['file']}` — `{item['sha256']}` ({item['bytes']} bytes)")
    lines.extend(["", "Gate 4 is pending explicit human approval.", ""])
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    contract = load_contract(args.contract)
    version = importlib.metadata.version("browser-use")
    if version != EXPECTED_BROWSER_USE_VERSION:
        raise RuntimeError(
            f"browser-use {EXPECTED_BROWSER_USE_VERSION} required, found {version}"
        )
    if not args.browser_executable.is_file():
        raise FileNotFoundError(args.browser_executable)
    parsed_clone = urlsplit(args.clone_origin)
    if parsed_clone.scheme != "http" or parsed_clone.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("clone origin must be loopback HTTP")
    parsed_source = urlsplit(args.source_origin)
    if parsed_source.scheme != "https" or not parsed_source.hostname or not parsed_source.hostname.endswith("amazon.com"):
        raise ValueError("source origin must be Amazon HTTPS")

    private_directory(args.output)
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
    from browser_use import Browser, Tools

    source_desktop, source_desktop_guard = await run_source_desktop(
        Browser, Tools, args.browser_executable, args.output, args.source_origin
    )
    source_mobile, source_mobile_guard = await run_source_mobile(
        Browser, Tools, args.browser_executable, args.output, args.source_origin
    )
    clone_task, clone_task_network = await run_clone_task(
        Browser, Tools, args.browser_executable, args.output, args.clone_origin
    )
    clone_secondary, clone_secondary_network = await run_clone_search_account(
        Browser, Tools, args.browser_executable, args.output, args.clone_origin
    )
    clone_empty, clone_empty_network = await run_clone_empty_cart(
        Browser, Tools, args.browser_executable, args.output, args.clone_origin
    )
    clone_mobile, clone_mobile_network = await run_clone_mobile(
        Browser, Tools, args.browser_executable, args.output, args.clone_origin
    )

    traces = [
        source_desktop,
        source_mobile,
        clone_task,
        clone_secondary,
        clone_empty,
        clone_mobile,
    ]
    source_requests = source_desktop_guard.requests + source_mobile_guard.requests
    source_guard_errors = source_desktop_guard.errors + source_mobile_guard.errors
    clone_requests = (
        clone_task_network.requests
        + clone_secondary_network.requests
        + clone_empty_network.requests
        + clone_mobile_network.requests
    )
    clone_duplicate_events = sum(
        observer.duplicate_events_suppressed
        for observer in (
            clone_task_network,
            clone_secondary_network,
            clone_empty_network,
            clone_mobile_network,
        )
    )
    external = external_clone_requests(clone_requests, args.clone_origin)
    clone_posts = [item for item in clone_requests if item["method"] == "POST"]
    terminal_posts = clone_terminal_posts(clone_requests)
    comparisons = build_comparisons(
        source_desktop,
        source_mobile,
        clone_task,
        clone_secondary,
        clone_empty,
        clone_mobile,
    )
    if source_guard_errors:
        raise AssertionError(f"source guard errors: {source_guard_errors}")
    if external:
        raise AssertionError(f"clone made external requests: {external[:5]}")
    if len(clone_posts) != 1:
        raise AssertionError(
            f"expected exactly one clone POST, observed {len(clone_posts)}"
        )
    if len(terminal_posts) != 1:
        raise AssertionError(
            f"expected exactly one clone terminal POST, observed {len(terminal_posts)}"
        )
    if not all(item["passed"] for item in traces):
        raise AssertionError("one or more BrowserUse traces failed")
    if any(item["status"] == "failed" for item in comparisons):
        raise AssertionError("one or more source/clone trajectory comparisons failed")

    screenshot_manifest = []
    for path in sorted(args.output.glob("*.png")):
        data = path.read_bytes()
        screenshot_manifest.append(
            {
                "file": path.name,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    report = {
        "format": REPORT_FORMAT,
        "capturedAt": utc_now(),
        "contractSha256": hashlib.sha256(args.contract.read_bytes()).hexdigest(),
        "runtimeStructuralSha256": amazon_runtime_fingerprint(
            REPO_ROOT, RUNTIME_MANIFEST
        ),
        "browserUse": contract["browserUse"],
        "sourceSafety": {
            **contract["sourceSafety"],
            "nonGetTransmitted": 0,
            "mutationsExecuted": 0,
            "guardErrors": source_guard_errors,
        },
        "origins": {
            "source": args.source_origin,
            "clone": args.clone_origin,
        },
        "summary": {
            "trajectoryCount": contract["gateCriteria"]["requiredTrajectoryCount"],
            "trajectoriesPassed": sum(item["status"] == "passed" for item in comparisons),
            "sourceUnavailableClonePassed": sum(
                item["status"] == "source-unavailable-clone-passed"
                for item in comparisons
            ),
            "traceCount": len(traces),
            "actionCount": sum(len(item["actions"]) for item in traces),
            "sourceRequests": len(source_requests),
            "sourceGetContinued": sum(item["decision"] == "continue" for item in source_requests),
            "sourceNonGetBlocked": sum(item["decision"] == "blocked-before-send" for item in source_requests),
            "sourceRequestsCanceled": sum(item["decision"] == "canceled-before-decision" for item in source_requests),
            "sourceNonGetTransmitted": 0,
            "sourceMutationsExecuted": 0,
            "cloneRequests": len(clone_requests),
            "cloneDuplicateNetworkEventsSuppressed": clone_duplicate_events,
            "cloneExternalRequests": len(external),
            "clonePostCount": len(clone_posts),
            "cloneTerminalPostCount": len(terminal_posts),
            "cloneTaskTerminalReached": True,
            "cloneTaskQuantity": 2,
            "cloneTaskSubtotal": "$439.98",
            "cloneEmptyCartCheckoutAffordance": False,
            "screenshotCount": len(screenshot_manifest),
        },
        "traces": traces,
        "comparisons": comparisons,
        "sourceNetwork": source_requests,
        "cloneNetwork": clone_requests,
        "screenshots": screenshot_manifest,
        "gate": {"number": 4, "status": "pending-human-approval"},
    }
    write_private_json(args.output / "report.json", report)
    review_path = args.output / "GATE4_REVIEW.md"
    review_path.write_text(build_review(report), encoding="utf-8")
    private_file(review_path)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clone-origin", required=True)
    parser.add_argument("--source-origin", default=SOURCE_ORIGIN)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-executable", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    report = asyncio.run(run(parse_args()))
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
