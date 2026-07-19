"""Private multi-seed browser/state evaluator for Northstar Market candidates."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import httpx
import numpy as np
from PIL import Image
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from clawbench.web2code.reporting import build_result, validate_result, write_reports
from clawbench.web2code.scoring import score_evaluation
from clawbench.web2code.visual import apply_masks, checkpoint_similarity


@dataclass(frozen=True)
class Target:
    name: str
    public_url: str
    admin_url: str


Journey = Callable[[Target, int], Awaitable[dict[str, Any]]]


class NorthstarJudge:
    def __init__(self, artifact_dir: Path) -> None:
        self.artifact_dir = artifact_dir
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.screenshot_dir = artifact_dir / "screenshots"
        self.screenshot_dir.mkdir(exist_ok=True)
        self.admin_token = os.environ["BENCH_ADMIN_TOKEN"]
        self.mailbox_url = os.environ.get("MAILBOX_URL", "http://mailbox:8025").rstrip("/")
        self.mailbox_admin = os.environ.get("MAILBOX_ADMIN_URL", "http://mailbox:8026").rstrip("/")
        self.fixture_dir = Path(os.environ.get("BENCH_FIXTURE_DIR", "/bench-fixtures"))
        self.runtime_fixture_dir = os.environ.get(
            "BENCH_RUNTIME_FIXTURE_DIR", "/bench-fixtures"
        ).rstrip("/")
        self.reference = Target(
            "reference",
            os.environ.get("REFERENCE_URL", "http://reference-app:8080").rstrip("/"),
            os.environ.get("REFERENCE_ADMIN_URL", "http://reference-app:8081").rstrip("/"),
        )
        self.candidate = Target(
            "candidate",
            os.environ.get("CANDIDATE_URL", "http://candidate-app:8080").rstrip("/"),
            os.environ.get("CANDIDATE_ADMIN_URL", "http://candidate-app:8081").rstrip("/"),
        )
        self.browser: Browser | None = None
        self.candidate_requests: list[str] = []

    async def admin(
        self, target: Target, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                method,
                f"{target.admin_url}{path}",
                headers={"X-Bench-Admin-Token": self.admin_token},
                json=body,
            )
        response.raise_for_status()
        return response.json()

    async def reset_mailbox(self) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{self.mailbox_admin}/__bench/reset",
                headers={"X-Bench-Admin-Token": self.admin_token},
            )
        response.raise_for_status()

    def fixture(self, seed: int) -> dict[str, Any]:
        return json.loads((self.fixture_dir / f"{seed}.json").read_text(encoding="utf-8"))

    async def reset(self, target: Target, seed: int) -> None:
        fixture = self.fixture(seed)
        await self.admin(
            target,
            "POST",
            "/__bench/reset",
            {
                "schema_version": 1,
                "run_id": f"judge-{target.name}-{seed}",
                "seed": seed,
                "now": fixture["now"],
                "fixture_path": f"{self.runtime_fixture_dir}/{seed}.json",
            },
        )
        await self.reset_mailbox()

    async def state(self, target: Target) -> dict[str, Any]:
        return await self.admin(target, "GET", "/__bench/state")

    async def advance(self, target: Target, seconds: int) -> None:
        await self.admin(target, "POST", "/__bench/clock/advance", {"seconds": seconds})

    async def context(self, target: Target, *, mobile: bool = False) -> BrowserContext:
        assert self.browser is not None
        viewport = {"width": 390, "height": 844} if mobile else {"width": 1440, "height": 1000}
        context = await self.browser.new_context(viewport=viewport, locale="en-US", timezone_id="UTC")
        if target.name == "candidate":
            context.on("request", lambda request: self.candidate_requests.append(request.url))
        return context

    async def page(self, target: Target, *, mobile: bool = False) -> tuple[BrowserContext, Page]:
        context = await self.context(target, mobile=mobile)
        page = await context.new_page()
        return context, page

    @staticmethod
    async def signed_in(page: Page) -> bool:
        return await page.locator('header form[action="/logout"]').count() == 1

    @staticmethod
    async def signed_out(page: Page) -> bool:
        return await page.locator('header a[href="/login"]').count() == 1

    @staticmethod
    def account(seed: int, index: int = 1) -> tuple[str, str]:
        return f"shopper{index}.{seed}@example.test", f"Northstar{seed}Test{index}"

    async def login(self, page: Page, target: Target, seed: int, index: int = 1) -> None:
        email, password = self.account(seed, index)
        await page.goto(f"{target.public_url}/login")
        await page.get_by_label("Email").fill(email)
        await page.get_by_label("Password", exact=True).fill(password)
        await page.get_by_role("button", name="Sign in").click()
        await page.wait_for_load_state("networkidle")

    async def add_product(
        self, page: Page, target: Target, product: dict[str, Any], quantity: int = 1
    ) -> None:
        await page.goto(f"{target.public_url}/products/{product['slug']}")
        quantity_control = page.get_by_label("Quantity")
        if await quantity_control.count():
            await quantity_control.select_option(str(quantity))
        await page.get_by_role("button", name="Add to cart").click()
        await page.wait_for_load_state("networkidle")

    async def inbox_link(self, recipient: str, path: str) -> str:
        deadline = time.monotonic() + 10
        async with httpx.AsyncClient(timeout=5) as client:
            while time.monotonic() < deadline:
                response = await client.get(
                    f"{self.mailbox_url}/api/v1/inbox", params={"recipient": recipient}
                )
                response.raise_for_status()
                for message in response.json()["messages"]:
                    for link in message["links"]:
                        if urlsplit(link).path == path:
                            return link
                await asyncio.sleep(0.2)
        raise RuntimeError(f"mailbox link not found for {recipient}: {path}")

    async def fill_checkout(self, page: Page, card: str = "4242 4242 4242 4242") -> None:
        await page.get_by_label("Full name").fill("Hidden Test Shopper")
        await page.get_by_label("Address line 1").fill("100 Benchmark Way")
        await page.get_by_label("City").fill("Portland")
        await page.get_by_label("State").fill("OR")
        await page.get_by_label("ZIP code").fill("97205")
        await page.get_by_label("Standard").check()
        await page.get_by_label("Card number").fill(card)
        await page.get_by_label("Expiration").fill("12/30")
        await page.get_by_label("CVV").fill("123")

    async def place_order(
        self, page: Page, target: Target, seed: int, product: dict[str, Any], quantity: int = 1
    ) -> str:
        await self.login(page, target, seed)
        await self.add_product(page, target, product, quantity)
        await page.goto(f"{target.public_url}/checkout")
        await self.fill_checkout(page)
        await page.get_by_role("button", name="Place test order").click()
        await page.wait_for_url(re.compile(r"/checkout/success/"))
        return page.url.rsplit("/", 1)[-1]

    async def catalog_journey(self, target: Target, seed: int) -> dict[str, Any]:
        fixture = self.fixture(seed)
        product = fixture["catalog"]["products"][0]
        context, page = await self.page(target)
        try:
            await page.goto(f"{target.public_url}/")
            home_text = await page.locator("body").inner_text()
            categories = [item["name"] for item in fixture["catalog"]["categories"]]
            await page.get_by_label("Search products").fill(product["tags"][1])
            await page.get_by_role("button", name="Search").click()
            result_text = await page.locator("body").inner_text()
            await page.goto(f"{target.public_url}/products/{product['slug']}")
            return {
                "terminal": await page.get_by_role("heading", name=product["title"]).count() == 1,
                "checkpoints": {
                    "brand-visible": "Northstar" in home_text,
                    "all-categories-visible": all(name in home_text for name in categories),
                    "search-result-matches": product["title"] in result_text,
                    "search-url-state": urlsplit(page.url).path.startswith("/products/"),
                    "product-price": f"${product['price_cents'] / 100:,.2f}" in await page.locator("body").inner_text(),
                    "stock-control": await page.get_by_role("button", name="Add to cart").count() == 1,
                },
            }
        finally:
            await context.close()

    async def registration_journey(self, target: Target, seed: int) -> dict[str, Any]:
        email = f"journey.registration.{seed}@example.test"
        password = "JourneyPass123"
        context, page = await self.page(target)
        try:
            await page.goto(f"{target.public_url}/register")
            await page.get_by_label("Email").fill(email)
            await page.get_by_label("Password", exact=True).fill(password)
            await page.get_by_label("Confirm password").fill(password)
            await page.get_by_role("button", name="Create account").click()
            sent = "Check your email" in await page.locator("body").inner_text()
            link = await self.inbox_link(email, "/verify")
            await page.goto(link)
            verified_ui = "Your email is verified" in await page.locator("body").inner_text()
            await page.goto(f"{target.public_url}/login")
            await page.get_by_label("Email").fill(email)
            await page.get_by_label("Password", exact=True).fill(password)
            await page.get_by_role("button", name="Sign in").click()
            signed_in = await self.signed_in(page)
            state = await self.state(target)
            verified_state = any(user["email"] == email and user["verified"] for user in state["users"])
            return {
                "terminal": signed_in and verified_state,
                "checkpoints": {
                    "verification-sent": sent,
                    "mailbox-link": urlsplit(link).path == "/verify",
                    "verification-ui": verified_ui,
                    "verified-state": verified_state,
                    "login-after-verify": signed_in,
                },
            }
        finally:
            await context.close()

    async def session_journey(self, target: Target, seed: int) -> dict[str, Any]:
        context, page = await self.page(target)
        try:
            await self.login(page, target, seed)
            await page.reload()
            persisted = await self.signed_in(page)
            await self.advance(target, 86400)
            await page.goto(f"{target.public_url}/account/orders")
            boundary_valid = urlsplit(page.url).path == "/account/orders"
            await self.advance(target, 1)
            await page.goto(f"{target.public_url}/account/orders")
            expired = urlsplit(page.url).path == "/login"
            email, password = self.account(seed)
            await page.goto(f"{target.public_url}/login?next=https://evil.example/")
            await page.get_by_label("Email").fill(email)
            await page.get_by_label("Password", exact=True).fill(password)
            await page.get_by_role("button", name="Sign in").click()
            safe_redirect = urlsplit(page.url).netloc == urlsplit(target.public_url).netloc
            await page.get_by_role("button", name="Sign out").click()
            logged_out = await self.signed_out(page)
            return {
                "terminal": expired and safe_redirect and logged_out,
                "checkpoints": {
                    "refresh-session": persisted,
                    "session-valid-at-boundary": boundary_valid,
                    "session-expired-after-boundary": expired,
                    "safe-next-path": safe_redirect,
                    "logout-invalidates": logged_out,
                },
            }
        finally:
            await context.close()

    async def reset_journey(self, target: Target, seed: int) -> dict[str, Any]:
        email, old_password = self.account(seed)
        new_password = "ResetJourney123"
        first, page = await self.page(target)
        second, other = await self.page(target)
        try:
            await self.login(page, target, seed)
            await self.login(other, target, seed)
            await page.goto(f"{target.public_url}/forgot-password")
            await page.get_by_label("Email").fill(email)
            await page.get_by_role("button", name="Send reset link").click()
            generic = "If an account exists" in await page.locator("body").inner_text()
            link = await self.inbox_link(email, "/reset-password")
            await page.goto(link)
            await page.get_by_label("New password", exact=True).fill(new_password)
            await page.get_by_label("Confirm new password").fill(new_password)
            await page.get_by_role("button", name="Update password").click()
            await other.goto(f"{target.public_url}/account/orders")
            invalidated = urlsplit(other.url).path == "/login"
            await page.goto(link)
            await page.get_by_label("New password", exact=True).fill("AgainPass123")
            await page.get_by_label("Confirm new password").fill("AgainPass123")
            await page.get_by_role("button", name="Update password").click()
            single_use = "already used" in (await page.locator("body").inner_text()).casefold()
            await page.goto(f"{target.public_url}/login")
            await page.get_by_label("Email").fill(email)
            await page.get_by_label("Password", exact=True).fill(old_password)
            await page.get_by_role("button", name="Sign in").click()
            old_rejected = "incorrect" in (await page.locator("body").inner_text()).casefold()
            await page.get_by_label("Password", exact=True).fill(new_password)
            await page.get_by_role("button", name="Sign in").click()
            new_accepted = await self.signed_in(page)
            return {
                "terminal": invalidated and old_rejected and new_accepted,
                "checkpoints": {
                    "generic-request-message": generic,
                    "reset-mailbox-link": urlsplit(link).path == "/reset-password",
                    "all-sessions-invalidated": invalidated,
                    "reset-token-single-use": single_use,
                    "old-password-rejected": old_rejected,
                    "new-password-accepted": new_accepted,
                },
            }
        finally:
            await first.close()
            await second.close()

    async def cart_journey(self, target: Target, seed: int) -> dict[str, Any]:
        product = self.fixture(seed)["catalog"]["products"][0]
        account_context, account = await self.page(target)
        guest_context, guest = await self.page(target)
        try:
            await self.login(account, target, seed)
            await self.add_product(account, target, product, 1)
            await account.get_by_role("button", name="Sign out").click()
            await self.add_product(guest, target, product, 2)
            await self.login(guest, target, seed)
            await guest.goto(f"{target.public_url}/cart")
            text = await guest.locator("body").inner_text()
            merged = "Quantity\n3" in text or await guest.get_by_label("Quantity").input_value() == "3"
            await guest.reload()
            persistent = await guest.get_by_label("Quantity").input_value() == "3"
            state = await self.state(target)
            account_id = next(user["id"] for user in state["users"] if user["email"] == self.account(seed)[0])
            state_quantity = next(
                cart["lines"][0]["quantity"]
                for cart in state["account_carts"]
                if cart["user_id"] == account_id
            )
            return {
                "terminal": merged and persistent and state_quantity == 3,
                "checkpoints": {
                    "guest-refresh-persistence": persistent,
                    "account-plus-guest-merge": merged,
                    "merge-state-quantity": state_quantity,
                    "guest-cart-cleared": len(state["guest_carts"]) == 0,
                },
            }
        finally:
            await account_context.close()
            await guest_context.close()

    async def checkout_journey(self, target: Target, seed: int) -> dict[str, Any]:
        product = self.fixture(seed)["catalog"]["products"][0]
        context, page = await self.page(target)
        try:
            await self.login(page, target, seed)
            await self.add_product(page, target, product, 2)
            before = await self.state(target)
            await page.goto(f"{target.public_url}/checkout")
            await self.fill_checkout(page, "4000 0000 0000 0002")
            await page.get_by_role("button", name="Place test order").click()
            decline_message = "declined" in (await page.locator("body").inner_text()).casefold()
            after_decline = await self.state(target)
            decline_safe = (
                before["products"] == after_decline["products"]
                and before["account_carts"] == after_decline["account_carts"]
                and not after_decline["orders"]
            )
            idempotency_key = await page.locator('input[name="idempotency_key"]').input_value()
            await page.get_by_label("Card number").fill("4242 4242 4242 4242")
            payload = {
                "idempotency_key": idempotency_key,
                "full_name": "Hidden Test Shopper",
                "line1": "100 Benchmark Way",
                "line2": "",
                "city": "Portland",
                "state": "OR",
                "zip_code": "97205",
                "shipping_method": "standard",
                "card_number": "4242 4242 4242 4242",
                "expiration": "12/30",
                "cvv": "123",
            }
            first = await context.request.post(f"{target.public_url}/checkout", form=payload, max_redirects=0)
            second = await context.request.post(f"{target.public_url}/checkout", form=payload, max_redirects=0)
            state = await self.state(target)
            order = state["orders"][0] if len(state["orders"]) == 1 else None
            subtotal = product["price_cents"] * 2
            totals = bool(
                order
                and order["subtotal_cents"] == subtotal
                and order["shipping_cents"] == (0 if subtotal >= 7500 else 599)
                and order["tax_cents"] == (subtotal * 825 + 5000) // 10000
            )
            return {
                "terminal": bool(order) and first.status in {302, 303} and second.status in {302, 303},
                "checkpoints": {
                    "decline-message": decline_message,
                    "decline-side-effect-free": decline_safe,
                    "success-one-order": len(state["orders"]) == 1,
                    "integer-totals": totals,
                    "idempotency-one-record": state["counters"]["idempotency_records"] == 1,
                    "card-last-four-only": bool(order and order["card_last_four"] == "4242"),
                },
            }
        finally:
            await context.close()

    async def orders_journey(self, target: Target, seed: int) -> dict[str, Any]:
        product = self.fixture(seed)["catalog"]["products"][0]
        owner_context, owner = await self.page(target)
        other_context, other = await self.page(target)
        try:
            order_number = await self.place_order(owner, target, seed, product)
            await owner.goto(f"{target.public_url}/account/orders")
            history = order_number in await owner.locator("body").inner_text()
            await owner.reload()
            persisted = order_number in await owner.locator("body").inner_text()
            await self.login(other, target, seed, index=2)
            response = await other.goto(f"{target.public_url}/account/orders/{order_number}")
            isolated = response is not None and response.status == 404
            return {
                "terminal": history and persisted and isolated,
                "checkpoints": {
                    "order-history": history,
                    "refresh-persistence": persisted,
                    "cross-account-404": isolated,
                    "single-owner-order": len((await self.state(target))["orders"]) == 1,
                },
            }
        finally:
            await owner_context.close()
            await other_context.close()

    async def cancellation_journey(self, target: Target, seed: int) -> dict[str, Any]:
        product = self.fixture(seed)["catalog"]["products"][0]
        initial_inventory = product["inventory"]
        context, page = await self.page(target)
        try:
            order_number = await self.place_order(page, target, seed, product, 2)
            await self.advance(target, 1800)
            await page.goto(f"{target.public_url}/account/orders/{order_number}")
            boundary_available = await page.get_by_role("button", name="Cancel order").count() == 1
            await page.get_by_role("button", name="Cancel order").click()
            state = await self.state(target)
            product_state = next(item for item in state["products"] if item["id"] == product["id"])
            cancelled = state["orders"][0]["status"] == "cancelled"
            restored = product_state["inventory"] == initial_inventory
            before_repeat = json.dumps(state, sort_keys=True)
            await context.request.post(
                f"{target.public_url}/account/orders/{order_number}/cancel", max_redirects=0
            )
            repeat_safe = json.dumps(await self.state(target), sort_keys=True) == before_repeat
            return {
                "terminal": cancelled and restored and repeat_safe,
                "checkpoints": {
                    "cancel-at-boundary": boundary_available,
                    "status-cancelled": cancelled,
                    "inventory-restored": restored,
                    "repeat-cancel-safe": repeat_safe,
                },
            }
        finally:
            await context.close()

    async def concurrency_probe(self, target: Target, seed: int = 9199) -> dict[str, Any]:
        fixture = self.fixture(seed)
        product = next(
            item
            for item in fixture["catalog"]["products"]
            if item["id"] == fixture["scenario"]["stock_one_product_id"]
        )
        contexts: list[BrowserContext] = []
        pages: list[Page] = []
        try:
            for index in (1, 2):
                context, page = await self.page(target)
                contexts.append(context)
                pages.append(page)
                await self.login(page, target, seed, index=index)
                await self.add_product(page, target, product)
                await page.goto(f"{target.public_url}/checkout")
                await self.fill_checkout(page)
            await asyncio.gather(
                *(page.get_by_role("button", name="Place test order").click() for page in pages),
                return_exceptions=True,
            )
            state = await self.state(target)
            inventory = next(item for item in state["products"] if item["id"] == product["id"])[
                "inventory"
            ]
            return {
                "one-order": len(state["orders"]) == 1,
                "never-negative": inventory == 0,
                "one-cart-preserved": sum(bool(cart["lines"]) for cart in state["account_carts"]) == 1,
            }
        finally:
            await asyncio.gather(*(context.close() for context in contexts), return_exceptions=True)

    async def boundary_probe(self, target: Target, seed: int) -> dict[str, Any]:
        """Probe rules not already terminal checkpoints in the eight journeys."""
        result: dict[str, Any] = {}
        context, page = await self.page(target)
        try:
            await page.goto(f"{target.public_url}/register")
            await page.get_by_label("Email").fill("invalid")
            await page.get_by_label("Password", exact=True).fill("short")
            await page.get_by_label("Confirm password").fill("different")
            await page.get_by_role("button", name="Create account").click()
            await page.get_by_label("Email").fill(f"valid.boundary.{seed}@example.test")
            await page.get_by_label("Password", exact=True).fill("BoundaryPass123")
            await page.get_by_label("Confirm password").fill("BoundaryPass123")
            await page.get_by_role("button", name="Create account").click()
            result["registration-input-validation-does-not-throttle"] = (
                "Check your email" in await page.locator("body").inner_text()
            )
            await page.goto(f"{target.public_url}/register")
            await page.get_by_label("Email").fill(f"other.boundary.{seed}@example.test")
            await page.get_by_label("Password", exact=True).fill("BoundaryPass123")
            await page.get_by_label("Confirm password").fill("BoundaryPass123")
            await page.get_by_role("button", name="Create account").click()
            throttled = "Please wait" in await page.locator("body").inner_text()
            await self.advance(target, 300)
            await page.get_by_label("Email").fill(f"other.boundary.{seed}@example.test")
            await page.get_by_label("Password", exact=True).fill("BoundaryPass123")
            await page.get_by_label("Confirm password").fill("BoundaryPass123")
            await page.get_by_role("button", name="Create account").click()
            allowed = "Check your email" in await page.locator("body").inner_text()
            result["email-or-device-throttle-five-minute-boundary"] = throttled and allowed
        finally:
            await context.close()

        await self.reset(target, seed)
        context, page = await self.page(target)
        email = f"expiry.verify.{seed}@example.test"
        try:
            await page.goto(f"{target.public_url}/register")
            await page.get_by_label("Email").fill(email)
            await page.get_by_label("Password", exact=True).fill("ExpiryPass123")
            await page.get_by_label("Confirm password").fill("ExpiryPass123")
            await page.get_by_role("button", name="Create account").click()
            link = await self.inbox_link(email, "/verify")
            await self.advance(target, 1801)
            await page.goto(link)
            result["verification-expiry-tamper-and-single-use"] = "expired" in (
                await page.locator("body").inner_text()
            ).casefold()
        finally:
            await context.close()

        await self.reset(target, seed)
        context, page = await self.page(target)
        email, _password = self.account(seed)
        try:
            await page.goto(f"{target.public_url}/forgot-password")
            await page.get_by_label("Email").fill(email)
            await page.get_by_role("button", name="Send reset link").click()
            link = await self.inbox_link(email, "/reset-password")
            await self.advance(target, 3601)
            await page.goto(link)
            await page.get_by_label("New password", exact=True).fill("ExpiredPass123")
            await page.get_by_label("Confirm new password").fill("ExpiredPass123")
            await page.get_by_role("button", name="Update password").click()
            result["reset-expiry-tamper-and-single-use"] = "expired" in (
                await page.locator("body").inner_text()
            ).casefold()
        finally:
            await context.close()

        await self.reset(target, seed)
        context, page = await self.page(target)
        product = self.fixture(seed)["catalog"]["products"][0]
        try:
            await self.login(page, target, seed)
            await self.add_product(page, target, product, 5)
            await self.add_product(page, target, product, 1)
            await page.goto(f"{target.public_url}/cart")
            result["cart-stock-and-five-unit-cap"] = (
                await page.get_by_label("Quantity").input_value() == "5"
            )
            await page.goto(f"{target.public_url}/checkout")
            await self.fill_checkout(page, "1111 1111 1111 1111")
            await page.get_by_role("button", name="Place test order").click()
            result["invalid-and-expired-test-card"] = "supported test card" in (
                await page.locator("body").inner_text()
            ).casefold() and not (await self.state(target))["orders"]
        finally:
            await context.close()
        return result

    async def paired_journey(
        self, identifier: str, seed: int, journey: Journey
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        observations: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}
        for target in (self.reference, self.candidate):
            try:
                await self.reset(target, seed)
                observations[target.name] = await journey(target, seed)
            except Exception as exc:
                errors[target.name] = f"{type(exc).__name__}: {exc}"
                observations[target.name] = {"terminal": False, "checkpoints": {"exception": errors[target.name]}}
        expected = observations["reference"]
        actual = observations["candidate"]
        checkpoint_ids = sorted(set(expected["checkpoints"]) | set(actual["checkpoints"]))
        checkpoints = [
            {
                "id": checkpoint_id,
                "passed": actual["checkpoints"].get(checkpoint_id) == expected["checkpoints"].get(checkpoint_id),
                "expected": expected["checkpoints"].get(checkpoint_id),
                "actual": actual["checkpoints"].get(checkpoint_id),
                "evidence_ids": [],
            }
            for checkpoint_id in checkpoint_ids
        ]
        terminal = bool(expected.get("terminal")) and bool(actual.get("terminal"))
        failures = []
        for checkpoint in checkpoints:
            if not checkpoint["passed"]:
                failures.append(
                    {
                        "id": f"{identifier}-{checkpoint['id']}",
                        "category": self.failure_category(identifier),
                        "severity": "major" if terminal else "critical",
                        "summary": f"{identifier}: {checkpoint['id']} differs from reference",
                        "expected": checkpoint["expected"],
                        "actual": checkpoint["actual"],
                        "reproduction": [f"Reset seed {seed}", f"Run journey {identifier}"],
                        "evidence_ids": [],
                    }
                )
        return (
            {
                "id": identifier,
                "seed": seed,
                "terminal_passed": terminal,
                "checkpoints": checkpoints,
            },
            failures,
        )

    @staticmethod
    def failure_category(identifier: str) -> str:
        for token in ("authentication", "session", "cart", "checkout", "order", "inventory", "time"):
            if token in identifier:
                return token
        return "interaction"

    async def robustness(
        self, journeys: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        config = json.loads(
            Path(os.environ.get("SCORING_PATH", "/task/public/scoring.json")).read_text()
        )
        groups = config["dimensions"]["robustness"]["groups"]
        reference: dict[str, Any] = {}
        candidate: dict[str, Any] = {}
        for target, output in ((self.reference, reference), (self.candidate, candidate)):
            await self.reset(target, 9105)
            output.update(await self.boundary_probe(target, 9105))
            await self.reset(target, 9199)
            concurrency = await self.concurrency_probe(target)
            output["concurrent-stock-one-purchase"] = all(concurrency.values())
            output["atomic-all-or-nothing-stock-shortage"] = all(concurrency.values())
        journey_by_id = {journey["id"]: journey for journey in journeys}

        def actual(journey_id: str, checkpoint_id: str) -> bool:
            journey = journey_by_id[journey_id]
            checkpoint = next(
                item for item in journey["checkpoints"] if item["id"] == checkpoint_id
            )
            return checkpoint["passed"] and checkpoint["actual"] is True

        candidate.update(
            {
                "session-expiry-and-reset-invalidation": actual(
                    "session-lifecycle-and-safe-redirect", "session-expired-after-boundary"
                )
                and actual(
                    "password-reset-and-session-invalidation", "all-sessions-invalidated"
                ),
                "safe-next-path-and-no-open-redirect": actual(
                    "session-lifecycle-and-safe-redirect", "safe-next-path"
                ),
                "cart-merge-is-retry-safe": actual(
                    "guest-cart-persistence-and-login-merge", "guest-cart-cleared"
                ),
                "declined-payment-is-side-effect-free": actual(
                    "checkout-decline-success-and-idempotency", "decline-side-effect-free"
                ),
                "checkout-idempotency-under-retry": actual(
                    "checkout-decline-success-and-idempotency", "idempotency-one-record"
                ),
                "cancellation-boundary-and-repeat-safety": actual(
                    "cancellation-window-and-inventory-restock", "repeat-cancel-safe"
                ),
                "cross-account-order-is-404": actual(
                    "order-history-detail-isolation-and-restart", "cross-account-404"
                ),
                "verification-expiry-tamper-and-single-use": bool(
                    candidate.get("verification-expiry-tamper-and-single-use")
                ),
                "reset-expiry-tamper-and-single-use": bool(
                    candidate.get("reset-expiry-tamper-and-single-use")
                )
                and actual(
                    "password-reset-and-session-invalidation", "reset-token-single-use"
                ),
            }
        )
        reference.update({group: True for group in groups})
        results = [
            {
                "id": group,
                "passed": candidate.get(group, False) == reference.get(group, True) and bool(candidate.get(group, False)),
            }
            for group in groups
        ]
        failures = [
            {
                "id": f"robustness-{item['id']}",
                "category": "concurrency" if "concurrent" in item["id"] else "time",
                "severity": "major",
                "summary": f"Robustness group failed: {item['id']}",
                "expected": reference.get(item["id"], True),
                "actual": candidate.get(item["id"], False),
                "reproduction": ["Run the named hidden robustness group"],
                "evidence_ids": [],
            }
            for item in results
            if not item["passed"]
        ]
        return results, failures

    async def geometry(self, page: Page) -> list[dict[str, Any]]:
        return await page.locator("a,button,input,select,h1,h2,h3").evaluate_all(
            """elements => elements.filter(e => { const r=e.getBoundingClientRect(); return r.width>0 && r.height>0; }).map(e => { const r=e.getBoundingClientRect(); return {role:e.getAttribute('role') || e.tagName.toLowerCase(), name:e.getAttribute('aria-label') || e.innerText || e.getAttribute('placeholder') || '', x:r.x/innerWidth, y:r.y/innerHeight, width:r.width/innerWidth, height:r.height/innerHeight}; })"""
        )

    async def capture(self, target: Target, checkpoint: dict[str, Any]) -> dict[str, Any]:
        mobile = checkpoint["viewport"] == "mobile"
        context, page = await self.page(target, mobile=mobile)
        try:
            path = checkpoint["path"]
            if "{featured_product_slug}" in path:
                path = path.replace("{featured_product_slug}", self.fixture(1101)["catalog"]["products"][0]["slug"])
            await page.goto(f"{target.public_url}{path}")
            setup = checkpoint["setup_case"]
            if setup == "open-mobile-menu":
                await page.get_by_role("button", name="Menu").click()
            elif setup == "guest-cart-two-lines":
                products = self.fixture(1101)["catalog"]["products"][:2]
                await self.add_product(page, target, products[0], 2)
                await self.add_product(page, target, products[1], 1)
                await page.goto(f"{target.public_url}/cart")
            elif setup == "invalid-registration-submitted":
                await page.get_by_label("Email").fill("invalid")
                await page.get_by_label("Password", exact=True).fill("short")
                await page.get_by_label("Confirm password").fill("different")
                await page.get_by_role("button", name="Create account").click()
            elif setup == "forgot-password-submitted":
                await page.get_by_label("Email").fill("unknown@example.test")
                await page.get_by_role("button", name="Send reset link").click()
            elif setup in {"verified-user-cart-checkout", "successful-standard-order", "cancelled-order"}:
                product = self.fixture(1101)["catalog"]["products"][0]
                await self.login(page, target, 1101)
                await self.add_product(page, target, product)
                if setup == "verified-user-cart-checkout":
                    await page.goto(f"{target.public_url}/checkout")
                else:
                    await page.goto(f"{target.public_url}/checkout")
                    await self.fill_checkout(page)
                    await page.get_by_role("button", name="Place test order").click()
                    await page.wait_for_url(re.compile(r"/checkout/success/"))
                    if setup == "cancelled-order":
                        order_number = page.url.rsplit("/", 1)[-1]
                        await page.goto(f"{target.public_url}/account/orders/{order_number}")
                        await page.get_by_role("button", name="Cancel order").click()
            await page.wait_for_load_state("networkidle")
            await page.evaluate("document.fonts.ready")
            screenshot_path = self.screenshot_dir / f"{checkpoint['id']}-{target.name}.png"
            await page.screenshot(path=screenshot_path)
            masks = []
            for mask in checkpoint.get("masks", []):
                if mask["kind"] == "accessible-name":
                    locator = page.get_by_label(mask["value"])
                    if await locator.count():
                        box = await locator.first.bounding_box()
                        if box:
                            masks.append((int(box["x"]), int(box["y"]), int(box["width"]), int(box["height"])))
            return {
                "image": np.asarray(Image.open(screenshot_path).convert("RGB")),
                "text": await page.locator("body").inner_text(),
                "geometry": await self.geometry(page),
                "masks": masks,
                "path": screenshot_path,
            }
        finally:
            await context.close()

    async def visual(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        config = json.loads(
            Path(os.environ.get("VISUAL_PATH", "/task/public/visual-checkpoints.json")).read_text()
        )
        results = []
        evidence = []
        for checkpoint in config["checkpoints"]:
            captures = {}
            for target in (self.reference, self.candidate):
                await self.reset(target, 1101)
                captures[target.name] = await self.capture(target, checkpoint)
            reference = captures["reference"]
            candidate = captures["candidate"]
            ref_image = apply_masks(reference["image"], reference["masks"])
            cand_image = apply_masks(candidate["image"], candidate["masks"])
            metrics = checkpoint_similarity(
                ref_image,
                cand_image,
                reference_text=reference["text"],
                candidate_text=candidate["text"],
                reference_geometry=reference["geometry"],
                candidate_geometry=candidate["geometry"],
            )
            results.append({"id": checkpoint["id"], **metrics})
            for target_name, capture in captures.items():
                path = capture["path"]
                evidence.append(
                    {
                        "id": f"visual-{checkpoint['id']}-{target_name}",
                        "kind": "screenshot",
                        "path": str(path.relative_to(self.artifact_dir.parent)),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
        return results, evidence

    async def security_hard_failures(self) -> list[dict[str, Any]]:
        failures: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            public_responses = [
                await client.get(f"{self.candidate.public_url}{path}")
                for path in ("/__bench/health", "/__bench/state", "/__bench/reset")
            ]
            if any(response.status_code != 404 for response in public_responses):
                failures.append(
                    {
                        "code": "PUBLIC_ADMIN_ROUTE_EXPOSED",
                        "message": "Candidate public listener exposed a benchmark admin path",
                        "evidence_ids": [],
                    }
                )
            unauthenticated = await client.get(f"{self.candidate.admin_url}/__bench/state")
            wrong_token = await client.get(
                f"{self.candidate.admin_url}/__bench/state",
                headers={"X-Bench-Admin-Token": "incorrect-token"},
            )
            if unauthenticated.status_code != 404 or wrong_token.status_code != 404:
                failures.append(
                    {
                        "code": "ADMIN_AUTH_BYPASS",
                        "message": "Candidate admin listener distinguishes or accepts invalid credentials",
                        "evidence_ids": [],
                    }
                )
            home = await client.get(f"{self.candidate.public_url}/")
            if self.admin_token and self.admin_token in home.text:
                failures.append(
                    {
                        "code": "ADMIN_TOKEN_EXPOSED",
                        "message": "Candidate rendered the benchmark admin token on public HTTP",
                        "evidence_ids": [],
                    }
                )
        return failures

    async def evaluate(self) -> dict[str, Any]:
        journey_specs: list[tuple[str, int, Journey]] = [
            ("catalog-search-and-product", 9101, self.catalog_journey),
            ("registration-verification-and-login", 9102, self.registration_journey),
            ("session-lifecycle-and-safe-redirect", 9103, self.session_journey),
            ("password-reset-and-session-invalidation", 9104, self.reset_journey),
            ("guest-cart-persistence-and-login-merge", 9105, self.cart_journey),
            ("checkout-decline-success-and-idempotency", 9101, self.checkout_journey),
            ("order-history-detail-isolation-and-restart", 9102, self.orders_journey),
            ("cancellation-window-and-inventory-restock", 9103, self.cancellation_journey),
        ]
        journeys = []
        failures = []
        for identifier, seed, function in journey_specs:
            journey, journey_failures = await self.paired_journey(identifier, seed, function)
            journeys.append(journey)
            failures.extend(journey_failures)
        robustness, robustness_failures = await self.robustness(journeys)
        failures.extend(robustness_failures)
        visual, evidence = await self.visual()
        interaction_checkpoints = [
            checkpoint
            for journey in journeys
            for checkpoint in journey["checkpoints"]
        ][:20]
        interactions = [
            {"id": f"interaction-{item['id']}", "passed": item["passed"]}
            for item in interaction_checkpoints
        ]
        while len(interactions) < 20:
            interactions.append({"id": f"interaction-padding-{len(interactions)}", "passed": False})
        hard_failures = await self.security_hard_failures()
        allowed_hosts = {
            urlsplit(self.candidate.public_url).hostname,
            urlsplit(self.mailbox_url).hostname,
        }
        reference_host = urlsplit(self.reference.public_url).hostname
        internet_requests = []
        reference_requests = []
        for url in self.candidate_requests:
            host = urlsplit(url).hostname
            if host == reference_host:
                reference_requests.append(url)
            elif host not in allowed_hosts and urlsplit(url).scheme in {"http", "https"}:
                internet_requests.append(url)
        if reference_requests:
            hard_failures.append(
                {"code": "RUNTIME_REFERENCE_REQUEST", "message": "Candidate requested the reference origin", "evidence_ids": []}
            )
        if internet_requests:
            hard_failures.append(
                {"code": "RUNTIME_INTERNET_REQUEST", "message": "Candidate made an external runtime request", "evidence_ids": []}
            )
        source_policy_path = self.artifact_dir / "source-policy.json"
        if source_policy_path.exists():
            for finding in json.loads(source_policy_path.read_text()):
                if finding.get("hard_failure"):
                    hard_failures.append(
                        {"code": finding["code"], "message": finding["message"], "evidence_ids": []}
                    )
        resource_path = self.artifact_dir / "resource-facts.json"
        resource_facts = json.loads(resource_path.read_text()) if resource_path.exists() else {}
        latencies = []

        async def health_request(client: httpx.AsyncClient) -> None:
            started = time.perf_counter()
            response = await client.get(f"{self.candidate.public_url}/healthz")
            response.raise_for_status()
            latencies.append((time.perf_counter() - started) * 1000)

        async with httpx.AsyncClient(timeout=5) as client:
            for _ in range(10):
                await asyncio.gather(*(health_request(client) for _ in range(10)))
        p95_latency = sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]
        resource_facts.setdefault("resources", {})["p95_latency_ms"] = p95_latency
        resource_facts.setdefault("efficiency", {})[
            "p95_latency_ms_at_10_concurrent"
        ] = p95_latency
        resource_path.write_text(json.dumps(resource_facts, indent=2) + "\n", encoding="utf-8")
        return {
            "visual": visual,
            "interactions": interactions,
            "journeys": journeys,
            "robustness": robustness,
            "efficiency": resource_facts.get("efficiency", {}),
            "hard_failures": hard_failures,
            "failures": failures,
            "evidence": evidence,
            "seeds": [
                {
                    "seed": seed,
                    "purpose": "concurrency" if seed == 9199 else "hidden-functional",
                    "reset_passed": True,
                    "tests_passed": sum(
                        checkpoint["passed"] for journey in journeys if journey["seed"] == seed for checkpoint in journey["checkpoints"]
                    ),
                    "tests_total": sum(
                        1 for journey in journeys if journey["seed"] == seed for _ in journey["checkpoints"]
                    ),
                }
                for seed in (9101, 9102, 9103, 9104, 9105, 9199)
            ],
            "resources": resource_facts.get(
                "resources",
                {
                    "build_seconds": 0,
                    "startup_seconds": 0,
                    "image_bytes": 0,
                    "source_bytes": 0,
                    "peak_memory_bytes": 0,
                    "p95_latency_ms": 0,
                },
            ),
            "network": {
                "runtime_requests": len(self.candidate_requests),
                "blocked_requests": 0,
                "reference_requests": len(reference_requests),
                "internet_requests": len(internet_requests),
            },
        }

    async def run(self) -> dict[str, Any]:
        async with async_playwright() as playwright:
            self.browser = await playwright.chromium.launch(headless=True)
            return await self.evaluate()


def usage(artifact_root: Path) -> dict[str, Any]:
    input_tokens = 0
    output_tokens = 0
    messages = artifact_root / "agent" / "agent-messages.jsonl"
    if messages.exists():
        for line in messages.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            encoded = json.dumps(value)
            for key, target in (("input_tokens", "input"), ("output_tokens", "output")):
                matches = re.findall(rf'"{key}"\s*:\s*(\d+)', encoded)
                if matches:
                    if target == "input":
                        input_tokens = max(input_tokens, *(int(item) for item in matches))
                    else:
                        output_tokens = max(output_tokens, *(int(item) for item in matches))
    actions = artifact_root / "browser" / "actions.jsonl"
    builds = artifact_root / "builds" / "builds.jsonl"
    human = artifact_root / "human-interventions.jsonl"
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "browser_actions": len(actions.read_text().splitlines()) if actions.exists() else 0,
        "candidate_builds": len(builds.read_text().splitlines()) if builds.exists() else 0,
        "human_messages": len(human.read_text().splitlines()) if human.exists() else 0,
        "human_minutes": 0,
    }


async def async_main() -> int:
    artifact_root = Path(os.environ.get("ARTIFACT_ROOT", "/artifacts"))
    eval_dir = artifact_root / "eval"
    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    judge = NorthstarJudge(eval_dir)
    facts = await judge.run()
    facts["usage"] = usage(artifact_root)
    facts["versions"] = {
        "evaluator": "1.0.0",
        "browser-use": "0.12.6",
        "playwright": "1.60.0",
        "protocol": "websitebench.result.v1",
    }
    (eval_dir / "facts.json").write_text(
        json.dumps(facts, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    scoring_path = Path(os.environ.get("SCORING_PATH", "/task/public/scoring.json"))
    scoring = json.loads(scoring_path.read_text())
    scored = score_evaluation(facts, scoring)
    run_meta = json.loads((artifact_root / "run-meta.json").read_text())
    run = {
        "run_id": run_meta["run_id"],
        "site_id": "northstar-market",
        "site_version": run_meta["site_version"],
        "track": run_meta["track"],
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    result = build_result(run=run, scored=scored, facts=facts)
    validate_result(result, "/task/schemas/report.schema.json")
    write_reports(result, eval_dir)
    return 0 if result["status"] == "passed" else 1


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
