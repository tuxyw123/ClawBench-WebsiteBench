"""Allowlisted commerce policy modules used by compiled reference variants.

This module is intentionally a closed interpreter.  Variant YAML selects a
named policy and typed parameters; it cannot import or execute code.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
import threading
from typing import Any, Mapping


POLICY_KINDS: Mapping[str, frozenset[str]] = {
    "quantity": frozenset({"standard_cap", "wholesale_case", "per_sku_limit", "store_stock"}),
    "pricing": frozenset({"standard", "tiered_line_discount"}),
    "inventory": frozenset({"checkout_decrement", "reservation", "store_isolated"}),
    "fulfillment": frozenset({"shipping", "pickup_slots"}),
    "cancellation": frozenset({"window", "final_sale", "pickup_cutoff"}),
    "token_lifetime": frozenset({"fixed"}),
}


class CommercePolicyError(ValueError):
    pass


def _exact_parameters(
    module: str,
    kind: str,
    parameters: Mapping[str, Any],
    *,
    required: Mapping[str, type],
    optional: Mapping[str, type] | None = None,
) -> None:
    optional = optional or {}
    missing = set(required) - set(parameters)
    extra = set(parameters) - set(required) - set(optional)
    if missing or extra:
        raise CommercePolicyError(
            f"{module}.{kind} parameters mismatch; missing={sorted(missing)}, extra={sorted(extra)}"
        )
    for name, expected in {**required, **optional}.items():
        if name not in parameters:
            continue
        value = parameters[name]
        if expected is int and (isinstance(value, bool) or not isinstance(value, int)):
            raise CommercePolicyError(f"{module}.{kind}.{name} must be an integer")
        if expected is bool and not isinstance(value, bool):
            raise CommercePolicyError(f"{module}.{kind}.{name} must be a boolean")
        if expected is str and not isinstance(value, str):
            raise CommercePolicyError(f"{module}.{kind}.{name} must be a string")
        if expected is list and not isinstance(value, list):
            raise CommercePolicyError(f"{module}.{kind}.{name} must be a list")


def _synchronized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return locked


def _money(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def validate_policies(policies: Mapping[str, Any]) -> None:
    if set(policies) != set(POLICY_KINDS):
        missing = sorted(set(POLICY_KINDS) - set(policies))
        extra = sorted(set(policies) - set(POLICY_KINDS))
        raise CommercePolicyError(f"policy modules mismatch; missing={missing}, extra={extra}")
    for module, policy in policies.items():
        kind = policy.get("kind")
        if kind not in POLICY_KINDS[module]:
            raise CommercePolicyError(
                f"unknown {module} policy {kind!r}; allowed: {sorted(POLICY_KINDS[module])}"
            )
        parameters = policy.get("parameters")
        if not isinstance(parameters, dict):
            raise CommercePolicyError(f"{module}.parameters must be a mapping")
    quantity = policies["quantity"]
    q = quantity["parameters"]
    if quantity["kind"] == "standard_cap":
        _exact_parameters("quantity", "standard_cap", q, required={"maximum": int})
        if q["maximum"] < 1:
            raise CommercePolicyError("standard quantity maximum must be positive")
    if quantity["kind"] == "wholesale_case":
        _exact_parameters(
            "quantity",
            "wholesale_case",
            q,
            required={"login_required": bool, "case_size": int, "minimum": int, "maximum": int},
        )
        if q["case_size"] < 1 or q["minimum"] < 1 or q["maximum"] < q["minimum"]:
            raise CommercePolicyError("invalid wholesale quantity bounds")
        if q["minimum"] % q["case_size"] or q["maximum"] % q["case_size"]:
            raise CommercePolicyError("wholesale bounds must be complete cases")
    if quantity["kind"] == "per_sku_limit":
        _exact_parameters(
            "quantity",
            "per_sku_limit",
            q,
            required={"limit": int, "guest_scope": str, "account_scope": str},
        )
        if q["limit"] < 1:
            raise CommercePolicyError("per-SKU quantity limit must be positive")
        if q["guest_scope"] != "device" or q["account_scope"] != "lifetime":
            raise CommercePolicyError("per-SKU scopes must be device/lifetime")
    if quantity["kind"] == "store_stock":
        _exact_parameters(
            "quantity", "store_stock", q, required={"minimum": int, "maximum": int}
        )
        if q["minimum"] < 1 or q["maximum"] < q["minimum"]:
            raise CommercePolicyError("invalid store quantity bounds")
    pricing = policies["pricing"]
    p = pricing["parameters"]
    if pricing["kind"] == "standard":
        _exact_parameters("pricing", "standard", p, required={"tax_basis_points": int})
    if pricing["kind"] == "tiered_line_discount":
        _exact_parameters(
            "pricing",
            "tiered_line_discount",
            p,
            required={"tax_basis_points": int, "tiers": list},
        )
        tiers = p.get("tiers")
        if not isinstance(tiers, list) or not tiers:
            raise CommercePolicyError("tiered pricing requires tiers")
        previous = 0
        for tier in tiers:
            if not isinstance(tier, dict):
                raise CommercePolicyError("each pricing tier must be a mapping")
            if set(tier) != {"minimum", "percent"}:
                raise CommercePolicyError("each pricing tier needs minimum and percent")
            if any(
                isinstance(tier[name], bool) or not isinstance(tier[name], int)
                for name in ("minimum", "percent")
            ):
                raise CommercePolicyError("pricing tier minimum and percent must be integers")
            if tier["minimum"] <= previous or not 0 <= tier["percent"] < 100:
                raise CommercePolicyError("pricing tiers must ascend and use percentages in [0,100)")
            previous = tier["minimum"]
    if not 0 <= p["tax_basis_points"] <= 10_000:
        raise CommercePolicyError("tax basis points must be between 0 and 10000")
    inventory = policies["inventory"]
    i = inventory["parameters"]
    if inventory["kind"] == "checkout_decrement":
        _exact_parameters(
            "inventory", "checkout_decrement", i, required={"atomic": bool}
        )
        if i["atomic"] is not True:
            raise CommercePolicyError("checkout decrement must be atomic")
    if inventory["kind"] == "reservation":
        _exact_parameters(
            "inventory",
            "reservation",
            i,
            required={
                "ttl_minutes": int,
                "repeated_action_extends_ttl": bool,
                "login_merge": str,
                "decline_preserves_active": bool,
            },
        )
        if i["ttl_minutes"] < 1:
            raise CommercePolicyError("reservation TTL must be positive")
        if i["repeated_action_extends_ttl"] or i["login_merge"] != "transfer" or not i["decline_preserves_active"]:
            raise CommercePolicyError("unsupported reservation semantics")
    if inventory["kind"] == "store_isolated":
        _exact_parameters(
            "inventory",
            "store_isolated",
            i,
            required={"atomic_with_slot_capacity": bool},
        )
        if i["atomic_with_slot_capacity"] is not True:
            raise CommercePolicyError("store inventory and slot capacity must be atomic")
    fulfillment = policies["fulfillment"]
    f = fulfillment["parameters"]
    if fulfillment["kind"] == "shipping":
        _exact_parameters(
            "fulfillment",
            "shipping",
            f,
            required={"standard_cents": int, "free_threshold_cents": int},
            optional={"threshold_basis": str},
        )
        if f["standard_cents"] < 0 or f["free_threshold_cents"] < 0:
            raise CommercePolicyError("shipping amounts cannot be negative")
        if f.get("threshold_basis", "discounted_subtotal") != "discounted_subtotal":
            raise CommercePolicyError("only discounted_subtotal shipping thresholds are supported")
    if fulfillment["kind"] == "pickup_slots":
        _exact_parameters(
            "fulfillment",
            "pickup_slots",
            f,
            required={
                "shipping_allowed": bool,
                "store_required": bool,
                "slot_required": bool,
                "slot_capacity": int,
            },
        )
        if f["slot_capacity"] < 1:
            raise CommercePolicyError("pickup slot capacity must be positive")
        if f["shipping_allowed"] or not f["store_required"] or not f["slot_required"]:
            raise CommercePolicyError("pickup variants require store/slot and forbid shipping")
    cancellation = policies["cancellation"]
    c = cancellation["parameters"]
    if cancellation["kind"] == "window":
        _exact_parameters("cancellation", "window", c, required={"minutes": int})
        if c["minutes"] < 0:
            raise CommercePolicyError("cancellation window cannot be negative")
    if cancellation["kind"] == "final_sale":
        _exact_parameters(
            "cancellation", "final_sale", c, required={"cancel_allowed": bool}
        )
        if c["cancel_allowed"]:
            raise CommercePolicyError("final-sale policy cannot allow cancellation")
    if cancellation["kind"] == "pickup_cutoff":
        _exact_parameters(
            "cancellation",
            "pickup_cutoff",
            c,
            required={
                "minimum_notice_minutes": int,
                "restore_inventory_once": bool,
                "restore_capacity_once": bool,
            },
        )
        if c["minimum_notice_minutes"] < 0 or not c["restore_inventory_once"] or not c["restore_capacity_once"]:
            raise CommercePolicyError("invalid pickup cancellation semantics")
    tokens = policies["token_lifetime"]["parameters"]
    _exact_parameters(
        "token_lifetime",
        "fixed",
        tokens,
        required={"verification_minutes": int, "reset_minutes": int},
    )
    if tokens["verification_minutes"] < 1 or tokens["reset_minutes"] < 1:
        raise CommercePolicyError("token lifetimes must be positive")


@dataclass
class Reservation:
    owner: str
    sku: str
    quantity: int
    created_at: datetime
    expires_at: datetime


@dataclass
class CommerceReference:
    """Small deterministic state model shared by policy/reference tests."""

    policies: Mapping[str, Any]
    now: datetime
    stock: dict[str, int]
    store_stock: dict[str, dict[str, int]] = field(default_factory=dict)
    slot_capacity: dict[str, int] = field(default_factory=dict)
    reservations: dict[tuple[str, str], Reservation] = field(default_factory=dict)
    lifetime_purchases: dict[tuple[str, str], int] = field(default_factory=dict)
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    idempotency: dict[tuple[str, str], str] = field(default_factory=dict)
    _order_sequence: int = 0
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        validate_policies(self.policies)
        self.now = _utc(self.now)

    def advance(self, seconds: int) -> None:
        if seconds < 0:
            raise CommercePolicyError("controlled clock cannot move backward")
        self.now += timedelta(seconds=seconds)
        self.release_expired()

    def token_expires_at(self, kind: str) -> datetime:
        key = {"verification": "verification_minutes", "reset": "reset_minutes"}.get(kind)
        if key is None:
            raise CommercePolicyError(f"unknown token kind: {kind}")
        return self.now + timedelta(minutes=int(self.policies["token_lifetime"]["parameters"][key]))

    def validate_quantity(self, *, owner: str, sku: str, quantity: int, authenticated: bool = True) -> None:
        policy = self.policies["quantity"]
        parameters = policy["parameters"]
        if quantity < 1:
            raise CommercePolicyError("quantity must be positive")
        if parameters.get("login_required") and not authenticated:
            raise CommercePolicyError("login required before adding to cart")
        if policy["kind"] == "standard_cap" and quantity > int(parameters["maximum"]):
            raise CommercePolicyError("quantity exceeds cart cap")
        if policy["kind"] == "wholesale_case":
            if not parameters["minimum"] <= quantity <= parameters["maximum"]:
                raise CommercePolicyError("quantity outside wholesale bounds")
            if quantity % parameters["case_size"]:
                raise CommercePolicyError("quantity must be a complete case")
        if policy["kind"] == "per_sku_limit":
            existing = self.lifetime_purchases.get((owner, sku), 0)
            if existing + quantity > int(parameters["limit"]):
                raise CommercePolicyError("lifetime per-SKU limit exceeded")
        if policy["kind"] == "store_stock":
            if not int(parameters.get("minimum", 1)) <= quantity <= int(parameters.get("maximum", quantity)):
                raise CommercePolicyError("quantity outside store purchase bounds")

    def line_total(self, unit_price_cents: int, quantity: int) -> tuple[int, int]:
        policy = self.policies["pricing"]
        percent = 0
        if policy["kind"] == "tiered_line_discount":
            for tier in policy["parameters"]["tiers"]:
                if quantity >= tier["minimum"]:
                    percent = tier["percent"]
        gross = unit_price_cents * quantity
        discount = _money(Decimal(gross) * Decimal(percent) / Decimal(100))
        return gross - discount, discount

    def totals(self, lines: list[tuple[int, int]]) -> dict[str, int]:
        subtotal = sum(self.line_total(price, quantity)[0] for price, quantity in lines)
        pricing = self.policies["pricing"]["parameters"]
        tax_basis_points = int(pricing.get("tax_basis_points", 0))
        tax = _money(Decimal(subtotal) * Decimal(tax_basis_points) / Decimal(10_000))
        fulfillment = self.policies["fulfillment"]
        shipping = 0
        if fulfillment["kind"] == "shipping":
            values = fulfillment["parameters"]
            threshold = int(values.get("free_threshold_cents", 0))
            shipping = 0 if threshold and subtotal >= threshold else int(values.get("standard_cents", 0))
        return {"subtotal_cents": subtotal, "tax_cents": tax, "shipping_cents": shipping, "total_cents": subtotal + tax + shipping}

    @_synchronized
    def reserve(self, owner: str, sku: str, quantity: int) -> Reservation:
        policy = self.policies["inventory"]
        if policy["kind"] != "reservation":
            raise CommercePolicyError("this variant does not reserve inventory on add")
        self.release_expired()
        key = (owner, sku)
        existing = self.reservations.get(key)
        if existing:
            # Repeated operations update quantity without extending the original TTL.
            delta = quantity - existing.quantity
            if delta > self.available_stock(sku):
                raise CommercePolicyError("insufficient available stock")
            existing.quantity = quantity
            return existing
        if quantity > self.available_stock(sku):
            raise CommercePolicyError("insufficient available stock")
        ttl = timedelta(minutes=int(policy["parameters"]["ttl_minutes"]))
        reservation = Reservation(owner, sku, quantity, self.now, self.now + ttl)
        self.reservations[key] = reservation
        return reservation

    @_synchronized
    def transfer_reservation(self, guest: str, account: str, sku: str) -> None:
        reservation = self.reservations.pop((guest, sku), None)
        if reservation is None:
            return
        existing = self.reservations.get((account, sku))
        if existing is not None:
            # Transfer ownership: never add quantities. Keep the earlier expiry.
            existing.quantity = max(existing.quantity, reservation.quantity)
            existing.expires_at = min(existing.expires_at, reservation.expires_at)
        else:
            reservation.owner = account
            self.reservations[(account, sku)] = reservation

    @_synchronized
    def release_expired(self) -> None:
        expired = [key for key, reservation in self.reservations.items() if reservation.expires_at <= self.now]
        for key in expired:
            del self.reservations[key]

    def available_stock(self, sku: str) -> int:
        reserved = sum(item.quantity for item in self.reservations.values() if item.sku == sku and item.expires_at > self.now)
        return self.stock.get(sku, 0) - reserved

    @_synchronized
    def checkout(
        self,
        *,
        owner: str,
        sku: str,
        quantity: int,
        idempotency_key: str,
        payment_ok: bool = True,
        store: str | None = None,
        slot: str | None = None,
        slot_starts_at: str | None = None,
    ) -> dict[str, Any]:
        prior = self.idempotency.get((owner, idempotency_key))
        if prior:
            return deepcopy(self.orders[prior])
        self.validate_quantity(owner=owner, sku=sku, quantity=quantity)
        if not payment_ok:
            # No stock/capacity effects; active reservations intentionally survive.
            raise CommercePolicyError("payment declined")
        fulfillment = self.policies["fulfillment"]
        if fulfillment["kind"] == "pickup_slots":
            if not store or not slot or not slot_starts_at:
                raise CommercePolicyError("store and pickup slot are required")
            if self.store_stock.get(store, {}).get(sku, 0) < quantity:
                raise CommercePolicyError("insufficient store inventory")
            if self.slot_capacity.get(slot, 0) < 1:
                raise CommercePolicyError("pickup slot is full")
            # Both resources are checked before either is decremented.
            self.store_stock[store][sku] -= quantity
            self.slot_capacity[slot] -= 1
        else:
            inventory = self.policies["inventory"]
            if inventory["kind"] == "reservation":
                reservation = self.reservations.get((owner, sku))
                if reservation is None or reservation.expires_at <= self.now or reservation.quantity < quantity:
                    raise CommercePolicyError("active reservation required")
                del self.reservations[(owner, sku)]
            if self.stock.get(sku, 0) < quantity:
                raise CommercePolicyError("insufficient inventory")
            self.stock[sku] -= quantity
        self._order_sequence += 1
        number = f"ORD-{self._order_sequence:06d}"
        order = {
            "number": number,
            "owner": owner,
            "sku": sku,
            "quantity": quantity,
            "status": "placed",
            "placed_at": self.now.isoformat().replace("+00:00", "Z"),
            "store": store,
            "slot": slot,
            "slot_starts_at": slot_starts_at,
            "resources_restored": False,
        }
        self.orders[number] = order
        self.idempotency[(owner, idempotency_key)] = number
        self.lifetime_purchases[(owner, sku)] = self.lifetime_purchases.get((owner, sku), 0) + quantity
        return deepcopy(order)

    def order_for(self, number: str, owner: str) -> dict[str, Any]:
        order = self.orders.get(number)
        if order is None or order["owner"] != owner:
            raise CommercePolicyError("order not found")
        return deepcopy(order)

    @_synchronized
    def cancel(self, number: str, owner: str) -> dict[str, Any]:
        order = self.orders.get(number)
        if order is None or order["owner"] != owner:
            raise CommercePolicyError("order not found")
        if order["status"] == "cancelled":
            return deepcopy(order)
        policy = self.policies["cancellation"]
        if policy["kind"] == "final_sale":
            raise CommercePolicyError("final sale orders cannot be cancelled")
        placed = _utc(order["placed_at"])
        if policy["kind"] == "window" and self.now > placed + timedelta(minutes=int(policy["parameters"]["minutes"])):
            raise CommercePolicyError("cancellation window closed")
        if policy["kind"] == "pickup_cutoff":
            starts = _utc(order["slot_starts_at"])
            if starts - self.now < timedelta(minutes=int(policy["parameters"]["minimum_notice_minutes"])):
                raise CommercePolicyError("pickup cancellation cutoff passed")
        order["status"] = "cancelled"
        if not order["resources_restored"]:
            if order["store"]:
                self.store_stock[order["store"]][order["sku"]] += order["quantity"]
                self.slot_capacity[order["slot"]] += 1
            else:
                self.stock[order["sku"]] = self.stock.get(order["sku"], 0) + order["quantity"]
            order["resources_restored"] = True
        return deepcopy(order)


def reference_profile_facts(
    spec: Mapping[str, Any],
    *,
    observed_policies: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce ten deterministic policy-profile Journey-Seed facts.

    This is the compiler/reference calibration layer: canonical policies are
    compared with the policy profile actually loaded by a reference runtime.
    It catches wiring or controlled-mutation defects before browser evaluation
    is trusted. Candidate scoring remains browser/fact based.
    """

    expected = spec["policies"]
    actual = observed_policies or expected
    validate_policies(actual)
    journey_modules = {
        "catalog_observability": ("quantity",),
        "account_lifecycle": ("token_lifetime",),
        "cart_inventory": ("quantity", "inventory"),
        "checkout_concurrency": ("pricing", "inventory", "fulfillment"),
        "orders_terminal": ("cancellation",),
    }
    journeys = []
    for journey_id, modules in journey_modules.items():
        for seed in (int(spec["seeds"]["public"]), int(spec["seeds"]["hidden"])):
            checkpoints = [
                {
                    "id": f"{module}-policy",
                    "passed": actual[module] == expected[module],
                    "expected": deepcopy(expected[module]),
                    "actual": deepcopy(actual[module]),
                    "evidence_ids": [],
                }
                for module in modules
            ]
            journeys.append(
                {
                    "id": journey_id,
                    "seed": seed,
                    "terminal_passed": all(item["passed"] for item in checkpoints),
                    "checkpoints": checkpoints,
                }
            )
    return {
        "schema_version": "websitebench.facts.v1",
        "visual": [],
        "interactions": [],
        "journeys": journeys,
        "robustness": [],
        "efficiency": {},
        "hard_failures": [],
        "failures": [],
        "evidence": [],
        "seeds": [],
        "versions": {"reference-profile": "v1", "protocol": "websitebench.facts.v1"},
    }
