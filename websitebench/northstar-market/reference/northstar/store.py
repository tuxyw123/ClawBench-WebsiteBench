"""Northstar commerce state transitions."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any

from .database import Database
from .security import (
    hash_password,
    new_token,
    normalize_email,
    password_error,
    token_digest,
    valid_email,
    verify_password,
)


@dataclass
class DomainError(Exception):
    code: str
    message: str
    status: int = 400
    fields: dict[str, str] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


def _product(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value["tags"] = json.loads(value.pop("tags_json"))
    value["image"] = json.loads(value.pop("image_json"))
    return value


class Store:
    def __init__(self, database: Database) -> None:
        self.db = database

    def categories(self) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            values = []
            for row in connection.execute("SELECT * FROM categories ORDER BY name"):
                item = dict(row)
                item["image"] = json.loads(item.pop("image_json"))
                values.append(item)
            return values

    def featured_products(self, limit: int = 12) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            return [
                _product(row)
                for row in connection.execute(
                    "SELECT * FROM products ORDER BY featured_rank LIMIT ?", (limit,)
                )
            ]

    def product_by_slug(self, slug: str) -> dict[str, Any] | None:
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM products WHERE slug = ?", (slug,)).fetchone()
            return _product(row) if row else None

    def product_by_id(self, product_id: str) -> dict[str, Any] | None:
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
            return _product(row) if row else None

    def image_by_key(self, key: str) -> dict[str, Any] | None:
        with self.db.connect() as connection:
            for row in connection.execute("SELECT title, image_json FROM products"):
                image = json.loads(row["image_json"])
                if image["key"] == key:
                    return {**image, "label": row["title"]}
            for row in connection.execute("SELECT name, image_json FROM categories"):
                image = json.loads(row["image_json"])
                if image["key"] == key:
                    return {**image, "label": row["name"]}
        return None

    def search(
        self,
        *,
        query: str = "",
        category: str = "",
        sort: str = "featured",
        page: int = 1,
    ) -> dict[str, Any]:
        query = query.strip()
        page = max(page, 1)
        conditions: list[str] = []
        params: list[Any] = []
        if query:
            conditions.append(
                """LOWER(p.title || ' ' || p.brand || ' ' || p.description || ' ' ||
                    c.name || ' ' || p.tags_json) LIKE ?"""
            )
            params.append(f"%{query.casefold()}%")
        if category:
            conditions.append("c.slug = ?")
            params.append(category)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        order = {
            "featured": "p.featured_rank ASC",
            "price-asc": "p.price_cents ASC, p.title ASC",
            "price-desc": "p.price_cents DESC, p.title ASC",
            "rating": "p.rating_basis_points DESC, p.review_count DESC, p.title ASC",
        }.get(sort, "p.featured_rank ASC")
        sort = sort if sort in {"featured", "price-asc", "price-desc", "rating"} else "featured"
        with self.db.connect() as connection:
            count = connection.execute(
                f"SELECT COUNT(*) FROM products p JOIN categories c ON c.id = p.category_id {where}",
                params,
            ).fetchone()[0]
            pages = max(1, (count + 11) // 12)
            page = min(page, pages)
            rows = connection.execute(
                f"""SELECT p.* FROM products p JOIN categories c ON c.id = p.category_id
                {where} ORDER BY {order} LIMIT 12 OFFSET ?""",
                (*params, (page - 1) * 12),
            )
            return {
                "products": [_product(row) for row in rows],
                "count": count,
                "page": page,
                "pages": pages,
                "query": query,
                "category": category,
                "sort": sort,
            }

    def user_for_session(self, raw_session: str | None) -> dict[str, Any] | None:
        if not raw_session:
            return None
        digest = token_digest(raw_session)
        with self.db.transaction(immediate=True) as connection:
            now = self.db.now(connection)
            row = connection.execute(
                """SELECT u.*, s.expires_at FROM sessions s
                JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?""",
                (digest,),
            ).fetchone()
            if row is None:
                return None
            if now > row["expires_at"]:
                connection.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))
                return None
            result = dict(row)
            result.pop("password_hash", None)
            return result

    def register(
        self,
        *,
        email: str,
        password: str,
        confirm_password: str,
        device_key: str,
    ) -> tuple[str, str] | None:
        fields: dict[str, str] = {}
        normalized = normalize_email(email)
        if not valid_email(email):
            fields["email"] = "Enter a valid email address."
        if error := password_error(password):
            fields["password"] = error
        if password != confirm_password:
            fields["confirm_password"] = "Passwords do not match."
        if fields:
            raise DomainError("invalid_registration", "Check the highlighted fields.", fields=fields)
        with self.db.transaction(immediate=True) as connection:
            now = self.db.now(connection)
            recent = connection.execute(
                """SELECT 1 FROM registration_attempts
                WHERE (email_normalized = ? OR device_key = ?) AND accepted_at > ? LIMIT 1""",
                (normalized, device_key, now - 300),
            ).fetchone()
            if recent:
                raise DomainError(
                    "registration_throttled",
                    "Please wait before trying to register again.",
                    status=429,
                )
            connection.execute(
                "INSERT INTO registration_attempts(email_normalized, device_key, accepted_at) VALUES (?, ?, ?)",
                (normalized, device_key, now),
            )
            existing = connection.execute(
                "SELECT * FROM users WHERE email_normalized = ?", (normalized,)
            ).fetchone()
            if existing and existing["verified"]:
                return None
            if existing:
                user_id = existing["id"]
                connection.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (hash_password(password), user_id),
                )
                connection.execute(
                    "UPDATE auth_tokens SET used_at = ? WHERE user_id = ? AND kind = 'verification' AND used_at IS NULL",
                    (now, user_id),
                )
            else:
                user_id = f"user_{uuid.uuid4().hex}"
                connection.execute(
                    """INSERT INTO users(
                        id, email, email_normalized, password_hash, verified, full_name, created_at
                    ) VALUES (?, ?, ?, ?, 0, '', ?)""",
                    (user_id, normalized, normalized, hash_password(password), now),
                )
            token = new_token()
            connection.execute(
                """INSERT INTO auth_tokens(kind, token_hash, user_id, issued_at, expires_at)
                VALUES ('verification', ?, ?, ?, ?)""",
                (token_digest(token), user_id, now, now + 1800),
            )
            return normalized, token

    def verify_email(self, token: str) -> str:
        digest = token_digest(token)
        with self.db.transaction(immediate=True) as connection:
            now = self.db.now(connection)
            row = connection.execute(
                "SELECT * FROM auth_tokens WHERE kind = 'verification' AND token_hash = ?",
                (digest,),
            ).fetchone()
            if row is None or row["used_at"] is not None or now > row["expires_at"]:
                raise DomainError(
                    "invalid_verification", "This verification link is invalid, expired, or already used."
                )
            connection.execute("UPDATE auth_tokens SET used_at = ? WHERE id = ?", (now, row["id"]))
            connection.execute("UPDATE users SET verified = 1 WHERE id = ?", (row["user_id"],))
            return row["user_id"]

    def login(self, *, email: str, password: str, device_key: str) -> tuple[str, dict[str, Any]]:
        normalized = normalize_email(email)
        with self.db.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE email_normalized = ?", (normalized,)
            ).fetchone()
            if row is None or not verify_password(row["password_hash"], password):
                raise DomainError("invalid_login", "Email or password is incorrect.", status=401)
            if not row["verified"]:
                raise DomainError(
                    "unverified_login", "Verify your email before signing in.", status=403
                )
            now = self.db.now(connection)
            raw_session = new_token()
            connection.execute(
                "INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token_digest(raw_session), row["id"], now, now + 86400),
            )
            self._merge_guest_cart(connection, device_key=device_key, user_id=row["id"])
            user = dict(row)
            user.pop("password_hash", None)
            return raw_session, user

    def logout(self, raw_session: str | None) -> None:
        if not raw_session:
            return
        with self.db.transaction(immediate=True) as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_digest(raw_session),))

    def forgot_password(self, email: str) -> tuple[str, str] | None:
        normalized = normalize_email(email)
        with self.db.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE email_normalized = ? AND verified = 1", (normalized,)
            ).fetchone()
            if row is None:
                return None
            now = self.db.now(connection)
            connection.execute(
                "UPDATE auth_tokens SET used_at = ? WHERE user_id = ? AND kind = 'reset' AND used_at IS NULL",
                (now, row["id"]),
            )
            token = new_token()
            connection.execute(
                """INSERT INTO auth_tokens(kind, token_hash, user_id, issued_at, expires_at)
                VALUES ('reset', ?, ?, ?, ?)""",
                (token_digest(token), row["id"], now, now + 3600),
            )
            return normalized, token

    def reset_password(self, *, token: str, password: str, confirm_password: str) -> None:
        fields: dict[str, str] = {}
        if error := password_error(password):
            fields["password"] = error
        if password != confirm_password:
            fields["confirm_password"] = "Passwords do not match."
        if fields:
            raise DomainError("invalid_password", "Check the highlighted fields.", fields=fields)
        with self.db.transaction(immediate=True) as connection:
            now = self.db.now(connection)
            row = connection.execute(
                "SELECT * FROM auth_tokens WHERE kind = 'reset' AND token_hash = ?",
                (token_digest(token),),
            ).fetchone()
            if row is None or row["used_at"] is not None or now > row["expires_at"]:
                raise DomainError(
                    "invalid_reset", "This reset link is invalid, expired, or already used."
                )
            connection.execute("UPDATE auth_tokens SET used_at = ? WHERE id = ?", (now, row["id"]))
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(password), row["user_id"]),
            )
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))

    def _cart_id(
        self,
        connection: sqlite3.Connection,
        *,
        owner_type: str,
        owner_key: str,
        create: bool = True,
    ) -> int | None:
        row = connection.execute(
            "SELECT id FROM carts WHERE owner_type = ? AND owner_key = ?",
            (owner_type, owner_key),
        ).fetchone()
        if row:
            return int(row["id"])
        if not create:
            return None
        cursor = connection.execute(
            "INSERT INTO carts(owner_type, owner_key) VALUES (?, ?)", (owner_type, owner_key)
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _owner(user: dict[str, Any] | None, device_key: str) -> tuple[str, str]:
        return ("account", user["id"]) if user else ("guest", device_key)

    def add_to_cart(
        self,
        *,
        product_id: str,
        quantity: int,
        user: dict[str, Any] | None,
        device_key: str,
    ) -> int:
        if quantity < 1:
            raise DomainError("invalid_quantity", "Choose a quantity of at least 1.")
        capped: tuple[int, int] | None = None
        with self.db.transaction(immediate=True) as connection:
            product = connection.execute(
                "SELECT inventory FROM products WHERE id = ?", (product_id,)
            ).fetchone()
            if product is None:
                raise DomainError("product_not_found", "Product not found.", status=404)
            cap = min(5, product["inventory"])
            if cap == 0:
                raise DomainError("out_of_stock", "This product is out of stock.", status=409)
            owner_type, owner_key = self._owner(user, device_key)
            cart_id = self._cart_id(
                connection, owner_type=owner_type, owner_key=owner_key, create=True
            )
            existing = connection.execute(
                "SELECT quantity FROM cart_lines WHERE cart_id = ? AND product_id = ?",
                (cart_id, product_id),
            ).fetchone()
            desired = quantity + (existing["quantity"] if existing else 0)
            final = min(desired, cap)
            connection.execute(
                """INSERT INTO cart_lines(cart_id, product_id, quantity) VALUES (?, ?, ?)
                ON CONFLICT(cart_id, product_id) DO UPDATE SET quantity = excluded.quantity""",
                (cart_id, product_id, final),
            )
            if final < desired:
                capped = (cap, final)
        if capped:
            raise DomainError(
                "quantity_capped",
                f"Cart quantity is limited to {capped[0]} for this product.",
                status=409,
                details={"quantity": capped[1]},
            )
        return final

    def update_cart(
        self,
        *,
        product_id: str,
        quantity: int,
        user: dict[str, Any] | None,
        device_key: str,
    ) -> int:
        capped: tuple[int, int] | None = None
        with self.db.transaction(immediate=True) as connection:
            owner_type, owner_key = self._owner(user, device_key)
            cart_id = self._cart_id(
                connection, owner_type=owner_type, owner_key=owner_key, create=False
            )
            if cart_id is None:
                return 0
            if quantity <= 0:
                connection.execute(
                    "DELETE FROM cart_lines WHERE cart_id = ? AND product_id = ?",
                    (cart_id, product_id),
                )
                return 0
            product = connection.execute(
                "SELECT inventory FROM products WHERE id = ?", (product_id,)
            ).fetchone()
            if product is None:
                raise DomainError("product_not_found", "Product not found.", status=404)
            cap = min(5, product["inventory"])
            if cap == 0:
                connection.execute(
                    "DELETE FROM cart_lines WHERE cart_id = ? AND product_id = ?",
                    (cart_id, product_id),
                )
                raise DomainError("out_of_stock", "This product is now out of stock.", status=409)
            final = min(quantity, cap)
            connection.execute(
                """INSERT INTO cart_lines(cart_id, product_id, quantity) VALUES (?, ?, ?)
                ON CONFLICT(cart_id, product_id) DO UPDATE SET quantity = excluded.quantity""",
                (cart_id, product_id, final),
            )
            if final < quantity:
                capped = (cap, final)
        if capped:
            raise DomainError(
                "quantity_capped",
                f"Cart quantity is limited to {capped[0]} for this product.",
                status=409,
                details={"quantity": capped[1]},
            )
        return final

    def cart(
        self, *, user: dict[str, Any] | None, device_key: str
    ) -> dict[str, Any]:
        with self.db.connect() as connection:
            owner_type, owner_key = self._owner(user, device_key)
            cart_id = self._cart_id(
                connection, owner_type=owner_type, owner_key=owner_key, create=False
            )
            if cart_id is None:
                return {"lines": [], "count": 0, "subtotal_cents": 0}
            rows = connection.execute(
                """SELECT p.*, cl.quantity FROM cart_lines cl
                JOIN products p ON p.id = cl.product_id WHERE cl.cart_id = ? ORDER BY p.title""",
                (cart_id,),
            )
            lines = []
            for row in rows:
                item = _product(row)
                item["line_total_cents"] = item["price_cents"] * item["quantity"]
                lines.append(item)
            return {
                "lines": lines,
                "count": sum(item["quantity"] for item in lines),
                "subtotal_cents": sum(item["line_total_cents"] for item in lines),
            }

    def _merge_guest_cart(
        self, connection: sqlite3.Connection, *, device_key: str, user_id: str
    ) -> None:
        guest_id = self._cart_id(
            connection, owner_type="guest", owner_key=device_key, create=False
        )
        if guest_id is None:
            return
        account_id = self._cart_id(
            connection, owner_type="account", owner_key=user_id, create=True
        )
        guest_lines = connection.execute(
            "SELECT product_id, quantity FROM cart_lines WHERE cart_id = ?", (guest_id,)
        ).fetchall()
        for line in guest_lines:
            existing = connection.execute(
                "SELECT quantity FROM cart_lines WHERE cart_id = ? AND product_id = ?",
                (account_id, line["product_id"]),
            ).fetchone()
            inventory = connection.execute(
                "SELECT inventory FROM products WHERE id = ?", (line["product_id"],)
            ).fetchone()[0]
            desired = line["quantity"] + (existing["quantity"] if existing else 0)
            final = min(desired, inventory, 5)
            if final > 0:
                connection.execute(
                    """INSERT INTO cart_lines(cart_id, product_id, quantity) VALUES (?, ?, ?)
                    ON CONFLICT(cart_id, product_id) DO UPDATE SET quantity = excluded.quantity""",
                    (account_id, line["product_id"], final),
                )
        connection.execute("DELETE FROM carts WHERE id = ?", (guest_id,))

    def checkout(
        self,
        *,
        user: dict[str, Any],
        idempotency_key: str,
        shipping_method: str,
        address: dict[str, str],
        card_number: str,
        expiration: str,
        cvv: str,
    ) -> dict[str, Any]:
        errors: dict[str, str] = {}
        if not idempotency_key or len(idempotency_key) > 128:
            errors["payment"] = "Checkout session is invalid. Refresh and try again."
        if shipping_method not in {"standard", "express"}:
            errors["shipping_method"] = "Choose a shipping method."
        for key, label in (
            ("full_name", "Full name"),
            ("line1", "Address line 1"),
            ("city", "City"),
        ):
            if not address.get(key, "").strip():
                errors[key] = f"{label} is required."
        address["state"] = address.get("state", "").strip().upper()
        if re.fullmatch(r"[A-Z]{2}", address["state"]) is None:
            errors["state"] = "Enter a two-letter US state."
        if re.fullmatch(r"[0-9]{5}", address.get("zip_code", "")) is None:
            errors["zip_code"] = "Enter a five-digit ZIP code."
        digits = re.sub(r"\D", "", card_number)
        if digits not in {"4242424242424242", "4000000000000002"}:
            errors["card_number"] = "Use a supported test card number."
        if re.fullmatch(r"[0-9]{3}", cvv) is None:
            errors["cvv"] = "Enter a three-digit CVV."
        match = re.fullmatch(r"(0[1-9]|1[0-2])/([0-9]{2})", expiration.strip())
        if not match:
            errors["expiration"] = "Enter expiration as MM/YY."
        else:
            with self.db.connect() as connection:
                now = self.db.now(connection)
            from datetime import datetime, timezone

            current = datetime.fromtimestamp(now, timezone.utc)
            month, year = int(match.group(1)), 2000 + int(match.group(2))
            if (year, month) < (current.year, current.month):
                errors["expiration"] = "The test card has expired."
        if errors:
            raise DomainError("invalid_checkout", "Check the highlighted fields.", fields=errors)
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "shipping_method": shipping_method,
                    "address": address,
                    "card_last_four": digits[-4:],
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        with self.db.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT request_hash, order_id FROM idempotency_records WHERE user_id = ? AND idempotency_key = ?",
                (user["id"], idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise DomainError(
                        "idempotency_conflict",
                        "This checkout request was already used with different details.",
                        status=409,
                    )
                return self._order(connection, existing["order_id"])
            cart_id = self._cart_id(
                connection, owner_type="account", owner_key=user["id"], create=False
            )
            if cart_id is None:
                raise DomainError("empty_cart", "Your cart is empty.", status=409)
            lines = connection.execute(
                """SELECT p.id, p.sku, p.title, p.price_cents, p.inventory, cl.quantity
                FROM cart_lines cl JOIN products p ON p.id = cl.product_id
                WHERE cl.cart_id = ? ORDER BY p.id""",
                (cart_id,),
            ).fetchall()
            if not lines:
                raise DomainError("empty_cart", "Your cart is empty.", status=409)
            shortages = [line["title"] for line in lines if line["quantity"] > line["inventory"]]
            if shortages:
                raise DomainError(
                    "insufficient_stock",
                    "Some cart items no longer have enough stock.",
                    status=409,
                    details={"products": shortages},
                )
            if digits == "4000000000000002":
                raise DomainError(
                    "payment_declined", "Your test payment was declined.", status=402
                )
            subtotal = sum(line["price_cents"] * line["quantity"] for line in lines)
            shipping = 1499 if shipping_method == "express" else (0 if subtotal >= 7500 else 599)
            tax = (subtotal * 825 + 5000) // 10000
            total = subtotal + shipping + tax
            now = self.db.now(connection)
            sequence = connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] + 1
            seed = self.db.meta(connection, "seed")
            order_id = f"order_{uuid.uuid4().hex}"
            order_number = f"NS-{seed}-{sequence:06d}"
            connection.execute(
                """INSERT INTO orders(
                    id, order_number, user_id, status, placed_at, cancelled_at,
                    subtotal_cents, shipping_cents, tax_cents, total_cents,
                    shipping_method, shipping_address_json, card_last_four
                ) VALUES (?, ?, ?, 'placed', ?, NULL, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_id,
                    order_number,
                    user["id"],
                    now,
                    subtotal,
                    shipping,
                    tax,
                    total,
                    shipping_method,
                    json.dumps(address, sort_keys=True),
                    digits[-4:],
                ),
            )
            for line in lines:
                updated = connection.execute(
                    "UPDATE products SET inventory = inventory - ? WHERE id = ? AND inventory >= ?",
                    (line["quantity"], line["id"], line["quantity"]),
                )
                if updated.rowcount != 1:
                    raise DomainError(
                        "insufficient_stock", "Some cart items no longer have enough stock.", status=409
                    )
                connection.execute(
                    "INSERT INTO order_lines VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        order_id,
                        line["id"],
                        line["sku"],
                        line["title"],
                        line["quantity"],
                        line["price_cents"],
                    ),
                )
            connection.execute(
                "INSERT INTO idempotency_records VALUES (?, ?, ?, ?)",
                (user["id"], idempotency_key, request_hash, order_id),
            )
            connection.execute("DELETE FROM cart_lines WHERE cart_id = ?", (cart_id,))
            return self._order(connection, order_id)

    def _order(self, connection: sqlite3.Connection, order_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row is None:
            raise DomainError("order_not_found", "Order not found.", status=404)
        order = dict(row)
        order["shipping_address"] = json.loads(order.pop("shipping_address_json"))
        order["lines"] = [
            dict(item)
            for item in connection.execute(
                "SELECT * FROM order_lines WHERE order_id = ? ORDER BY title", (order_id,)
            )
        ]
        return order

    def order_for_user(self, *, user_id: str, order_number: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT id FROM orders WHERE order_number = ? AND user_id = ?",
                (order_number, user_id),
            ).fetchone()
            if row is None:
                raise DomainError("order_not_found", "Order not found.", status=404)
            return self._order(connection, row["id"])

    def orders_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            ids = connection.execute(
                "SELECT id FROM orders WHERE user_id = ? ORDER BY placed_at DESC, order_number DESC",
                (user_id,),
            ).fetchall()
            return [self._order(connection, row["id"]) for row in ids]

    def cancel_order(self, *, user_id: str, order_number: str) -> dict[str, Any]:
        with self.db.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM orders WHERE order_number = ? AND user_id = ?",
                (order_number, user_id),
            ).fetchone()
            if row is None:
                raise DomainError("order_not_found", "Order not found.", status=404)
            if row["status"] == "cancelled":
                return self._order(connection, row["id"])
            now = self.db.now(connection)
            if now > row["placed_at"] + 1800:
                raise DomainError(
                    "cancellation_closed", "The cancellation window has closed.", status=409
                )
            lines = connection.execute(
                "SELECT product_id, quantity FROM order_lines WHERE order_id = ?", (row["id"],)
            ).fetchall()
            for line in lines:
                connection.execute(
                    "UPDATE products SET inventory = inventory + ? WHERE id = ?",
                    (line["quantity"], line["product_id"]),
                )
            connection.execute(
                "UPDATE orders SET status = 'cancelled', cancelled_at = ? WHERE id = ? AND status = 'placed'",
                (now, row["id"]),
            )
            return self._order(connection, row["id"])
