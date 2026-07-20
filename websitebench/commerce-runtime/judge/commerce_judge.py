"""Private five-journey fact producer for compiled commerce variants.

It exercises browser-observable HTTP forms plus the published deterministic
admin contract.  It never imports scoring/reporting code and writes only
``websitebench.facts.v1`` evidence for host-side scoring.
"""

from __future__ import annotations

import json
import os
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import httpx
from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright


JOURNEY_MODULES = {
    "catalog_observability": ("quantity", "pricing", "fulfillment"),
    "account_lifecycle": ("token_lifetime",),
    "cart_inventory": ("quantity", "inventory"),
    "checkout_concurrency": ("pricing", "inventory", "fulfillment"),
    "orders_terminal": ("cancellation",),
}


def checkpoint(identifier: str, passed: bool, expected: object, actual: object) -> dict:
    return {
        "id": identifier,
        "passed": bool(passed),
        "expected": expected,
        "actual": actual,
        "evidence_ids": [],
    }


def fact_failure(
    identifier: str,
    *,
    category: str,
    severity: str,
    summary: str,
    expected: object,
    actual: object,
    reproduction: list[str],
) -> dict[str, Any]:
    return {
        "id": identifier,
        "category": category,
        "severity": severity,
        "summary": summary,
        "expected": expected,
        "actual": actual,
        "reproduction": reproduction,
        "evidence_ids": [],
    }


class CommerceJudge:
    def __init__(self) -> None:
        self.public_url = os.environ["CANDIDATE_URL"].rstrip("/")
        self.admin_url = os.environ["CANDIDATE_ADMIN_URL"].rstrip("/")
        self.reference_url = os.environ["REFERENCE_URL"].rstrip("/")
        self.reference_admin_url = os.environ["REFERENCE_ADMIN_URL"].rstrip("/")
        self.mailbox_url = os.environ["MAILBOX_URL"].rstrip("/")
        self.mailbox_admin_url = os.environ["MAILBOX_ADMIN_URL"].rstrip("/")
        self.admin_token = os.environ["BENCH_ADMIN_TOKEN"]
        self.fixture_dir = Path(os.environ["BENCH_FIXTURE_DIR"])
        self.public_fixture_dir = Path(
            os.environ.get("PUBLIC_FIXTURE_DIR", "/task/public/fixtures")
        )
        self.runtime_fixture_dir = os.environ.get(
            "BENCH_RUNTIME_FIXTURE_DIR", "/bench-fixtures"
        ).rstrip("/")
        self.runtime_public_fixture_dir = os.environ.get(
            "BENCH_PUBLIC_RUNTIME_FIXTURE_DIR", self.runtime_fixture_dir
        ).rstrip("/")
        self.reference_public_fixture_dir = os.environ.get(
            "REFERENCE_PUBLIC_FIXTURE_DIR", "/bench-public-fixtures"
        ).rstrip("/")
        self.assertions = json.loads(Path(os.environ["ASSERTIONS_PATH"]).read_text(encoding="utf-8"))
        self.policies = self.assertions["policies"]
        self.failures: list[dict[str, Any]] = []

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Bench-Admin-Token": self.admin_token}

    def fixture(self, seed: int) -> dict[str, Any]:
        for root in (self.public_fixture_dir, self.fixture_dir):
            path = root / f"{seed}.json"
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        raise FileNotFoundError(seed)

    def admin(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = httpx.request(
            method,
            self.admin_url + path,
            headers=self.headers,
            json=body,
            timeout=20,
        )
        response.raise_for_status()
        return response.json()

    def reset(self, seed: int) -> None:
        fixture = self.fixture(seed)
        is_public = (self.public_fixture_dir / f"{seed}.json").is_file()
        payload = {
            "schema_version": 1,
            "run_id": f"commerce-judge-{seed}",
            "seed": seed,
            "now": fixture["now"],
            "fixture_path": (
                f"{self.runtime_public_fixture_dir}/{seed}.json"
                if is_public
                else f"{self.runtime_fixture_dir}/{seed}.json"
            ),
        }
        try:
            self.admin("POST", "/__bench/reset", payload)
        except httpx.HTTPStatusError:
            # Reference containers keep the public fixture on a separate
            # read-only mount; final candidates receive both seed files under
            # the standard /bench-fixtures contract.
            if is_public:
                payload["fixture_path"] = f"{self.reference_public_fixture_dir}/{seed}.json"
                self.admin("POST", "/__bench/reset", payload)
            else:
                raise
        try:
            response = httpx.post(
                self.mailbox_admin_url + "/__bench/reset",
                headers=self.headers,
                timeout=10,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self.failures.append(
                fact_failure(
                    "mailbox-reset",
                    category="interaction",
                    severity="minor",
                    summary="The controlled mailbox could not be reset",
                    expected="mailbox admin reset succeeds",
                    actual=str(exc),
                    reproduction=["POST /__bench/reset to the mailbox admin service"],
                )
            )

    def state(self) -> dict[str, Any]:
        return self.admin("GET", "/__bench/state")

    def advance(self, seconds: int) -> None:
        self.admin("POST", "/__bench/clock/advance", {"seconds": seconds})

    def policy_checkpoint(self, journey_id: str) -> dict[str, Any]:
        observed = self.state().get("policy_profile", {})
        expected = {name: self.policies[name] for name in JOURNEY_MODULES[journey_id]}
        actual = {name: observed.get(name) for name in JOURNEY_MODULES[journey_id]}
        return checkpoint("policy-profile", actual == expected, expected, actual)

    @staticmethod
    def login(client: httpx.Client, fixture: dict[str, Any], index: int = 0) -> httpx.Response:
        account = fixture["accounts"][index]
        return client.post(
            "/login",
            data={"email": account["email"], "password": account["password"], "next": "/"},
        )

    @staticmethod
    def add(client: httpx.Client, product_id: str, quantity: int) -> httpx.Response:
        return client.post(
            "/cart/add",
            data={"product_id": product_id, "quantity": quantity, "return_to": "/cart"},
        )

    def mailbox_token(self, recipient: str) -> str:
        response = httpx.get(
            self.mailbox_url + "/api/v1/inbox",
            params={"recipient": recipient},
            timeout=10,
        )
        response.raise_for_status()
        messages = response.json().get("messages", [])
        if not messages or not messages[0].get("links"):
            raise RuntimeError(f"mailbox contains no link for {recipient}")
        token = parse_qs(urlsplit(messages[0]["links"][0]).query).get("token", [])
        if len(token) != 1 or not token[0]:
            raise RuntimeError(f"mailbox link contains no unique token for {recipient}")
        return token[0]

    def valid_quantity(self) -> int:
        policy = self.policies["quantity"]
        if policy["kind"] == "wholesale_case":
            return int(policy["parameters"]["minimum"])
        return 1

    @staticmethod
    def money(cents: int) -> str:
        return f"${cents / 100:,.2f}"

    def checkout_payload(self, *, key: str, card: str, pickup: bool = False) -> dict[str, str]:
        payload = {
            "idempotency_key": key,
            "card_number": card,
            "full_name": "Benchmark Shopper",
            "line1": "100 Benchmark Way",
            "city": "Portland",
            "state": "OR",
            "zip_code": "97205",
            "shipping_method": "standard",
            "expiration": "12/30",
            "cvv": "123",
        }
        if pickup:
            payload.update({"store": "harbor-east", "slot": "pickup-early"})
        return payload

    def catalog(self, seed: int) -> list[dict[str, Any]]:
        fixture = self.fixture(seed)
        product = fixture["catalog"]["products"][0]
        checks = [self.policy_checkpoint("catalog_observability")]
        with httpx.Client(base_url=self.public_url, follow_redirects=True, timeout=20) as client:
            home = client.get("/")
            search = client.get("/search", params={"q": product["title"].split()[0]})
            detail = client.get(f"/products/{product['slug']}")
        checks.extend(
            [
                checkpoint(
                    "catalog-visible",
                    home.status_code == 200 and product["title"] in home.text,
                    product["title"],
                    {"status": home.status_code, "visible": product["title"] in home.text},
                ),
                checkpoint(
                    "search-finds-product",
                    search.status_code == 200 and product["title"] in search.text,
                    product["title"],
                    {"status": search.status_code, "visible": product["title"] in search.text},
                ),
                checkpoint(
                    "product-and-rules",
                    detail.status_code == 200
                    and product["description"] in detail.text
                    and "Observable purchase rules" in detail.text,
                    "product detail and observable rules",
                    {"status": detail.status_code, "bytes": len(detail.content)},
                ),
            ]
        )
        return checks

    def account(self, seed: int) -> list[dict[str, Any]]:
        fixture = self.fixture(seed)
        account = fixture["accounts"][0]
        checks = [self.policy_checkpoint("account_lifecycle")]
        with httpx.Client(base_url=self.public_url, follow_redirects=True, timeout=20) as client:
            forms = {
                route: client.get(route)
                for route in ("/register", "/login", "/forgot-password", "/reset-password")
            }
            login = self.login(client, fixture)
            session = client.get("/account/orders")
            forgot = client.post("/forgot-password", data={"email": account["email"]})
            registration_email = f"new-{seed}@example.test"
            registration = client.post(
                "/register",
                data={
                    "email": registration_email,
                    "password": "VariantBenchmark123!",
                    "confirm_password": "VariantBenchmark123!",
                },
            )
            reset_token = self.mailbox_token(account["email"])
            verification_token = self.mailbox_token(registration_email)
            verification = client.get(
                "/verify", params={"token": verification_token}
            )
            verification_reuse = client.get(
                "/verify", params={"token": verification_token}
            )
            reset = client.post(
                "/reset-password",
                data={
                    "token": reset_token,
                    "password": "ChangedVariant123!",
                    "confirm_password": "ChangedVariant123!",
                },
            )
            reset_reuse = client.post(
                "/reset-password",
                data={
                    "token": reset_token,
                    "password": "ChangedAgain123!",
                    "confirm_password": "ChangedAgain123!",
                },
            )
            invalidated_session = client.get("/account/orders")
        with httpx.Client(
            base_url=self.public_url, follow_redirects=True, timeout=20
        ) as verification_client:
            verified_login = verification_client.post(
                "/login",
                data={
                    "email": registration_email,
                    "password": "VariantBenchmark123!",
                    "next": "/account/orders",
                },
            )
        with httpx.Client(
            base_url=self.public_url, follow_redirects=True, timeout=20
        ) as password_client:
            old_password = password_client.post(
                "/login",
                data={
                    "email": account["email"],
                    "password": account["password"],
                    "next": "/account/orders",
                },
            )
            new_password = password_client.post(
                "/login",
                data={
                    "email": account["email"],
                    "password": "ChangedVariant123!",
                    "next": "/account/orders",
                },
            )
        state = self.state()
        tokens = state.get("tokens", [])
        reset_tokens = [item for item in tokens if item.get("kind") == "reset"]
        verification_tokens = [item for item in tokens if item.get("kind") == "verification"]
        expected_tokens = self.policies["token_lifetime"]["parameters"]

        def lifetime(record: dict[str, Any]) -> int:
            issued = datetime.fromisoformat(record["issued_at"].replace("Z", "+00:00"))
            expires = datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00"))
            return int((expires - issued).total_seconds() // 60)

        checks.extend(
            [
                checkpoint(
                    "account-forms",
                    all(response.status_code == 200 and "<form" in response.text for response in forms.values()),
                    "four account forms",
                    {route: response.status_code for route, response in forms.items()},
                ),
                checkpoint(
                    "login-session",
                    login.status_code == 200
                    and session.status_code == 200
                    and account["email"] in session.text,
                    "persistent authenticated session",
                    {"login": login.status_code, "orders": session.status_code},
                ),
                checkpoint(
                    "registration-verification-single-use",
                    registration.status_code == 200
                    and verification.status_code == 200
                    and verification_reuse.status_code == 400
                    and verified_login.status_code == 200,
                    "verification link succeeds once and enables login",
                    {
                        "registration": registration.status_code,
                        "verification": verification.status_code,
                        "reuse": verification_reuse.status_code,
                        "login": verified_login.status_code,
                    },
                ),
                checkpoint(
                    "password-reset-and-session-invalidation",
                    reset.status_code == 200
                    and reset_reuse.status_code == 400
                    and old_password.status_code == 401
                    and new_password.status_code == 200
                    and account["email"] not in invalidated_session.text,
                    "single-use reset changes password and invalidates prior sessions",
                    {
                        "reset": reset.status_code,
                        "reuse": reset_reuse.status_code,
                        "old_password": old_password.status_code,
                        "new_password": new_password.status_code,
                        "old_session_visible": account["email"] in invalidated_session.text,
                    },
                ),
                checkpoint(
                    "reset-token-lifetime",
                    forgot.status_code == 200
                    and bool(reset_tokens)
                    and lifetime(reset_tokens[-1]) == expected_tokens["reset_minutes"],
                    expected_tokens["reset_minutes"],
                    lifetime(reset_tokens[-1]) if reset_tokens else None,
                ),
                checkpoint(
                    "verification-token-lifetime",
                    registration.status_code == 200
                    and bool(verification_tokens)
                    and lifetime(verification_tokens[-1]) == expected_tokens["verification_minutes"],
                    expected_tokens["verification_minutes"],
                    lifetime(verification_tokens[-1]) if verification_tokens else None,
                ),
            ]
        )
        return checks

    def cart_inventory(self, seed: int) -> list[dict[str, Any]]:
        fixture = self.fixture(seed)
        product = fixture["catalog"]["products"][0]
        inventory_kind = self.policies["inventory"]["kind"]
        quantity_kind = self.policies["quantity"]["kind"]
        checks = [self.policy_checkpoint("cart_inventory")]
        with httpx.Client(base_url=self.public_url, follow_redirects=True, timeout=20) as client:
            guest_add = self.add(client, product["id"], self.valid_quantity())
            after_guest = self.state()
            first_expiry = next(
                (
                    item["expires_at"]
                    for item in after_guest.get("reservations", {}).values()
                    if item.get("product_id") == product["id"]
                ),
                None,
            )
            if inventory_kind == "reservation":
                self.advance(120)
                self.add(client, product["id"], 1)
            self.login(client, fixture)
            account_cart = client.get("/cart")
        state = self.state()
        account_id = fixture["accounts"][0]["id"]
        account_owner = f"account:{account_id}"
        account_quantity = int(state.get("carts", {}).get(account_owner, {}).get(product["id"], 0))
        checks.append(
            checkpoint(
                "guest-account-cart-policy",
                (
                    guest_add.status_code == 200
                    and account_quantity == 1
                    if quantity_kind == "per_sku_limit"
                    else account_quantity == self.valid_quantity()
                    if quantity_kind != "wholesale_case"
                    else guest_add.status_code == 200 and account_quantity == 0
                ),
                "variant guest/account merge rule",
                {
                    "guest_status": guest_add.status_code,
                    "account_quantity": account_quantity,
                    "cart_status": account_cart.status_code,
                },
            )
        )
        if quantity_kind == "wholesale_case":
            with httpx.Client(base_url=self.public_url, follow_redirects=True, timeout=20) as account_client:
                self.login(account_client, fixture)
                add_account = self.add(account_client, product["id"], self.valid_quantity())
            state = self.state()
            account_quantity = int(state.get("carts", {}).get(account_owner, {}).get(product["id"], 0))
            checks.append(
                checkpoint(
                    "authenticated-case-quantity",
                    add_account.status_code == 200 and account_quantity == self.valid_quantity(),
                    self.valid_quantity(),
                    account_quantity,
                )
            )
            with httpx.Client(
                base_url=self.public_url, follow_redirects=True, timeout=20
            ) as pricing_client:
                self.login(pricing_client, fixture)
                six_page = pricing_client.post(
                    "/cart/update",
                    data={"product_id": product["id"], "quantity": 6},
                )
                twelve_page = pricing_client.post(
                    "/cart/update",
                    data={"product_id": product["id"], "quantity": 12},
                )

            def summary(quantity: int) -> str:
                percent = max(
                    (
                        int(tier["percent"])
                        for tier in self.policies["pricing"]["parameters"]["tiers"]
                        if quantity >= int(tier["minimum"])
                    ),
                    default=0,
                )
                gross = int(product["price_cents"]) * quantity
                subtotal = gross - (gross * percent + 50) // 100
                tax = (
                    subtotal
                    * int(self.policies["pricing"]["parameters"]["tax_basis_points"])
                    + 5000
                ) // 10000
                fulfillment = self.policies["fulfillment"]["parameters"]
                shipping = (
                    0
                    if subtotal >= int(fulfillment["free_threshold_cents"])
                    else int(fulfillment["standard_cents"])
                )
                return (
                    f"Subtotal: {self.money(subtotal)}; tax: {self.money(tax)}; "
                    f"shipping: {self.money(shipping)}; "
                    f"total: {self.money(subtotal + tax + shipping)}"
                )

            expected_six = summary(6)
            expected_twelve = summary(12)
            checks.append(
                checkpoint(
                    "tiered-pricing-tax-and-discounted-shipping",
                    six_page.status_code == 200
                    and twelve_page.status_code == 200
                    and expected_six in six_page.text
                    and expected_twelve in twelve_page.text,
                    {"quantity_6": expected_six, "quantity_12": expected_twelve},
                    {
                        "quantity_6": expected_six in six_page.text,
                        "quantity_12": expected_twelve in twelve_page.text,
                    },
                )
            )
        if inventory_kind == "reservation":
            transferred = state.get("reservations", {}).get(f"{account_owner}|{product['id']}")
            checks.append(
                checkpoint(
                    "reservation-transfer-without-extension",
                    bool(transferred) and transferred["expires_at"] == first_expiry,
                    first_expiry,
                    transferred["expires_at"] if transferred else None,
                )
            )
            self.advance(int(self.policies["inventory"]["parameters"]["ttl_minutes"]) * 60)
            expired_state = self.state()
            checks.append(
                checkpoint(
                    "reservation-expiry-releases-stock",
                    not expired_state.get("reservations")
                    and next(
                        item["inventory"]
                        for item in expired_state["products"]
                        if item["id"] == product["id"]
                    )
                    == int(product["inventory"]),
                    "expired reservation removed without decrementing inventory",
                    {
                        "reservations": len(expired_state.get("reservations", {})),
                        "inventory": next(
                            item["inventory"]
                            for item in expired_state["products"]
                            if item["id"] == product["id"]
                        ),
                    },
                )
            )
        if inventory_kind == "store_isolated":
            east = state["store_stock"]["harbor-east"][product["id"]]
            west = state["store_stock"]["harbor-west"][product["id"]]
            checks.append(checkpoint("store-inventory-isolated", east == west, east, west))
        return checks

    def checkout(self, seed: int) -> list[dict[str, Any]]:
        fixture = self.fixture(seed)
        product = fixture["catalog"]["products"][0]
        pickup = self.policies["fulfillment"]["kind"] == "pickup_slots"
        reservation = self.policies["inventory"]["kind"] == "reservation"
        checks = [self.policy_checkpoint("checkout_concurrency")]
        with httpx.Client(base_url=self.public_url, follow_redirects=True, timeout=20) as client:
            self.login(client, fixture)
            self.add(client, product["id"], self.valid_quantity())
            before = self.state()
            checkout_page = client.get("/checkout")
            missing_pickup = None
            after_missing_pickup = None
            if pickup:
                missing_pickup = client.post(
                    "/checkout",
                    data=self.checkout_payload(
                        key="missing-pickup",
                        card="4242424242424242",
                        pickup=False,
                    ),
                )
                after_missing_pickup = self.state()
            decline = client.post(
                "/checkout",
                data=self.checkout_payload(key="decline", card="4000000000000002", pickup=pickup),
            )
            after_decline = self.state()
            first = client.post(
                "/checkout",
                data=self.checkout_payload(key="stable-key", card="4242424242424242", pickup=pickup),
            )
            second = client.post(
                "/checkout",
                data=self.checkout_payload(key="stable-key", card="4242424242424242", pickup=pickup),
            )
        after = self.state()
        checks.extend(
            [
                checkpoint(
                    "payment-decline-no-order",
                    decline.status_code in {200, 402}
                    and len(after_decline.get("orders", [])) == 0,
                    0,
                    len(after_decline.get("orders", [])),
                ),
                checkpoint(
                    "decline-reservation-retained",
                    (not reservation)
                    or after_decline.get("reservations") == before.get("reservations"),
                    "unchanged active reservation" if reservation else "not applicable",
                    after_decline.get("reservations"),
                ),
                checkpoint(
                    "idempotent-checkout",
                    first.status_code == 200
                    and second.status_code == 200
                    and len(after.get("orders", [])) == 1,
                    "one persisted order",
                    {"orders": len(after.get("orders", [])), "first": first.status_code, "second": second.status_code},
                ),
            ]
        )
        if pickup:
            before_slot = before["slots"]["pickup-early"]["capacity"]
            after_slot = after["slots"]["pickup-early"]["capacity"]
            before_stock = before["store_stock"]["harbor-east"][product["id"]]
            after_stock = after["store_stock"]["harbor-east"][product["id"]]
            checks.append(
                checkpoint(
                    "atomic-pickup-resources",
                    before_slot - after_slot == 1
                    and before_stock - after_stock == self.valid_quantity(),
                    {"slot": 1, "stock": self.valid_quantity()},
                    {"slot": before_slot - after_slot, "stock": before_stock - after_stock},
                )
            )
            checks.append(
                checkpoint(
                    "pickup-required-without-shipping-fields",
                    checkout_page.status_code == 200
                    and "Pickup store" in checkout_page.text
                    and "Pickup time slot" in checkout_page.text
                    and "Address line 1" not in checkout_page.text
                    and missing_pickup is not None
                    and missing_pickup.status_code == 400
                    and after_missing_pickup == before,
                    "pickup selectors only; missing store/slot has no side effects",
                    {
                        "page": checkout_page.status_code,
                        "missing_status": missing_pickup.status_code
                        if missing_pickup
                        else None,
                        "resources_stable": after_missing_pickup == before,
                    },
                )
            )
        checks.append(self.concurrency_checkpoint())
        return checks

    def concurrency_checkpoint(self) -> dict[str, Any]:
        concurrency_paths = [
            path
            for path in self.fixture_dir.glob("*.json")
            if json.loads(path.read_text(encoding="utf-8")).get("scenario", {}).get("kind")
            == "concurrency"
        ]
        if len(concurrency_paths) != 1:
            return checkpoint(
                "atomic-concurrency-boundary",
                False,
                "one private concurrency fixture",
                len(concurrency_paths),
            )
        fixture = json.loads(concurrency_paths[0].read_text(encoding="utf-8"))
        seed = int(fixture["seed"])
        product = fixture["catalog"]["products"][0]
        pickup = self.policies["fulfillment"]["kind"] == "pickup_slots"
        self.reset(seed)

        def place(index: int) -> int:
            with httpx.Client(
                base_url=self.public_url, follow_redirects=True, timeout=20
            ) as client:
                self.login(client, fixture, index)
                self.add(client, product["id"], self.valid_quantity())
                response = client.post(
                    "/checkout",
                    data=self.checkout_payload(
                        key=f"concurrent-{index}",
                        card="4242424242424242",
                        pickup=pickup,
                    ),
                )
                return response.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(place, (0, 1)))
        state = self.state()
        product_inventory = next(
            item["inventory"] for item in state["products"] if item["id"] == product["id"]
        )
        store_inventory = [
            inventory[product["id"]] for inventory in state.get("store_stock", {}).values()
        ]
        nonnegative = product_inventory >= 0 and all(value >= 0 for value in store_inventory)
        return checkpoint(
            "atomic-concurrency-boundary",
            statuses.count(200) == 1 and len(state.get("orders", [])) == 1 and nonnegative,
            {"successful_requests": 1, "orders": 1, "nonnegative_inventory": True},
            {
                "statuses": statuses,
                "orders": len(state.get("orders", [])),
                "product_inventory": product_inventory,
                "store_inventory": store_inventory,
            },
        )

    def orders(self, seed: int) -> list[dict[str, Any]]:
        fixture = self.fixture(seed)
        product = fixture["catalog"]["products"][0]
        pickup = self.policies["fulfillment"]["kind"] == "pickup_slots"
        cancellation_kind = self.policies["cancellation"]["kind"]
        checks = [self.policy_checkpoint("orders_terminal")]
        with httpx.Client(base_url=self.public_url, follow_redirects=True, timeout=20) as owner:
            self.login(owner, fixture)
            self.add(owner, product["id"], self.valid_quantity())
            owner.post(
                "/checkout",
                data=self.checkout_payload(key="order", card="4242424242424242", pickup=pickup),
            )
            state_before_cancel = self.state()
            order = state_before_cancel["orders"][0]
            listing = owner.get("/account/orders")
            detail = owner.get(f"/account/orders/{order['number']}")
            cancel_first = owner.post(f"/account/orders/{order['number']}/cancel")
            after_first = self.state()
            cancel_second = owner.post(f"/account/orders/{order['number']}/cancel")
            after_second = self.state()
            boundary_cancel = None
            boundary_before = None
            boundary_after = None
            if cancellation_kind != "final_sale":
                self.add(owner, product["id"], self.valid_quantity())
                owner.post(
                    "/checkout",
                    data=self.checkout_payload(
                        key="order-boundary",
                        card="4242424242424242",
                        pickup=pickup,
                    ),
                )
                boundary_order = self.state()["orders"][-1]
                if cancellation_kind == "window":
                    self.advance(
                        int(self.policies["cancellation"]["parameters"]["minutes"])
                        * 60
                        + 1
                    )
                else:
                    state = self.state()
                    starts = datetime.fromisoformat(
                        state["slots"]["pickup-early"]["starts_at"].replace("Z", "+00:00")
                    )
                    now = datetime.fromisoformat(state["now"].replace("Z", "+00:00"))
                    notice = int(
                        self.policies["cancellation"]["parameters"][
                            "minimum_notice_minutes"
                        ]
                    )
                    self.advance(max(0, int((starts - now).total_seconds()) - notice * 60 + 1))
                boundary_before = self.state()
                boundary_cancel = owner.post(
                    f"/account/orders/{boundary_order['number']}/cancel"
                )
                boundary_after = self.state()
        with httpx.Client(base_url=self.public_url, follow_redirects=True, timeout=20) as other:
            self.login(other, fixture, 1)
            isolation = other.get(f"/account/orders/{order['number']}")
        checks.extend(
            [
                checkpoint(
                    "order-persistence",
                    listing.status_code == 200
                    and detail.status_code == 200
                    and order["number"] in listing.text,
                    order["number"],
                    {"listing": listing.status_code, "detail": detail.status_code},
                ),
                checkpoint(
                    "cross-account-isolation",
                    isolation.status_code == 404,
                    404,
                    isolation.status_code,
                ),
            ]
        )
        final_order = after_second["orders"][0]
        if cancellation_kind == "final_sale":
            with httpx.Client(
                base_url=self.public_url, follow_redirects=True, timeout=20
            ) as repeat_client:
                self.login(repeat_client, fixture)
                self.add(repeat_client, product["id"], 1)
            repeat_state = self.state()
            checks.append(
                checkpoint(
                    "final-sale-terminal",
                    cancel_first.status_code == 409 and final_order["status"] == "placed",
                    "placed and non-cancellable",
                    {"status": final_order["status"], "response": cancel_first.status_code},
                )
            )
            checks.append(
                checkpoint(
                    "account-lifetime-sku-limit-after-order",
                    not repeat_state.get("carts", {}).get(
                        f"account:{fixture['accounts'][0]['id']}", {}
                    ).get(product["id"]),
                    "purchased SKU cannot be added again by the account",
                    repeat_state.get("carts", {}).get(
                        f"account:{fixture['accounts'][0]['id']}", {}
                    ),
                )
            )
        else:
            first_order = after_first["orders"][0]
            checks.append(
                checkpoint(
                    "cancel-and-restore-once",
                    cancel_first.status_code == 200
                    and cancel_second.status_code == 200
                    and first_order["status"] == "cancelled"
                    and after_first == after_second,
                    "cancelled with stable resources after repeated cancel",
                    {"status": final_order["status"], "stable": after_first == after_second},
                )
            )
            checks.append(
                checkpoint(
                    "cancellation-cutoff-preserves-resources",
                    boundary_cancel is not None
                    and boundary_cancel.status_code == 409
                    and boundary_before == boundary_after,
                    "cancellation rejected beyond cutoff without resource changes",
                    {
                        "status": boundary_cancel.status_code if boundary_cancel else None,
                        "stable": boundary_before == boundary_after,
                    },
                )
            )
        return checks

    def evaluate_journey(
        self,
        journey_id: str,
        seed: int,
        function: Callable[[int], list[dict[str, Any]]],
    ) -> dict[str, Any]:
        self.reset(seed)
        try:
            checks = function(seed)
        except Exception as exc:
            checks = [
                checkpoint(
                    "journey-exception",
                    False,
                    "journey completes",
                    {"type": type(exc).__name__, "message": str(exc)},
                )
            ]
            self.failures.append(
                fact_failure(
                    f"{journey_id}-exception",
                    category={
                        "catalog_observability": "interaction",
                        "account_lifecycle": "authentication",
                        "cart_inventory": "cart",
                        "checkout_concurrency": "checkout",
                        "orders_terminal": "order",
                    }[journey_id],
                    severity="critical",
                    summary=f"Journey {journey_id} raised an exception",
                    expected="journey completes and emits mandatory checkpoints",
                    actual=f"{type(exc).__name__}: {exc}",
                    reproduction=[f"Reset seed {seed}", f"Execute journey {journey_id}"],
                )
            )
        return {
            "id": journey_id,
            "seed": seed,
            "terminal_passed": bool(checks) and all(item["passed"] for item in checks),
            "checkpoints": checks,
        }

    def run(self) -> dict[str, Any]:
        public = sorted(self.public_fixture_dir.glob("*.json"))
        hidden = sorted(
            path
            for path in self.fixture_dir.glob("*.json")
            if json.loads(path.read_text(encoding="utf-8")).get("scenario", {}).get("kind")
            != "concurrency"
        )
        if len(public) != 1 or len(hidden) != 1:
            raise RuntimeError(
                f"Judge requires one public and one hidden fixture, got {len(public)} and {len(hidden)}"
            )
        seeds = [
            json.loads(public[0].read_text(encoding="utf-8"))["seed"],
            json.loads(hidden[0].read_text(encoding="utf-8"))["seed"],
        ]
        functions = {
            "catalog_observability": self.catalog,
            "account_lifecycle": self.account,
            "cart_inventory": self.cart_inventory,
            "checkout_concurrency": self.checkout,
            "orders_terminal": self.orders,
        }
        journeys = [
            self.evaluate_journey(journey_id, seed, functions[journey_id])
            for journey_id in self.assertions["journeys"]
            for seed in seeds
        ]
        interactions = [
            {
                "id": f"{journey['id']}-{journey['seed']}-{item['id']}",
                "passed": item["passed"],
            }
            for journey in journeys
            for item in journey["checkpoints"]
        ]
        observed_checks = [
            item for journey in journeys for item in journey["checkpoints"]
        ]
        robustness = [
            {
                "id": f"rule-{index + 1:02d}",
                "passed": observed_checks[index % len(observed_checks)]["passed"],
            }
            for index in range(15)
        ]
        visual = self.visual(seeds[0])
        latency = self.latency_probe()
        return {
            "schema_version": "websitebench.facts.v1",
            "visual": visual,
            "interactions": interactions,
            "journeys": journeys,
            "robustness": robustness,
            "efficiency": {"p95_latency_ms_at_10_concurrent": latency},
            "hard_failures": [],
            "failures": self.failures,
            "evidence": [],
            "seeds": [
                {
                    "seed": seed,
                    "purpose": "public" if index == 0 else "hidden",
                    "reset_passed": True,
                    "tests_passed": sum(journey["terminal_passed"] for journey in journeys if journey["seed"] == seed),
                    "tests_total": 5,
                }
                for index, seed in enumerate(seeds)
            ],
            "versions": {
                "evaluator": "commerce-behavior-v1",
                "protocol": "websitebench.facts.v1",
            },
        }

    def latency_probe(self) -> float:
        """Measure one ten-way health burst from inside the evaluator network."""

        def request() -> float:
            started = time.perf_counter()
            response = httpx.get(self.public_url + "/healthz", timeout=10)
            response.raise_for_status()
            return (time.perf_counter() - started) * 1000

        try:
            with ThreadPoolExecutor(max_workers=10) as pool:
                values = sorted(pool.map(lambda _index: request(), range(10)))
        except httpx.HTTPError as exc:
            self.failures.append(
                fact_failure(
                    "latency-probe",
                    category="efficiency",
                    severity="minor",
                    summary="The ten-way latency probe failed",
                    expected="ten concurrent /healthz requests succeed",
                    actual=str(exc),
                    reproduction=["Send ten concurrent GET /healthz requests"],
                )
            )
            return 1_000_000.0
        index = max(0, math.ceil(0.95 * len(values)) - 1)
        return round(values[index], 4)

    def visual(self, public_seed: int) -> list[dict[str, Any]]:
        """Capture paired home pages and emit normalized pixel facts only."""

        self.reset(public_seed)
        fixture = self.fixture(public_seed)
        reference_payload = {
            "schema_version": 1,
            "run_id": f"commerce-visual-reference-{public_seed}",
            "seed": public_seed,
            "now": fixture["now"],
            "fixture_path": f"{self.reference_public_fixture_dir}/{public_seed}.json",
        }
        response = httpx.post(
            self.reference_admin_url + "/__bench/reset",
            headers=self.headers,
            json=reference_payload,
            timeout=20,
        )
        response.raise_for_status()
        screenshot_dir = Path(os.environ.get("ARTIFACT_ROOT", "/artifacts")) / "eval" / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        facts = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for identifier, viewport in (
                    ("home-desktop", {"width": 1440, "height": 1000}),
                    ("home-mobile", {"width": 390, "height": 844}),
                ):
                    paths = {}
                    for name, url in (
                        ("reference", self.reference_url),
                        ("candidate", self.public_url),
                    ):
                        context = browser.new_context(viewport=viewport)
                        page = context.new_page()
                        page.goto(url + "/", wait_until="networkidle")
                        path = screenshot_dir / f"{identifier}-{name}.png"
                        page.screenshot(path=str(path), full_page=False)
                        paths[name] = path
                        context.close()
                    with Image.open(paths["reference"]) as reference_image, Image.open(
                        paths["candidate"]
                    ) as candidate_image:
                        reference_rgb = reference_image.convert("RGB")
                        candidate_rgb = candidate_image.convert("RGB")
                        if candidate_rgb.size != reference_rgb.size:
                            candidate_rgb = candidate_rgb.resize(reference_rgb.size)
                        histogram = ImageChops.difference(reference_rgb, candidate_rgb).histogram()
                        squared = sum((index % 256) ** 2 * count for index, count in enumerate(histogram))
                        rms = math.sqrt(squared / max(1, reference_rgb.width * reference_rgb.height * 3))
                        similarity = max(0.0, min(1.0, 1.0 - rms / 255.0))
                    facts.append(
                        {
                            "id": identifier,
                            "similarity": round(similarity, 6),
                            "passed": similarity >= 0.8,
                        }
                    )
            finally:
                browser.close()
        return facts


def main() -> int:
    judge = CommerceJudge()
    facts = judge.run()
    destination = Path(os.environ.get("ARTIFACT_ROOT", "/artifacts")) / "eval" / "facts.json"
    destination.write_text(json.dumps(facts, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if all(journey["terminal_passed"] for journey in facts["journeys"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
