"""Persistent runtime domain model for compiled commerce variants.

The browser application is intentionally thin.  This module owns deterministic
state, controlled time, account/cart/order persistence, and the closed policy
interpreter used by the Foundry, Ember, and Harbor reference variants.
"""

from __future__ import annotations

import json
import secrets
import threading
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Mapping

from .commerce import validate_policies
from .commerce_contract import AccountOrderCommerce


def _epoch(value: str | datetime) -> int:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(parsed.astimezone(timezone.utc).timestamp())


def _utc(value: int) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _money(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class DomainError(ValueError):
    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


class PersistentCommerce(AccountOrderCommerce):
    """Thread-safe JSON-backed commerce state for one compiled policy profile."""

    def __init__(
        self,
        path: Path | str,
        *,
        spec: Mapping[str, Any],
        initial_fixture: Mapping[str, Any],
    ) -> None:
        validate_policies(spec["policies"])
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.spec = deepcopy(dict(spec))
        self.policies = deepcopy(spec["policies"])
        self.lock = threading.RLock()
        if self.path.is_file():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data: dict[str, Any] = {}
            self.reset(initial_fixture, run_id="bootstrap")

    @property
    def now(self) -> int:
        return int(self.data["now"])

    def _save(self) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(self.path)

    def reset(self, fixture: Mapping[str, Any], *, run_id: str) -> dict[str, Any]:
        with self.lock:
            seed = int(fixture["seed"])
            now = _epoch(str(fixture["now"]))
            products = {item["id"]: deepcopy(item) for item in fixture["catalog"]["products"]}
            users = {}
            for account in fixture["accounts"]:
                users[account["id"]] = {
                    "id": account["id"],
                    "email": account["email"].strip().casefold(),
                    "password": account["password"],
                    "verified": bool(account["verified"]),
                    "full_name": account["full_name"],
                }
            capacity = int(
                fixture.get("scenario", {}).get(
                    "slot_capacity_override",
                    self.policies["fulfillment"]["parameters"].get("slot_capacity", 0),
                )
            )
            store_stock = {
                store: {
                    product_id: int(product["inventory"])
                    for product_id, product in products.items()
                }
                for store in ("harbor-east", "harbor-west")
            }
            self.data = {
                "schema_version": "websitebench.commerce-state.v1",
                "run_id": run_id,
                "seed": seed,
                "now": now,
                "categories": deepcopy(fixture["catalog"]["categories"]),
                "products": products,
                "users": users,
                "sessions": {},
                "tokens": {},
                "carts": {},
                "reservations": {},
                "orders": {},
                "idempotency": {},
                "lifetime_purchases": {},
                "store_stock": store_stock,
                "slots": {
                    "pickup-early": {
                        "starts_at": _utc(now + 4 * 3600),
                        "capacity": capacity,
                    },
                    "pickup-late": {
                        "starts_at": _utc(now + 24 * 3600),
                        "capacity": capacity,
                    },
                },
                "order_sequence": 0,
            }
            self._save()
            return self.normalized_state()

    def products(self, *, query: str = "") -> list[dict[str, Any]]:
        needle = query.strip().casefold()
        values = list(self.data["products"].values())
        if needle:
            values = [
                item
                for item in values
                if needle in " ".join(
                    [item["title"], item["description"], item["brand"], *item["tags"]]
                ).casefold()
            ]
        return sorted(values, key=lambda item: (item["featured_rank"], item["id"]))

    def product_by_slug(self, slug: str) -> dict[str, Any] | None:
        return next(
            (deepcopy(item) for item in self.data["products"].values() if item["slug"] == slug),
            None,
        )

    def user_for_session(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        with self.lock:
            session = self.data["sessions"].get(token)
            if not session or int(session["expires_at"]) <= self.now:
                return None
            return deepcopy(self.data["users"].get(session["user_id"]))

    def _user_by_email(self, email: str) -> dict[str, Any] | None:
        normalized = email.strip().casefold()
        return next(
            (item for item in self.data["users"].values() if item["email"] == normalized),
            None,
        )

    def register(self, email: str, password: str, confirm: str) -> str:
        with self.lock:
            normalized = email.strip().casefold()
            if "@" not in normalized:
                raise DomainError("invalid_email", "Enter a valid email address.")
            if len(password) < 10:
                raise DomainError("weak_password", "Password must contain at least 10 characters.")
            if password != confirm:
                raise DomainError("password_mismatch", "Passwords do not match.")
            if self._user_by_email(normalized):
                raise DomainError("duplicate_email", "An account already exists for this email.", status=409)
            user_id = f"user_runtime_{len(self.data['users']) + 1:04d}"
            self.data["users"][user_id] = {
                "id": user_id,
                "email": normalized,
                "password": password,
                "verified": False,
                "full_name": normalized.split("@", 1)[0],
            }
            token = self._token("verification", user_id)
            self._save()
            return token

    def _token(self, kind: str, user_id: str) -> str:
        minutes = int(
            self.policies["token_lifetime"]["parameters"][
                "verification_minutes" if kind == "verification" else "reset_minutes"
            ]
        )
        token = secrets.token_urlsafe(24)
        self.data["tokens"][token] = {
            "kind": kind,
            "user_id": user_id,
            "issued_at": self.now,
            "expires_at": self.now + minutes * 60,
            "used_at": None,
        }
        return token

    def verify(self, token: str) -> None:
        with self.lock:
            record = self.data["tokens"].get(token)
            if not record or record["kind"] != "verification" or record["used_at"] is not None:
                raise DomainError("invalid_token", "Verification link is invalid.")
            if int(record["expires_at"]) <= self.now:
                raise DomainError("expired_token", "Verification link has expired.")
            self.data["users"][record["user_id"]]["verified"] = True
            record["used_at"] = self.now
            self._save()

    def forgot_password(self, email: str) -> str | None:
        with self.lock:
            user = self._user_by_email(email)
            if not user:
                return None
            token = self._token("reset", user["id"])
            self._save()
            return token

    def reset_password(self, token: str, password: str, confirm: str) -> None:
        with self.lock:
            record = self.data["tokens"].get(token)
            if not record or record["kind"] != "reset" or record["used_at"] is not None:
                raise DomainError("invalid_token", "Reset link is invalid.")
            if int(record["expires_at"]) <= self.now:
                raise DomainError("expired_token", "Reset link has expired.")
            if len(password) < 10 or password != confirm:
                raise DomainError("invalid_password", "Passwords must match and contain 10 characters.")
            user_id = record["user_id"]
            self.data["users"][user_id]["password"] = password
            record["used_at"] = self.now
            self.data["sessions"] = {
                key: value
                for key, value in self.data["sessions"].items()
                if value["user_id"] != user_id
            }
            self._save()

    def login(self, email: str, password: str, *, device: str) -> str:
        with self.lock:
            user = self._user_by_email(email)
            if not user or user["password"] != password:
                raise DomainError("invalid_login", "Email or password is incorrect.", status=401)
            if not user["verified"]:
                raise DomainError("unverified_login", "Verify your email before signing in.", status=403)
            token = secrets.token_urlsafe(24)
            self.data["sessions"][token] = {
                "user_id": user["id"],
                "created_at": self.now,
                "expires_at": self.now + 86400,
            }
            self._merge_guest_cart(device, user["id"])
            self._save()
            return token

    def logout(self, token: str | None, *, device: str | None = None) -> None:
        del device
        with self.lock:
            if token:
                self.data["sessions"].pop(token, None)
                self._save()

    @staticmethod
    def owner_key(*, user: Mapping[str, Any] | None, device: str) -> str:
        return f"account:{user['id']}" if user else f"guest:{device}"

    def _merge_guest_cart(self, device: str, user_id: str) -> None:
        guest = f"guest:{device}"
        account = f"account:{user_id}"
        guest_cart = self.data["carts"].pop(guest, {})
        account_cart = self.data["carts"].setdefault(account, {})
        reservation_policy = self.policies["inventory"]["kind"] == "reservation"
        for product_id, quantity in guest_cart.items():
            if reservation_policy:
                account_cart[product_id] = max(int(account_cart.get(product_id, 0)), int(quantity))
                guest_reservation = self.data["reservations"].pop(
                    f"{guest}|{product_id}", None
                )
                if guest_reservation:
                    account_key = f"{account}|{product_id}"
                    existing = self.data["reservations"].get(account_key)
                    if existing:
                        existing["quantity"] = max(existing["quantity"], guest_reservation["quantity"])
                        existing["expires_at"] = min(existing["expires_at"], guest_reservation["expires_at"])
                    else:
                        guest_reservation["owner"] = account
                        self.data["reservations"][account_key] = guest_reservation
            else:
                merged = int(account_cart.get(product_id, 0)) + int(quantity)
                try:
                    self._validate_quantity(account, product_id, merged, authenticated=True)
                except DomainError:
                    merged = int(account_cart.get(product_id, 0)) or int(quantity)
                account_cart[product_id] = merged

    def _validate_quantity(
        self,
        owner: str,
        product_id: str,
        quantity: int,
        *,
        authenticated: bool,
    ) -> None:
        policy = self.policies["quantity"]
        parameters = policy["parameters"]
        if quantity < 1:
            raise DomainError("invalid_quantity", "Quantity must be positive.")
        if parameters.get("login_required") and not authenticated:
            raise DomainError("login_required", "Sign in before adding wholesale items.", status=401)
        if policy["kind"] == "standard_cap" and quantity > int(parameters["maximum"]):
            raise DomainError("quantity_cap", "Quantity exceeds the cart limit.", status=409)
        if policy["kind"] == "wholesale_case":
            if not int(parameters["minimum"]) <= quantity <= int(parameters["maximum"]):
                raise DomainError("quantity_bounds", "Quantity is outside wholesale bounds.", status=409)
            if quantity % int(parameters["case_size"]):
                raise DomainError("case_quantity", "Quantity must be a complete case.", status=409)
        if policy["kind"] == "per_sku_limit":
            purchased = int(self.data["lifetime_purchases"].get(f"{owner}|{product_id}", 0))
            if purchased + quantity > int(parameters["limit"]):
                raise DomainError("lifetime_limit", "This SKU is limited to one per account.", status=409)
        if policy["kind"] == "store_stock":
            if not int(parameters["minimum"]) <= quantity <= int(parameters["maximum"]):
                raise DomainError("quantity_bounds", "Quantity is outside store limits.", status=409)

    def _release_expired(self) -> None:
        self.data["reservations"] = {
            key: value
            for key, value in self.data["reservations"].items()
            if int(value["expires_at"]) > self.now
        }

    def _available(self, product_id: str, *, excluding_owner: str | None = None) -> int:
        self._release_expired()
        reserved = sum(
            int(value["quantity"])
            for value in self.data["reservations"].values()
            if value["product_id"] == product_id and value["owner"] != excluding_owner
        )
        return int(self.data["products"][product_id]["inventory"]) - reserved

    def add_to_cart(
        self,
        *,
        product_id: str,
        quantity: int,
        user: Mapping[str, Any] | None,
        device: str,
    ) -> None:
        with self.lock:
            if product_id not in self.data["products"]:
                raise DomainError("product_not_found", "Product not found.", status=404)
            owner = self.owner_key(user=user, device=device)
            cart = self.data["carts"].setdefault(owner, {})
            if self.policies["quantity"]["kind"] == "per_sku_limit":
                new_quantity = max(int(cart.get(product_id, 0)), quantity)
            else:
                new_quantity = int(cart.get(product_id, 0)) + quantity
            self._validate_quantity(owner, product_id, new_quantity, authenticated=user is not None)
            if self.policies["inventory"]["kind"] == "reservation":
                key = f"{owner}|{product_id}"
                existing = self.data["reservations"].get(key)
                if new_quantity > self._available(product_id, excluding_owner=owner):
                    raise DomainError("insufficient_stock", "Not enough inventory is available.", status=409)
                if existing:
                    existing["quantity"] = new_quantity
                else:
                    ttl = int(self.policies["inventory"]["parameters"]["ttl_minutes"])
                    self.data["reservations"][key] = {
                        "owner": owner,
                        "product_id": product_id,
                        "quantity": new_quantity,
                        "created_at": self.now,
                        "expires_at": self.now + ttl * 60,
                    }
            elif new_quantity > int(self.data["products"][product_id]["inventory"]):
                raise DomainError("insufficient_stock", "Not enough inventory is available.", status=409)
            cart[product_id] = new_quantity
            self._save()

    def update_cart(
        self,
        *,
        product_id: str,
        quantity: int,
        user: Mapping[str, Any] | None,
        device: str,
    ) -> None:
        with self.lock:
            owner = self.owner_key(user=user, device=device)
            cart = self.data["carts"].setdefault(owner, {})
            if quantity <= 0:
                cart.pop(product_id, None)
                self.data["reservations"].pop(f"{owner}|{product_id}", None)
                self._save()
                return
            old = int(cart.get(product_id, 0))
            cart[product_id] = 0
            try:
                self.add_to_cart(
                    product_id=product_id,
                    quantity=quantity,
                    user=user,
                    device=device,
                )
            except Exception:
                cart[product_id] = old
                raise

    def cart(self, *, user: Mapping[str, Any] | None, device: str) -> dict[str, Any]:
        with self.lock:
            owner = self.owner_key(user=user, device=device)
            lines = []
            for product_id, quantity in sorted(self.data["carts"].get(owner, {}).items()):
                product = deepcopy(self.data["products"][product_id])
                line_total, discount = self._line_total(product["price_cents"], int(quantity))
                lines.append(
                    {
                        **product,
                        "quantity": int(quantity),
                        "line_total_cents": line_total,
                        "discount_cents": discount,
                    }
                )
            totals = self._totals(lines)
            return {"owner": owner, "lines": lines, "count": sum(item["quantity"] for item in lines), **totals}

    def _line_total(self, price: int, quantity: int) -> tuple[int, int]:
        percent = 0
        pricing = self.policies["pricing"]
        if pricing["kind"] == "tiered_line_discount":
            for tier in pricing["parameters"]["tiers"]:
                if quantity >= int(tier["minimum"]):
                    percent = int(tier["percent"])
        gross = price * quantity
        discount = _money(Decimal(gross) * Decimal(percent) / Decimal(100))
        return gross - discount, discount

    def _totals(self, lines: list[Mapping[str, Any]]) -> dict[str, int]:
        subtotal = sum(int(item["line_total_cents"]) for item in lines)
        tax = _money(
            Decimal(subtotal)
            * Decimal(int(self.policies["pricing"]["parameters"]["tax_basis_points"]))
            / Decimal(10_000)
        )
        shipping = 0
        fulfillment = self.policies["fulfillment"]
        if fulfillment["kind"] == "shipping":
            parameters = fulfillment["parameters"]
            threshold = int(parameters["free_threshold_cents"])
            shipping = (
                0
                if threshold > 0 and subtotal >= threshold
                else int(parameters["standard_cents"])
            )
        return {
            "subtotal_cents": subtotal,
            "tax_cents": tax,
            "shipping_cents": shipping,
            "total_cents": subtotal + tax + shipping,
        }

    def checkout(
        self,
        *,
        user: Mapping[str, Any] | None,
        device: str,
        idempotency_key: str,
        card_number: str,
        store: str | None = None,
        slot: str | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            if user is None:
                raise DomainError("login_required", "Sign in before checkout.", status=401)
            owner = self.owner_key(user=user, device=device)
            idempotency_id = f"{owner}|{idempotency_key}"
            existing_order = self.data["idempotency"].get(idempotency_id)
            if existing_order:
                return deepcopy(self.data["orders"][existing_order])
            cart = self.cart(user=user, device=device)
            if not cart["lines"]:
                raise DomainError("empty_cart", "Your cart is empty.", status=409)
            if card_number.replace(" ", "").endswith("0002"):
                raise DomainError("payment_declined", "The test payment was declined.", status=402)
            for line in cart["lines"]:
                self._validate_quantity(owner, line["id"], line["quantity"], authenticated=True)
            fulfillment = self.policies["fulfillment"]
            if fulfillment["kind"] == "pickup_slots":
                if store not in self.data["store_stock"] or slot not in self.data["slots"]:
                    raise DomainError("pickup_required", "Choose a pickup store and time slot.")
                if int(self.data["slots"][slot]["capacity"]) < 1:
                    raise DomainError("slot_full", "The pickup slot is full.", status=409)
                for line in cart["lines"]:
                    if int(self.data["store_stock"][store][line["id"]]) < line["quantity"]:
                        raise DomainError("insufficient_store_stock", "The selected store lacks inventory.", status=409)
                for line in cart["lines"]:
                    self.data["store_stock"][store][line["id"]] -= line["quantity"]
                self.data["slots"][slot]["capacity"] -= 1
            else:
                for line in cart["lines"]:
                    if self.policies["inventory"]["kind"] == "reservation":
                        reservation = self.data["reservations"].get(f"{owner}|{line['id']}")
                        if not reservation or int(reservation["expires_at"]) <= self.now:
                            raise DomainError("reservation_expired", "The inventory reservation expired.", status=409)
                    if int(self.data["products"][line["id"]]["inventory"]) < line["quantity"]:
                        raise DomainError("insufficient_stock", "Not enough inventory is available.", status=409)
                for line in cart["lines"]:
                    self.data["products"][line["id"]]["inventory"] -= line["quantity"]
                    self.data["reservations"].pop(f"{owner}|{line['id']}", None)
            self.data["order_sequence"] += 1
            number = f"{self.spec['variant_id'][:3].upper()}-{self.data['seed']}-{self.data['order_sequence']:06d}"
            order = {
                "number": number,
                "user_id": user["id"],
                "status": "placed",
                "placed_at": _utc(self.now),
                "cancelled_at": None,
                "lines": [
                    {
                        "product_id": line["id"],
                        "sku": line["sku"],
                        "title": line["title"],
                        "quantity": line["quantity"],
                        "unit_price_cents": line["price_cents"],
                    }
                    for line in cart["lines"]
                ],
                "subtotal_cents": cart["subtotal_cents"],
                "tax_cents": cart["tax_cents"],
                "shipping_cents": cart["shipping_cents"],
                "total_cents": cart["total_cents"],
                "store": store,
                "slot": slot,
                "slot_starts_at": self.data["slots"][slot]["starts_at"] if slot else None,
                "resources_restored": False,
            }
            self.data["orders"][number] = order
            self.data["idempotency"][idempotency_id] = number
            for line in cart["lines"]:
                key = f"{owner}|{line['id']}"
                self.data["lifetime_purchases"][key] = int(
                    self.data["lifetime_purchases"].get(key, 0)
                ) + line["quantity"]
            self.data["carts"][owner] = {}
            self._save()
            return deepcopy(order)

    def orders_for(self, user_id: str) -> list[dict[str, Any]]:
        return [
            deepcopy(item)
            for item in self.data["orders"].values()
            if item["user_id"] == user_id
        ]

    def order_for(self, number: str, user_id: str) -> dict[str, Any]:
        order = self.data["orders"].get(number)
        if not order or order["user_id"] != user_id:
            raise DomainError("order_not_found", "Order not found.", status=404)
        return deepcopy(order)

    def cancel(self, number: str, user_id: str) -> dict[str, Any]:
        with self.lock:
            order = self.data["orders"].get(number)
            if not order or order["user_id"] != user_id:
                raise DomainError("order_not_found", "Order not found.", status=404)
            if order["status"] == "cancelled":
                return deepcopy(order)
            policy = self.policies["cancellation"]
            if policy["kind"] == "final_sale":
                raise DomainError("final_sale", "Final sale orders cannot be cancelled.", status=409)
            if policy["kind"] == "window":
                closes = _epoch(order["placed_at"]) + int(policy["parameters"]["minutes"]) * 60
                if self.now > closes:
                    raise DomainError("cancellation_closed", "The cancellation window has closed.", status=409)
            if policy["kind"] == "pickup_cutoff":
                notice = _epoch(order["slot_starts_at"]) - self.now
                if notice < int(policy["parameters"]["minimum_notice_minutes"]) * 60:
                    raise DomainError("pickup_cutoff", "The pickup cancellation cutoff passed.", status=409)
            order["status"] = "cancelled"
            order["cancelled_at"] = _utc(self.now)
            if not order["resources_restored"]:
                for line in order["lines"]:
                    if order["store"]:
                        self.data["store_stock"][order["store"]][line["product_id"]] += line["quantity"]
                    else:
                        self.data["products"][line["product_id"]]["inventory"] += line["quantity"]
                if order["slot"]:
                    self.data["slots"][order["slot"]]["capacity"] += 1
                order["resources_restored"] = True
            self._save()
            return deepcopy(order)

    def advance(self, seconds: int) -> dict[str, Any]:
        if not 0 <= seconds <= 2_678_400:
            raise DomainError("invalid_clock", "Clock advance is outside the allowed range.")
        with self.lock:
            self.data["now"] += seconds
            self._release_expired()
            self._save()
            return {"now": _utc(self.now), "seconds": seconds}

    def normalized_state(self) -> dict[str, Any]:
        with self.lock:
            self._release_expired()
            return {
                "schema_version": "websitebench.state.v1",
                "run_id": self.data["run_id"],
                "seed": self.data["seed"],
                "now": _utc(self.now),
                "users": [
                    {key: item[key] for key in ("id", "email", "verified", "full_name")}
                    for item in sorted(self.data["users"].values(), key=lambda row: row["id"])
                ],
                "products": [
                    {
                        "id": item["id"],
                        "sku": item["sku"],
                        "price_cents": item["price_cents"],
                        "inventory": item["inventory"],
                    }
                    for item in sorted(self.data["products"].values(), key=lambda row: row["id"])
                ],
                "carts": deepcopy(self.data["carts"]),
                "reservations": deepcopy(self.data["reservations"]),
                "orders": [deepcopy(self.data["orders"][key]) for key in sorted(self.data["orders"])],
                "store_stock": deepcopy(self.data["store_stock"]),
                "slots": deepcopy(self.data["slots"]),
                "tokens": [
                    {
                        "kind": item["kind"],
                        "user_id": item["user_id"],
                        "issued_at": _utc(item["issued_at"]),
                        "expires_at": _utc(item["expires_at"]),
                        "used_at": _utc(item["used_at"]) if item["used_at"] is not None else None,
                    }
                    for item in self.data["tokens"].values()
                ],
                "policy_profile": deepcopy(self.policies),
            }
